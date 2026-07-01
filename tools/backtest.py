#!/usr/bin/env python3
"""whicc 回测框架 V6：读取 JSONL 事件日志 + 字幕 ground truth → 输出 CSV 评估

用法:
    python3 backtest.py --events /tmp/whicc/events.jsonl \
                        --subs   '/path/to/subtitle.txt' \
                        --video-minutes 47 \
                        --out    /tmp/whicc/metrics.csv \
                        [--run-id v6_001] [--stage 5min] \
                        [--param min_chunk_sec=2.0 max_chunk_sec=4.5 ...]
"""
import json
import csv
import sys
import re
import argparse


def load_final_events(path: str) -> list[dict]:
    finals = []
    all_events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            all_events.append(ev)
            if ev.get("event_type") == "final" and ev.get("accepted", True):
                finals.append(ev)
    return finals, all_events


def parse_subtitles(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# Disfluency patterns (case-insensitive, word-boundary)
DISFLUENCY_PATTERNS = [
    r'\buh+\b', r'\bum+\b', r'\bumm+\b', r'\buhh+\b',
    r'\byou know\b', r'\blike\b(?=,\s|\s(?:I|he|she|it|we|they|the|a|an|that|this|so|and|but|or))',
    r'\bI mean\b', r'\bbasically\b', r'\bactually\b',
    r'\bso\b(?=,\s)', r'\bwell\b(?=,\s)',
    r'\bkind of\b', r'\bsort of\b',
    r'\berr+\b', r'\ber+\b',
]
_DISFLUENCY_RE = re.compile('|'.join(DISFLUENCY_PATTERNS), re.IGNORECASE)


def normalize_disfluency(text: str) -> str:
    """Remove common speech disfluencies from text (for WER normalization)"""
    text = _DISFLUENCY_RE.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def word_error_rate(ref: str, hyp: str) -> float:
    ref_words = ref.split()
    hyp_words = hyp.split()
    n, m = len(ref_words), len(hyp_words)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])
    return dp[n][m] / max(n, 1)


def char_error_rate(ref: str, hyp: str) -> float:
    ref_chars = list(ref.replace(' ', ''))
    hyp_chars = list(hyp.replace(' ', ''))
    n, m = len(ref_chars), len(hyp_chars)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref_chars[i - 1] == hyp_chars[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])
    return dp[n][m] / max(n, 1)


def sliding_align(text: str, sub_lines: list[str]) -> tuple[int, float]:
    """找到 text 在 sub_lines 中的最佳对齐位置"""
    text_norm = normalize_text(text)
    if not text_norm:
        return 0, 0.0
    sub_full = ' '.join(normalize_text(l) for l in sub_lines)
    window = min(500, len(text_norm))
    text_win = text_norm[:window]
    best_pos = 0
    best_overlap = 0.0
    text_words = set(text_win.split())
    if not text_words:
        return 0, 0.0
    for pos in range(0, max(1, len(sub_full) - window), 30):
        sub_win = sub_full[pos:pos + window]
        sub_words = set(sub_win.split())
        if sub_words:
            overlap = len(text_words & sub_words) / max(len(text_words), len(sub_words))
            if overlap > best_overlap:
                best_overlap = overlap
                best_pos = pos
    # 转为行号
    char_count = 0
    start_line = 0
    for i, sl in enumerate(sub_lines):
        if char_count >= best_pos:
            start_line = i
            break
        char_count += len(normalize_text(sl)) + 1
    return start_line, best_overlap


def compute_windows(finals: list[dict], sub_lines: list[str],
                    video_minutes: float, window_min: int = 5) -> list[dict]:
    """计算每个 window_min 分钟窗口的指标"""
    if not finals:
        return []
    video_sec = video_minutes * 60
    sub_per_sec = len(sub_lines) / video_sec
    n_windows = int(video_minutes) // window_min

    rows = []
    for wi in range(n_windows):
        t_start = wi * window_min * 60
        t_end = (wi + 1) * window_min * 60

        win_finals = [e for e in finals if t_start <= e.get("audio_start_sec", 0) < t_end]
        if not win_finals:
            continue

        texts = [e.get("text", "") for e in win_finals]
        win_text = ' '.join(texts)

        # 字幕范围
        sub_i = int(t_start * sub_per_sec)
        sub_j = int(t_end * sub_per_sec)
        sub_text = ' '.join(sub_lines[sub_i:sub_j])

        # WER / CER (raw and normalized)
        ref_norm = normalize_text(sub_text)
        hyp_norm = normalize_text(win_text)
        w_wer = word_error_rate(ref_norm, hyp_norm)
        w_cer = char_error_rate(ref_norm, hyp_norm)
        # Normalized WER: remove disfluencies from both ref and hyp
        ref_dnorm = normalize_disfluency(ref_norm)
        hyp_dnorm = normalize_disfluency(hyp_norm)
        w_wer_norm = word_error_rate(ref_dnorm, hyp_dnorm) if ref_dnorm else w_wer

        # 延迟
        latencies = [e.get("relative_confirm_latency_sec", 0) for e in win_finals]
        latencies = [l for l in latencies if l > 0]
        median_lat = sorted(latencies)[len(latencies) // 2] if latencies else -1.0

        # 漂移：累积文本 vs 字幕
        acc_text = ' '.join(e.get("text", "") for e in finals
                           if e.get("audio_start_sec", 0) < t_end)
        acc_sub_i = 0
        acc_sub_j = int(t_end * sub_per_sec)
        acc_sub_text = ' '.join(sub_lines[acc_sub_i:acc_sub_j])
        drift_wer = word_error_rate(normalize_text(acc_sub_text), normalize_text(acc_text))
        acc_sub_chars = len(normalize_text(acc_sub_text))
        drift_chars = int(drift_wer * max(acc_sub_chars, 1))

        rows.append({
            "segment_window": f"{wi * window_min}-{(wi + 1) * window_min}min",
            "chunk_wer": round(w_wer, 4),
            "chunk_wer_normalized": round(w_wer_norm, 4),
            "chunk_cer": round(w_cer, 4),
            "median_confirm_latency_sec": round(median_lat, 3),
            "drift_chars": drift_chars,
            "drift_wer": round(drift_wer, 4),
            "final_lines": len(win_finals),
            "covered_audio_sec": round(sum(e.get("chunk_sec", 0) for e in win_finals), 1),
        })

    return rows


def compute_run_metrics(finals: list[dict], all_events: list[dict],
                        sub_lines: list[str], video_minutes: float) -> dict:
    """计算 run 级聚合指标"""
    video_sec = video_minutes * 60
    if not finals:
        return {
            "adjacent_dup_rate": 0.0,
            "reject_rate": 1.0,
            "total_submits": 0,
            "total_rejects": 0,
            "covered_audio_sec": 0.0,
        }

    # 相邻重复率
    dup_count = 0
    for i in range(1, len(finals)):
        w1 = set(normalize_text(finals[i - 1].get("text", "")).split())
        w2 = set(normalize_text(finals[i].get("text", "")).split())
        if w1 and w2 and len(w1 & w2) / max(len(w1), len(w2)) > 0.7:
            dup_count += 1
    adj_dup = dup_count / max(len(finals) - 1, 1)

    # Reject rate
    submits = [e for e in all_events if e.get("event_type") in ("final", "partial", "reject")]
    rejects = [e for e in all_events if e.get("event_type") == "reject"]
    reject_rate = len(rejects) / max(len(submits), 1)

    covered = sum(e.get("chunk_sec", 0) for e in finals)

    return {
        "adjacent_dup_rate": round(adj_dup, 4),
        "reject_rate": round(reject_rate, 4),
        "total_submits": len(submits),
        "total_rejects": len(rejects),
        "covered_audio_sec": round(covered, 1),
    }


def rank_results(rows: list[dict], stage: str = "15min") -> list[tuple[str, float, dict]]:
    """按 V6.1 排序规则排名"""
    grouped = {}
    for r in rows:
        key = (r["min_chunk_sec"], r["max_chunk_sec"], r["overlap_sec"],
               r["silence_threshold"], r["silence_submit_sec"],
               r["no_speech_threshold"], r["temperature"],
               r["prompt_mode"], r["prompt_tail_chars"])
        grouped.setdefault(key, []).append(r)

    ranked = []
    for key, group in grouped.items():
        stage_rows = [r for r in group if r.get("stage") == stage]
        if not stage_rows:
            continue
        avg_wer = sum(r["chunk_wer"] for r in stage_rows) / len(stage_rows)
        last_drift = stage_rows[-1]["drift_chars"]
        median_lat = sum(r["median_confirm_latency_sec"] for r in stage_rows) / len(stage_rows)
        adj_dup = stage_rows[0]["adjacent_dup_rate"]
        rej_rate = stage_rows[0]["reject_rate"]

        # 淘汰规则
        if adj_dup > 0.20:
            continue
        if len(stage_rows) >= 3 and stage_rows[-1]["chunk_wer"] > 0.80:
            continue

        score = (avg_wer, last_drift, median_lat, adj_dup, rej_rate)
        ranked.append((key, score, {
            "avg_wer": round(avg_wer, 4),
            "last_drift_chars": last_drift,
            "median_latency": round(median_lat, 3),
            "adj_dup": adj_dup,
            "reject_rate": rej_rate,
        }))

    ranked.sort(key=lambda x: x[1])
    return ranked


def main():
    parser = argparse.ArgumentParser(description="whicc V6 回测")
    parser.add_argument("--events", required=True, help="JSONL 事件日志")
    parser.add_argument("--subs", required=True, help="字幕文件")
    parser.add_argument("--video-minutes", type=float, default=47)
    parser.add_argument("--out", required=True, help="输出 CSV 路径")
    # run 参数（由 sweep.py 传入）
    parser.add_argument("--run-id", default="unknown")
    parser.add_argument("--stage", default="5min", choices=["5min", "15min"])
    parser.add_argument("--min-chunk-sec", type=float, default=0)
    parser.add_argument("--max-chunk-sec", type=float, default=0)
    parser.add_argument("--overlap-sec", type=float, default=0)
    parser.add_argument("--silence-threshold", type=float, default=0)
    parser.add_argument("--silence-submit-sec", type=float, default=0)
    parser.add_argument("--no-speech-threshold", type=float, default=0)
    parser.add_argument("--temperature", default="")
    parser.add_argument("--prompt-mode", default="")
    parser.add_argument("--prompt-tail-chars", type=int, default=0)
    args = parser.parse_args()

    finals, all_events = load_final_events(args.events)
    sub_lines = parse_subtitles(args.subs)

    print(f"[backtest] {args.run_id} ({args.stage}): {len(finals)} final events", file=sys.stderr)

    windows = compute_windows(finals, sub_lines, args.video_minutes)
    run_metrics = compute_run_metrics(finals, all_events, sub_lines, args.video_minutes)

    # 组装输出行
    rows = []
    for w in windows:
        row = {
            "run_id": args.run_id,
            "stage": args.stage,
            "min_chunk_sec": args.min_chunk_sec,
            "max_chunk_sec": args.max_chunk_sec,
            "overlap_sec": args.overlap_sec,
            "silence_threshold": args.silence_threshold,
            "silence_submit_sec": args.silence_submit_sec,
            "no_speech_threshold": args.no_speech_threshold,
            "temperature": args.temperature,
            "prompt_mode": args.prompt_mode,
            "prompt_tail_chars": args.prompt_tail_chars,
        }
        row.update(w)
        row.update({
            "adjacent_dup_rate": run_metrics["adjacent_dup_rate"],
            "reject_rate": run_metrics["reject_rate"],
            "total_submits": run_metrics["total_submits"],
            "total_rejects": run_metrics["total_rejects"],
        })
        rows.append(row)

    # 写 CSV
    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    fieldnames = [
        "run_id", "stage", "min_chunk_sec", "max_chunk_sec", "overlap_sec",
        "silence_threshold", "silence_submit_sec", "no_speech_threshold",
        "temperature", "prompt_mode", "prompt_tail_chars",
        "segment_window", "chunk_wer", "chunk_wer_normalized", "chunk_cer",
        "median_confirm_latency_sec", "drift_chars", "drift_wer",
        "final_lines", "covered_audio_sec",
        "adjacent_dup_rate", "reject_rate", "total_submits", "total_rejects",
    ]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"[backtest] wrote {len(rows)} rows to {args.out}", file=sys.stderr)

    # 排行（仅在调用方显式请求时）
    if "--rank" in sys.argv:
        ranked = rank_results(rows, stage=args.stage)
        print(f"\n=== 排行 ({args.stage}) ===")
        for i, (key, score, metrics) in enumerate(ranked[:10], 1):
            params = f"chunk=({key[0]},{key[1]}) ov={key[2]} sil={key[3]} sub={key[4]}"
            print(f"  {i}. WER={metrics['avg_wer']:.3f} drift={metrics['last_drift_chars']}"
                  f" lat={metrics['median_latency']:.1f}s dup={metrics['adj_dup']:.2f}"
                  f" rej={metrics['reject_rate']:.2f}  | {params}")


if __name__ == "__main__":
    main()
