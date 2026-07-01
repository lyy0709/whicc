#!/usr/bin/env python3
"""whicc V6 自动化扫参脚本

用法:
    # Phase 1: 18 组 × 5 分钟
    python3 sweep.py --subs '/path/to/subtitle.txt' --phase 1 --duration 300

    # Phase 1 复赛: 前 5 名 × 15 分钟
    python3 sweep.py --subs '/path/to/subtitle.txt' --phase 1b --duration 900 --top-n 5

    # Phase 2: prompt 验证 (在 top-3 配置上跑 3 种 prompt)
    python3 sweep.py --subs '/path/to/subtitle.txt' --phase 2 --duration 900

    # Phase 3: temperature 验证
    python3 sweep.py --subs '/path/to/subtitle.txt' --phase 3 --duration 900
"""
import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict

WHICC_PY = "/tmp/whicc/src/whicc.py"
BACKTEST_PY = "/tmp/whicc/tools/backtest.py"
FILE_AUDIO_PY = "/tmp/whicc/tools/whicc_file_audio.py"
SEG_DIR = "/tmp/whicc-seg"
RUNS_DIR = "/tmp/whicc/runs"
VIDEO_MINUTES = 47

# 第 1 阶段固定参数
PHASE1_FIXED = {
    "temperature": "0.0",
    "no_speech_threshold": 0.45,
    "silence_threshold": 0.008,
    "prompt_mode": "fixed",
    "prompt_tail_chars": 0,
}

# 第 1 阶段网格
PHASE1_GRID = [
    # (MIN_CHUNK_SEC, MAX_CHUNK_SEC, OVERLAP_SEC, SILENCE_SUBMIT_SEC)
    (2.0, 3.5, 0.3, 0.25),
    (2.0, 3.5, 0.3, 0.40),
    (2.0, 3.5, 0.5, 0.25),
    (2.0, 3.5, 0.5, 0.40),
    (2.0, 3.5, 0.7, 0.40),
    (2.0, 4.5, 0.3, 0.25),
    (2.0, 4.5, 0.3, 0.40),
    (2.0, 4.5, 0.5, 0.25),
    (2.0, 4.5, 0.5, 0.40),
    (2.0, 4.5, 0.7, 0.40),
    (2.0, 5.5, 0.3, 0.25),
    (2.0, 5.5, 0.3, 0.40),
    (2.0, 5.5, 0.5, 0.25),
    (2.0, 5.5, 0.5, 0.40),
    (2.0, 5.5, 0.7, 0.40),
    (2.5, 4.5, 0.5, 0.25),
    (2.5, 4.5, 0.5, 0.40),
    (2.5, 5.5, 0.5, 0.40),
]

# Prompt 策略（Phase 2）
PROMPT_STRATEGIES = [
    ("fixed", 0),
    ("tail", 80),
    ("tail", 160),
]

# Temperature 策略（Phase 3）
TEMP_STRATEGIES = ["0.0", "0.0,0.2"]


READY_SIGNAL = "模型就绪"
MODEL_LOAD_TIMEOUT = 90  # 秒


def wait_for_ready(proc, timeout: int = MODEL_LOAD_TIMEOUT) -> bool:
    """阻塞读取 stdout 直到出现就绪信号或超时。返回 True=就绪，False=超时/进程退出
    就绪信号 "模型就绪" 由 whicc.py 打印到 stdout（非 stderr）。
    """
    result = [None]

    def _reader():
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode(errors="replace").rstrip()
                if READY_SIGNAL in line:
                    result[0] = True
                    return
        except (ValueError, OSError):
            pass  # stdout 已关闭

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False
    return result[0] is not None


def make_run_id(stage: str, idx: int) -> str:
    return f"v6_{stage}_{idx:03d}"


def build_whicc_args(run_id: str, params: dict, file_audio: str = None) -> list[str]:
    """构建 whicc.py 命令行参数"""
    run_dir = os.path.join(RUNS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)
    cmd = [
        sys.executable, WHICC_PY,
        "--run-id", run_id,
        "--min-chunk-sec", str(params["min_chunk_sec"]),
        "--max-chunk-sec", str(params["max_chunk_sec"]),
        "--overlap-sec", str(params["overlap_sec"]),
        "--silence-threshold", str(params["silence_threshold"]),
        "--silence-submit-sec", str(params["silence_submit_sec"]),
        "--no-speech-threshold", str(params["no_speech_threshold"]),
        "--temperature", str(params["temperature"]),
        "--prompt-mode", params["prompt_mode"],
        "--prompt-tail-chars", str(params["prompt_tail_chars"]),
        "--events-jsonl", os.path.join(run_dir, "events.jsonl"),
        "--output-text", os.path.join(run_dir, "final.txt"),
        "--stats",
    ]
    if file_audio:
        cmd += ["--audio-bin", f"{sys.executable} {FILE_AUDIO_PY} {file_audio}"]
    return cmd


def build_backtest_args(run_id: str, params: dict, stage: str,
                        duration: int, subs: str) -> list[str]:
    """构建 backtest.py 命令行参数"""
    run_dir = os.path.join(RUNS_DIR, run_id)
    # backtest.py 只接受 5min/15min，其它 stage 名按实际 duration 映射
    bt_stage = stage if stage in ("5min", "15min") else ("15min" if duration >= 900 else "5min")
    return [
        sys.executable, BACKTEST_PY,
        "--events", os.path.join(run_dir, "events.jsonl"),
        "--subs", subs,
        "--video-minutes", str(VIDEO_MINUTES),
        "--out", os.path.join(run_dir, "metrics.csv"),
        "--run-id", run_id,
        "--stage", bt_stage,
        "--min-chunk-sec", str(params["min_chunk_sec"]),
        "--max-chunk-sec", str(params["max_chunk_sec"]),
        "--overlap-sec", str(params["overlap_sec"]),
        "--silence-threshold", str(params["silence_threshold"]),
        "--silence-submit-sec", str(params["silence_submit_sec"]),
        "--no-speech-threshold", str(params["no_speech_threshold"]),
        "--temperature", str(params["temperature"]),
        "--prompt-mode", params["prompt_mode"],
        "--prompt-tail-chars", str(params["prompt_tail_chars"]),
    ]


def clean_seg_dir():
    """清理段文件目录"""
    if os.path.isdir(SEG_DIR):
        for f in os.listdir(SEG_DIR):
            if f.endswith(".pcm"):
                try:
                    os.unlink(os.path.join(SEG_DIR, f))
                except OSError:
                    pass


def stop_proc(proc):
    """优雅停止进程"""
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def wait_for_user(no_wait: bool, idx: int, total: int):
    """等待用户重启视频"""
    if idx >= total - 1:
        return
    if no_wait:
        print(f"\n  >>> 10 秒后自动开始下一组（请重启视频）...")
        time.sleep(10)
    else:
        print(f"\n  >>> 请重启视频，然后按 Enter 继续（或 Ctrl+C 结束并保存已有结果）...")
        try:
            input()
        except EOFError:
            pass


def run_one(run_id: str, params: dict, duration: int, stage: str, idx: int, total: int,
            subs: str, file_audio: str = None) -> dict | None:
    """运行一组参数，返回 backtest 结果行或 None"""
    clean_seg_dir()
    tag = f"[{idx + 1}/{total}]"

    param_desc = (f"chunk=({params['min_chunk_sec']},{params['max_chunk_sec']}) "
                  f"ov={params['overlap_sec']} sil={params['silence_threshold']} "
                  f"sub={params['silence_submit_sec']} t={params['temperature']} "
                  f"prompt={params['prompt_mode']}")
    print(f"\n{tag} {run_id}: {param_desc}")

    cmd = build_whicc_args(run_id, params, file_audio=file_audio)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        print(f"  FAILED to start: {e}")
        return None

    # 等待模型加载完成（读取就绪信号，最多 MODEL_LOAD_TIMEOUT 秒）
    print("  等待模型加载...", end="", flush=True)
    if not wait_for_ready(proc):
        stop_proc(proc)
        print(" 超时，跳过")
        return None
    print(" 就绪")

    # 运行指定时长
    print(f"  录制中 ({duration}s)...", end="", flush=True)
    try:
        proc.wait(timeout=duration)
        # whicc.py 在 timeout 前自己退出了
        print(" (提前退出)", flush=True)
    except subprocess.TimeoutExpired:
        stop_proc(proc)
        print(" done", flush=True)
    except KeyboardInterrupt:
        stop_proc(proc)
        print(" (用户中断)", flush=True)
        raise

    # 清理段文件
    clean_seg_dir()

    # 运行 backtest
    bt_cmd = build_backtest_args(run_id, params, stage, duration, subs)
    try:
        result = subprocess.run(bt_cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"  backtest 失败: {result.stderr[:200]}")
            return None
    except Exception as e:
        print(f"  backtest 异常: {e}")
        return None

    # 读取 CSV 结果
    csv_path = os.path.join(RUNS_DIR, run_id, "metrics.csv")
    if not os.path.exists(csv_path):
        print(f"  CSV 未生成: {csv_path}")
        return None

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        print(f"  CSV 为空")
        return None

    # 聚合窗口指标（排除无效窗口：covered_audio_sec < 60s）
    valid_rows = [r for r in rows if float(r.get("covered_audio_sec", 0)) >= 60]
    if not valid_rows:
        valid_rows = rows  # fallback: 保留全部

    wers = [float(r["chunk_wer"]) for r in valid_rows if float(r["chunk_wer"]) >= 0]
    avg_wer = sum(wers) / len(wers) if wers else 1.0
    wers_norm = [float(r.get("chunk_wer_normalized", r["chunk_wer"])) for r in valid_rows
                 if float(r.get("chunk_wer_normalized", r["chunk_wer"])) >= 0]
    avg_wer_normalized = sum(wers_norm) / len(wers_norm) if wers_norm else avg_wer
    last_drift = int(valid_rows[-1]["drift_chars"]) if valid_rows else 0
    last_wer = float(valid_rows[-1]["chunk_wer"]) if valid_rows else 1.0
    adj_dup = float(rows[0]["adjacent_dup_rate"]) if rows else 1.0
    rej_rate = float(rows[0]["reject_rate"]) if rows else 1.0
    n_finals = sum(int(r["final_lines"]) for r in valid_rows)
    covered = sum(float(r.get("covered_audio_sec", 0)) for r in valid_rows)

    print(f"  → WER={avg_wer:.3f} norm_WER={avg_wer_normalized:.3f} drift={last_drift} "
          f"dup={adj_dup:.2f} rej={rej_rate:.2f} finals={n_finals}")

    return {
        "run_id": run_id,
        "stage": stage,
        "params": params,
        "avg_wer": avg_wer,
        "avg_wer_normalized": avg_wer_normalized,
        "last_wer": last_wer,
        "last_drift": last_drift,
        "adj_dup": adj_dup,
        "reject_rate": rej_rate,
        "n_finals": n_finals,
        "n_windows": len(valid_rows),
        "covered_audio_sec": covered,
    }


def rank_and_select(results: list[dict], top_n: int, min_finals: int = 10) -> list[dict]:
    """按 V6.2 排序规则选出 top_n"""
    valid = [r for r in results if r["n_finals"] >= min_finals]
    if len(valid) < top_n:
        valid = [r for r in results if r["n_finals"] >= 3]

    # 淘汰规则
    filtered = []
    for r in valid:
        if r["adj_dup"] > 0.20:
            print(f"  淘汰 {r['run_id']}: adj_dup={r['adj_dup']:.2f} > 0.20")
            continue
        if r["last_wer"] > 0.80:
            print(f"  淘汰 {r['run_id']}: last_wer={r['last_wer']:.3f} > 0.80")
            continue
        filtered.append(r)

    # V6.2 排序: avg_wer → last_wer → last_drift → covered → adj_dup → reject_rate
    filtered.sort(key=lambda r: (
        r["avg_wer"],
        r.get("last_wer", r["avg_wer"]),
        r["last_drift"],
        -r.get("covered_audio_sec", 0),
        r["adj_dup"],
        r["reject_rate"],
    ))

    selected = filtered[:top_n]
    print(f"\n=== TOP {len(selected)} ===")
    for i, r in enumerate(selected, 1):
        print(f"  {i}. {r['run_id']}: WER={r['avg_wer']:.3f} "
              f"norm={r.get('avg_wer_normalized',0):.3f} "
              f"last_WER={r.get('last_wer',0):.3f} drift={r['last_drift']} "
              f"dup={r['adj_dup']:.2f} finals={r['n_finals']}")
    return selected


def write_leaderboard(path: str, all_results: list[dict]):
    """写 leaderboard CSV"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fieldnames = ["run_id", "stage", "min_chunk_sec", "max_chunk_sec", "overlap_sec",
                  "silence_threshold", "silence_submit_sec", "no_speech_threshold",
                  "temperature", "prompt_mode", "prompt_tail_chars",
                  "avg_wer", "avg_wer_normalized", "last_wer", "last_drift", "adj_dup", "reject_rate",
                  "n_finals", "n_windows", "covered_audio_sec"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in all_results:
            row = {"run_id": r["run_id"], "stage": r["stage"]}
            row.update(r.get("params", {}))
            row.update({k: r[k] for k in ["avg_wer", "avg_wer_normalized", "last_wer", "last_drift",
                                           "adj_dup", "reject_rate", "n_finals", "n_windows",
                                           "covered_audio_sec"]})
            writer.writerow(row)
    print(f"\n写入 {path} ({len(all_results)} 行)")


# --------------- Checkpoint（断点续跑）---------------
def checkpoint_path(leaderboard_path: str) -> str:
    """checkpoint 文件与 leaderboard 同目录，后缀 .checkpoint.jsonl"""
    return leaderboard_path.rsplit(".", 1)[0] + ".checkpoint.jsonl"


def checkpoint_append(ckpt_file: str, result: dict):
    """每完成一个 run 立即追加到 checkpoint"""
    os.makedirs(os.path.dirname(ckpt_file) or ".", exist_ok=True)
    with open(ckpt_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")


def checkpoint_load(ckpt_file: str) -> list[dict]:
    """加载已有 checkpoint"""
    if not os.path.exists(ckpt_file):
        return []
    results = []
    with open(ckpt_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def checkpoint_done_ids(ckpt_file: str) -> set:
    """返回已完成的 run_id 集合"""
    return {r["run_id"] for r in checkpoint_load(ckpt_file)}


# --------------- 全局参数（在 main 中赋值）---------------
args = None


def main():
    global args
    parser = argparse.ArgumentParser(description="whicc V6 自动化扫参")
    parser.add_argument("--subs", required=True, help="字幕文件路径")
    parser.add_argument("--phase", required=True,
                        choices=["1", "1b", "2", "2r", "3", "regress", "tail", "vad"],
                        help="1=初筛, 1b=复赛, 2=prompt, 2r=稳健性, 3=temperature, regress=回归验证, tail=tail长度扫描, vad=VAD阈值扫描")
    parser.add_argument("--duration", type=int, default=300, help="每组录制秒数")
    parser.add_argument("--top-n", type=int, default=5, help="1b 阶段选前 N 名")
    parser.add_argument("--leaderboard", default=os.path.join(RUNS_DIR, "leaderboard.csv"))
    # 1b: 接收 top 配置 JSON
    parser.add_argument("--top-configs", metavar="FILE", help="top 配置 JSON 文件 (1b 阶段)")
    # 2: 接收 base 配置
    parser.add_argument("--base-configs", metavar="FILE", help="base 配置 JSON 文件 (prompt 阶段)")
    # 3: 接收最优配置
    parser.add_argument("--best-config", metavar="FILE", help="最优配置 JSON 文件 (temperature 阶段)")
    parser.add_argument("--no-wait", action="store_true", help="不等待用户按 Enter，自动连续运行")
    parser.add_argument("--file-audio", metavar="PCM", help="用 PCM 文件代替实时音频（全自动模式）")
    parser.add_argument("--repeats", type=int, default=3, help="2r 阶段每组重复次数 (default 3)")
    parser.add_argument("--shadow", action="store_true", help="2r 阶段包含 shadow control (v6_1b_003)")
    parser.add_argument("--regress-baseline", type=float, default=0.155,
                        help="regress 阶段 WER 上限阈值，超过则 FAIL (default 0.155)")
    parser.add_argument("--tail-lengths", type=str, default="80,160,320,480",
                        help="tail 阶段要测试的 prompt 长度列表，逗号分隔 (default 80,160,320,480)")
    parser.add_argument("--silence-thresholds", type=str, default="0.005,0.008,0.012",
                        help="vad 阶段 silence_threshold 列表 (default 0.005,0.008,0.012)")
    parser.add_argument("--no-speech-thresholds", type=str, default="0.30,0.45,0.55",
                        help="vad 阶段 no_speech_threshold 列表 (default 0.30,0.45,0.55)")
    args = parser.parse_args()

    os.makedirs(RUNS_DIR, exist_ok=True)
    all_results = []

    # 注册信号处理
    def sigint_handler(sig, frame):
        print("\n\n中断，正在写入已收集的结果...")
        write_leaderboard(args.leaderboard, all_results)
        sys.exit(1)
    signal.signal(signal.SIGINT, sigint_handler)

    if args.phase == "1":
        # ---- 第 1 阶段：初筛 18 组 × 5 分钟 ----
        print(f"=== Phase 1: {len(PHASE1_GRID)} configs × {args.duration}s ===\n")
        ckpt = checkpoint_path(args.leaderboard)
        done_ids = checkpoint_done_ids(ckpt)
        all_results = checkpoint_load(ckpt)
        if done_ids:
            print(f"已有 checkpoint: {len(done_ids)} 个 run 完成，跳过\n")
        for idx, grid_row in enumerate(PHASE1_GRID):
            params = {
                "min_chunk_sec": grid_row[0],
                "max_chunk_sec": grid_row[1],
                "overlap_sec": grid_row[2],
                "silence_submit_sec": grid_row[3],
                **PHASE1_FIXED,
            }
            run_id = make_run_id("1", idx)
            if run_id in done_ids:
                print(f"[{idx+1}/{len(PHASE1_GRID)}] {run_id}: 已完成，跳过")
                continue
            r = run_one(run_id, params, args.duration, "5min", idx, len(PHASE1_GRID),
                        subs=args.subs, file_audio=args.file_audio)
            if r:
                all_results.append(r)
                checkpoint_append(ckpt, r)

            # 用户提示：重启视频（file-audio 模式下跳过）
            if not args.file_audio:
                wait_for_user(args.no_wait, idx, len(PHASE1_GRID))

        write_leaderboard(args.leaderboard, all_results)

        # 选出 top-5 配置并保存
        top = rank_and_select(all_results, top_n=5)
        top_path = os.path.join(RUNS_DIR, "phase1_top5.json")
        with open(top_path, "w") as f:
            json.dump([r["params"] for r in top], f, indent=2)
        print(f"\nTop 5 配置已保存到 {top_path}")
        print(f"下一步: python3 sweep.py --subs '...' --phase 1b --duration 900 --top-configs {top_path}")

    elif args.phase == "1b":
        # ---- 第 1 阶段复赛：top N × 15 分钟 ----
        if not args.top_configs:
            print("错误: --top-configs 必须指定 (phase1_top5.json)")
            sys.exit(1)
        with open(args.top_configs) as f:
            configs = json.load(f)
        print(f"=== Phase 1b: {len(configs)} configs × {args.duration}s ===\n")
        ckpt = checkpoint_path(args.leaderboard)
        done_ids = checkpoint_done_ids(ckpt)
        all_results = checkpoint_load(ckpt)
        if done_ids:
            print(f"已有 checkpoint: {len(done_ids)} 个 run 完成，跳过\n")
        for idx, params in enumerate(configs):
            run_id = make_run_id("1b", idx)
            if run_id in done_ids:
                print(f"[{idx+1}/{len(configs)}] {run_id}: 已完成，跳过")
                continue
            r = run_one(run_id, params, args.duration, "15min", idx, len(configs),
                        subs=args.subs, file_audio=args.file_audio)
            if r:
                all_results.append(r)
                checkpoint_append(ckpt, r)
            if not args.file_audio:
                wait_for_user(args.no_wait, idx, len(configs))

        write_leaderboard(args.leaderboard, all_results)
        top = rank_and_select(all_results, top_n=3)
        top_path = os.path.join(RUNS_DIR, "phase1b_top3.json")
        with open(top_path, "w") as f:
            json.dump([r["params"] for r in top], f, indent=2)
        print(f"\nTop 3 配置已保存到 {top_path}")
        print(f"下一步: python3 sweep.py --subs '...' --phase 2 --duration 900 --base-configs {top_path}")

    elif args.phase == "2":
        # ---- 第 2 阶段：prompt 验证 ----
        if not args.base_configs:
            print("错误: --base-configs 必须指定 (phase1b_top3.json)")
            sys.exit(1)
        with open(args.base_configs) as f:
            base_configs = json.load(f)

        combos = []
        for base in base_configs:
            for pm, pt in PROMPT_STRATEGIES:
                p = dict(base)
                p["prompt_mode"] = pm
                p["prompt_tail_chars"] = pt
                combos.append(p)

        print(f"=== Phase 2: {len(combos)} combos × {args.duration}s ===\n")
        ckpt = checkpoint_path(args.leaderboard)
        done_ids = checkpoint_done_ids(ckpt)
        all_results = checkpoint_load(ckpt)
        if done_ids:
            print(f"已有 checkpoint: {len(done_ids)} 个 run 完成，跳过\n")
        for idx, params in enumerate(combos):
            run_id = make_run_id("2", idx)
            if run_id in done_ids:
                print(f"[{idx+1}/{len(combos)}] {run_id}: 已完成，跳过")
                continue
            r = run_one(run_id, params, args.duration, "15min", idx, len(combos),
                        subs=args.subs, file_audio=args.file_audio)
            if r:
                all_results.append(r)
                checkpoint_append(ckpt, r)
            if not args.file_audio:
                wait_for_user(args.no_wait, idx, len(combos))

        write_leaderboard(args.leaderboard, all_results)
        top = rank_and_select(all_results, top_n=1)
        if top:
            best_path = os.path.join(RUNS_DIR, "phase2_best.json")
            with open(best_path, "w") as f:
                json.dump(top[0]["params"], f, indent=2)
            print(f"\n最优配置已保存到 {best_path}")
            print(f"下一步: python3 sweep.py --subs '...' --phase 3 --duration 900 --best-config {best_path}")

    elif args.phase == "2r":
        # ---- 稳健性验证：prompt × repeats ----
        # 主配置：v6_1b_004（Phase 1b 唯一全程稳定冠军）
        CHAMPION = {
            "min_chunk_sec": 2.0, "max_chunk_sec": 5.5,
            "overlap_sec": 0.3, "silence_threshold": 0.008,
            "silence_submit_sec": 0.4, "no_speech_threshold": 0.45,
            "temperature": "0.0",
        }
        # Shadow control：v6_1b_003（三窗口也稳定，WER 略高）
        SHADOW = {
            "min_chunk_sec": 2.5, "max_chunk_sec": 5.5,
            "overlap_sec": 0.5, "silence_threshold": 0.008,
            "silence_submit_sec": 0.4, "no_speech_threshold": 0.45,
            "temperature": "0.0",
        }
        repeats = args.repeats

        configs = [("champion", CHAMPION)]
        if args.shadow:
            configs.append(("shadow", SHADOW))

        combos = []
        for cfg_name, base in configs:
            for pm, pt in PROMPT_STRATEGIES:
                p = dict(base)
                p["prompt_mode"] = pm
                p["prompt_tail_chars"] = pt
                for rep in range(repeats):
                    run_p = dict(p)
                    run_p["_label"] = f"{cfg_name}_{pm}{('_'+str(pt)) if pt else ''}_r{rep}"
                    combos.append(run_p)

        total = len(combos)
        print(f"=== Phase 2r: {len(configs)} configs × {len(PROMPT_STRATEGIES)} prompts × {repeats} repeats = {total} runs × {args.duration}s ===")
        print(f"预计耗时: {total * (args.duration + 30) / 3600:.1f}h\n")

        # 断点续跑：加载已有 checkpoint
        ckpt = checkpoint_path(args.leaderboard)
        done_ids = checkpoint_done_ids(ckpt)
        all_results = checkpoint_load(ckpt)
        if done_ids:
            print(f"已有 checkpoint: {len(done_ids)} 个 run 完成，跳过\n")

        for idx, params in enumerate(combos):
            label = params.pop("_label")
            run_id = f"v6_2r_{idx:03d}"
            if run_id in done_ids:
                print(f"[{idx+1}/{total}] {run_id}: 已完成，跳过")
                continue
            r = run_one(run_id, params, args.duration, "15min", idx, total,
                        subs=args.subs, file_audio=args.file_audio)
            if r:
                r["label"] = label
                all_results.append(r)
                checkpoint_append(ckpt, r)
            if not args.file_audio:
                wait_for_user(args.no_wait, idx, total)

        write_leaderboard(args.leaderboard, all_results)

        # 按 (config, prompt) 分组，计算稳健性指标
        groups = defaultdict(list)
        for r in all_results:
            key = (r["label"].rsplit("_r", 1)[0],)  # "champion_fixed" etc.
            groups[key].append(r)

        print(f"\n{'='*70}")
        print(f"{'Phase 2r 稳健性排名 (按 median WER)':^70}")
        print(f"{'='*70}")

        agg_rows = []
        for key, runs in groups.items():
            wers = [r["avg_wer"] for r in runs]
            wers_norm = [r.get("avg_wer_normalized", r["avg_wer"]) for r in runs]
            finals_list = [r["n_finals"] for r in runs]
            hallu_count = sum(1 for r in runs if r["avg_wer"] > 0.40 or r["n_finals"] < 30)
            median_wer = sorted(wers)[len(wers) // 2]
            median_wer_norm = sorted(wers_norm)[len(wers_norm) // 2]
            avg_finals = sum(finals_list) / len(finals_list)
            best_run = min(runs, key=lambda r: r["avg_wer"])

            print(f"\n  {key[0]}:")
            print(f"    WER: {[f'{w:.3f}' for w in wers]}")
            print(f"    median={median_wer:.3f}  best={min(wers):.3f}  worst={max(wers):.3f}")
            print(f"    hallucinations={hallu_count}/{len(runs)}  avg_finals={avg_finals:.0f}")

            agg_rows.append({
                "label": key[0],
                "median_wer": round(median_wer, 4),
                "median_wer_normalized": round(median_wer_norm, 4),
                "best_wer": round(min(wers), 4),
                "worst_wer": round(max(wers), 4),
                "spread": round(max(wers) - min(wers), 4),
                "hallucinations": hallu_count,
                "total_runs": len(runs),
                "avg_finals": round(avg_finals, 1),
                "best_run_id": best_run["run_id"],
            })

        # 排序：median_wer → hallucinations → spread
        agg_rows.sort(key=lambda r: (r["median_wer"], r["hallucinations"], r["spread"]))

        print(f"\n{'='*70}")
        print("最终排名:")
        for i, r in enumerate(agg_rows, 1):
            print(f"  {i}. {r['label']}: median={r['median_wer']:.3f} "
                  f"best={r['best_wer']:.3f} worst={r['worst_wer']:.3f} "
                  f"hallu={r['hallucinations']}/{r['total_runs']}")

        # 写聚合 CSV
        agg_path = os.path.join(RUNS_DIR, "phase2r_aggregated.csv")
        agg_fieldnames = ["label", "median_wer", "median_wer_normalized", "best_wer", "worst_wer",
                          "spread", "hallucinations", "total_runs", "avg_finals", "best_run_id"]
        with open(agg_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=agg_fieldnames)
            writer.writeheader()
            writer.writerows(agg_rows)
        print(f"\n聚合结果已写入 {agg_path}")

        # 保存胜出 prompt 配置
        if agg_rows:
            winner_label = agg_rows[0]["label"]
            best_id = agg_rows[0]["best_run_id"]
            # 从 best run 找完整 params
            best_run = next(r for r in all_results if r["run_id"] == best_id)
            best_path = os.path.join(RUNS_DIR, "phase2r_best.json")
            with open(best_path, "w") as f:
                json.dump(best_run["params"], f, indent=2)
            print(f"胜出配置: {winner_label} (best run: {best_id})")
            print(f"已保存到 {best_path}")
            print(f"下一步: python3 sweep.py --subs '...' --phase 3 --duration 900 --best-config {best_path}")

    elif args.phase == "3":
        # ---- 第 3 阶段：temperature 验证 ----
        if not args.best_config:
            print("错误: --best-config 必须指定 (phase2_best.json)")
            sys.exit(1)
        with open(args.best_config) as f:
            base = json.load(f)

        combos = []
        for temp in TEMP_STRATEGIES:
            p = dict(base)
            p["temperature"] = temp
            combos.append(p)

        print(f"=== Phase 3: {len(combos)} temperature combos × {args.duration}s ===\n")
        ckpt = checkpoint_path(args.leaderboard)
        done_ids = checkpoint_done_ids(ckpt)
        all_results = checkpoint_load(ckpt)
        if done_ids:
            print(f"已有 checkpoint: {len(done_ids)} 个 run 完成，跳过\n")
        for idx, params in enumerate(combos):
            run_id = make_run_id("3", idx)
            if run_id in done_ids:
                print(f"[{idx+1}/{len(combos)}] {run_id}: 已完成，跳过")
                continue
            r = run_one(run_id, params, args.duration, "15min", idx, len(combos),
                        subs=args.subs, file_audio=args.file_audio)
            if r:
                all_results.append(r)
                checkpoint_append(ckpt, r)
            if not args.file_audio:
                wait_for_user(args.no_wait, idx, len(combos))

        write_leaderboard(args.leaderboard, all_results)
        top = rank_and_select(all_results, top_n=1)
        if top:
            print(f"\n=== 最终配置 ===")
            print(json.dumps(top[0]["params"], indent=2))
            final_path = os.path.join(RUNS_DIR, "FINAL_CONFIG.json")
            with open(final_path, "w") as f:
                json.dump(top[0]["params"], f, indent=2)
            print(f"已保存到 {final_path}")

    elif args.phase == "regress":
        # ---- 回归验证：N 次重复，median WER ≤ 阈值 ----
        config_path = args.best_config or os.path.join(RUNS_DIR, "FINAL_CONFIG.json")
        if not os.path.exists(config_path):
            print(f"错误: 找不到配置文件 {config_path}，请用 --best-config 指定")
            sys.exit(1)
        with open(config_path) as f:
            base = json.load(f)

        repeats = args.repeats
        baseline = args.regress_baseline
        print(f"=== 回归验证：{repeats} 次重复 × {args.duration}s ===")
        print(f"配置: {config_path}")
        print(f"WER 阈值: ≤ {baseline*100:.1f}%\n")

        ckpt = checkpoint_path(args.leaderboard)
        done_ids = checkpoint_done_ids(ckpt)
        all_results = checkpoint_load(ckpt)
        if done_ids:
            print(f"已有 checkpoint: {len(done_ids)} 个 run 完成，跳过\n")

        for idx in range(repeats):
            run_id = f"v6_regress_{idx:03d}"
            if run_id in done_ids:
                print(f"[{idx+1}/{repeats}] {run_id}: 已完成，跳过")
                continue
            r = run_one(run_id, dict(base), args.duration, "regress", idx, repeats,
                        subs=args.subs, file_audio=args.file_audio)
            if r:
                all_results.append(r)
                checkpoint_append(ckpt, r)
            if not args.file_audio:
                wait_for_user(args.no_wait, idx, repeats)

        write_leaderboard(args.leaderboard, all_results)

        # 计算回归指标
        wers = [r["avg_wer"] for r in all_results[-repeats:]]  # 只取本次 regress 的结果
        wers_norm = [r.get("avg_wer_normalized", r["avg_wer"]) for r in all_results[-repeats:]]
        finals_list = [r["n_finals"] for r in all_results[-repeats:]]
        hallu_count = sum(1 for r in all_results[-repeats:]
                          if r["avg_wer"] > 0.40 or r["n_finals"] < 30)

        median_wer = sorted(wers)[len(wers) // 2]
        median_wer_norm = sorted(wers_norm)[len(wers_norm) // 2]
        spread = max(wers) - min(wers)
        avg_finals = sum(finals_list) / len(finals_list)
        passed = median_wer <= baseline and hallu_count == 0

        print(f"\n{'='*70}")
        print(f"{'回归验证结果':^70}")
        print(f"{'='*70}")
        print(f"  重复次数:   {repeats}")
        print(f"  WER 逐次:   {[f'{w:.4f}' for w in wers]}")
        print(f"  Median WER: {median_wer:.4f}  (normalized: {median_wer_norm:.4f})")
        print(f"  Best:       {min(wers):.4f}")
        print(f"  Worst:      {max(wers):.4f}")
        print(f"  Spread:     {spread:.4f}")
        print(f"  Halluc:     {hallu_count}/{repeats}")
        print(f"  Avg finals: {avg_finals:.0f}")
        print(f"  阈值:       ≤ {baseline*100:.1f}%")
        print()
        if passed:
            print(f"  ✅ PASS  median {median_wer*100:.2f}% ≤ {baseline*100:.1f}%")
        else:
            print(f"  ❌ FAIL  median {median_wer*100:.2f}% > {baseline*100:.1f}%")
            if hallu_count > 0:
                print(f"           hallucinations: {hallu_count}/{repeats}")
        print(f"{'='*70}")

        # 写回归报告 JSON
        regress_report = {
            "passed": passed,
            "config": base,
            "repeats": repeats,
            "baseline_threshold": baseline,
            "median_wer": round(median_wer, 4),
            "median_wer_normalized": round(median_wer_norm, 4),
            "best_wer": round(min(wers), 4),
            "worst_wer": round(max(wers), 4),
            "spread": round(spread, 4),
            "hallucinations": hallu_count,
            "avg_finals": round(avg_finals, 1),
            "run_ids": [r["run_id"] for r in all_results[-repeats:]],
        }
        report_path = os.path.join(RUNS_DIR, "regress_report.json")
        with open(report_path, "w") as f:
            json.dump(regress_report, f, indent=2)
        print(f"\n回归报告: {report_path}")
        sys.exit(0 if passed else 1)

    elif args.phase == "tail":
        # ---- Tail prompt 长度扫描 ----
        # 冠军配置（固定不变）
        CHAMPION = {
            "min_chunk_sec": 2.0, "max_chunk_sec": 5.5,
            "overlap_sec": 0.3, "silence_threshold": 0.008,
            "silence_submit_sec": 0.4, "no_speech_threshold": 0.45,
            "temperature": "0.0",
            "prompt_mode": "tail",
        }
        repeats = args.repeats
        tail_lengths = [int(x) for x in args.tail_lengths.split(",")]

        combos = []
        for tl in tail_lengths:
            for rep in range(repeats):
                p = dict(CHAMPION)
                p["prompt_tail_chars"] = tl
                p["_label"] = f"tail_{tl}_r{rep}"
                combos.append(p)

        total = len(combos)
        print(f"=== Tail 扫描: {len(tail_lengths)} 长度 × {repeats} repeats = {total} runs × {args.duration}s ===")
        print(f"长度: {tail_lengths}")
        print(f"预计耗时: {total * (args.duration + 30) / 3600:.1f}h\n")

        ckpt = checkpoint_path(args.leaderboard)
        done_ids = checkpoint_done_ids(ckpt)
        all_results = checkpoint_load(ckpt)
        if done_ids:
            print(f"已有 checkpoint: {len(done_ids)} 个 run 完成，跳过\n")

        for idx, params in enumerate(combos):
            label = params.pop("_label")
            run_id = f"v6_tail_{idx:03d}"
            if run_id in done_ids:
                print(f"[{idx+1}/{total}] {run_id}: 已完成，跳过")
                continue
            r = run_one(run_id, params, args.duration, "tail", idx, total,
                        subs=args.subs, file_audio=args.file_audio)
            if r:
                r["label"] = label
                all_results.append(r)
                checkpoint_append(ckpt, r)
            if not args.file_audio:
                wait_for_user(args.no_wait, idx, total)

        write_leaderboard(args.leaderboard, all_results)

        # 按 tail 长度分组聚合
        groups = defaultdict(list)
        for r in all_results:
            tl = r["params"].get("prompt_tail_chars", 0)
            groups[tl].append(r)

        agg_rows = []
        for tl, runs in groups.items():
            wers = [r["avg_wer"] for r in runs]
            wers_norm = [r.get("avg_wer_normalized", r["avg_wer"]) for r in runs]
            finals_list = [r["n_finals"] for r in runs]
            hallu_count = sum(1 for r in runs if r["avg_wer"] > 0.40 or r["n_finals"] < 30)
            median_wer = sorted(wers)[len(wers) // 2]
            median_wer_norm = sorted(wers_norm)[len(wers_norm) // 2]
            avg_finals = sum(finals_list) / len(finals_list)

            agg_rows.append({
                "tail_chars": tl,
                "median_wer": round(median_wer, 4),
                "median_wer_normalized": round(median_wer_norm, 4),
                "best_wer": round(min(wers), 4),
                "worst_wer": round(max(wers), 4),
                "spread": round(max(wers) - min(wers), 4),
                "hallucinations": hallu_count,
                "total_runs": len(runs),
                "avg_finals": round(avg_finals, 1),
            })

        agg_rows.sort(key=lambda r: r["median_wer"])

        print(f"\n{'='*70}")
        print(f"{'Tail Prompt 长度扫描结果 (按 median WER)':^70}")
        print(f"{'='*70}")
        print(f"  {'长度':>6}  {'Median':>8}  {'Norm':>8}  {'Best':>8}  {'Worst':>8}  {'Spread':>8}  {'Halluc':>7}  {'Finals':>7}")
        print(f"  {'-'*6}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*7}")
        for r in agg_rows:
            print(f"  {r['tail_chars']:>6}  {r['median_wer']*100:>7.2f}%  {r['median_wer_normalized']*100:>7.2f}%  "
                  f"{r['best_wer']*100:>7.2f}%  {r['worst_wer']*100:>7.2f}%  {r['spread']*100:>7.3f}%  "
                  f"{r['hallucinations']:>3}/{r['total_runs']}  {r['avg_finals']:>6.0f}")
        print(f"{'='*70}")

        # 写聚合 CSV
        agg_path = os.path.join(RUNS_DIR, "tail_scan_aggregated.csv")
        agg_fieldnames = ["tail_chars", "median_wer", "median_wer_normalized", "best_wer", "worst_wer",
                          "spread", "hallucinations", "total_runs", "avg_finals"]
        with open(agg_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=agg_fieldnames)
            writer.writeheader()
            writer.writerows(agg_rows)
        print(f"\n聚合结果: {agg_path}")

        if agg_rows:
            best = agg_rows[0]
            print(f"最优 tail 长度: {best['tail_chars']} (median WER {best['median_wer']*100:.2f}%)")
            # 保存最优配置
            best_config = dict(CHAMPION)
            best_config["prompt_tail_chars"] = best["tail_chars"]
            best_path = os.path.join(RUNS_DIR, "tail_best.json")
            with open(best_path, "w") as f:
                json.dump(best_config, f, indent=2)
            print(f"最优配置: {best_path}")

    elif args.phase == "vad":
        # ---- VAD 阈值扫描：silence_threshold × no_speech_threshold ----
        BASE = {
            "min_chunk_sec": 2.0, "max_chunk_sec": 5.5,
            "overlap_sec": 0.3, "silence_submit_sec": 0.4,
            "temperature": "0.0", "prompt_mode": "tail", "prompt_tail_chars": 160,
        }
        sil_thresholds = [float(x) for x in args.silence_thresholds.split(",")]
        nsp_thresholds = [float(x) for x in args.no_speech_thresholds.split(",")]
        repeats = args.repeats

        combos = []
        for sil in sil_thresholds:
            for nsp in nsp_thresholds:
                for rep in range(repeats):
                    p = dict(BASE)
                    p["silence_threshold"] = sil
                    p["no_speech_threshold"] = nsp
                    p["_label"] = f"sil{sil}_nsp{nsp}_r{rep}"
                    combos.append(p)

        total = len(combos)
        print(f"=== VAD 扫描: {len(sil_thresholds)} sil × {len(nsp_thresholds)} nsp × {repeats} repeats = {total} runs × {args.duration}s ===")
        print(f"silence_threshold: {sil_thresholds}")
        print(f"no_speech_threshold: {nsp_thresholds}")
        print(f"预计耗时: {total * (args.duration + 30) / 3600:.1f}h\n")

        ckpt = checkpoint_path(args.leaderboard)
        done_ids = checkpoint_done_ids(ckpt)
        all_results = checkpoint_load(ckpt)
        if done_ids:
            print(f"已有 checkpoint: {len(done_ids)} 个 run 完成，跳过\n")

        for idx, params in enumerate(combos):
            label = params.pop("_label")
            run_id = f"v6_vad_{idx:03d}"
            if run_id in done_ids:
                print(f"[{idx+1}/{total}] {run_id}: 已完成，跳过")
                continue
            r = run_one(run_id, params, args.duration, "vad", idx, total,
                        subs=args.subs, file_audio=args.file_audio)
            if r:
                r["label"] = label
                all_results.append(r)
                checkpoint_append(ckpt, r)
            if not args.file_audio:
                wait_for_user(args.no_wait, idx, total)

        write_leaderboard(args.leaderboard, all_results)

        # 按 (sil, nsp) 分组聚合
        groups = defaultdict(list)
        for r in all_results:
            sil = r["params"].get("silence_threshold", 0.008)
            nsp = r["params"].get("no_speech_threshold", 0.45)
            groups[(sil, nsp)].append(r)

        agg_rows = []
        for (sil, nsp), runs in groups.items():
            wers = [r["avg_wer"] for r in runs]
            wers_norm = [r.get("avg_wer_normalized", r["avg_wer"]) for r in runs]
            finals_list = [r["n_finals"] for r in runs]
            hallu_count = sum(1 for r in runs if r["avg_wer"] > 0.40 or r["n_finals"] < 30)
            median_wer = sorted(wers)[len(wers) // 2]
            median_wer_norm = sorted(wers_norm)[len(wers_norm) // 2]
            avg_finals = sum(finals_list) / len(finals_list)

            agg_rows.append({
                "silence_threshold": sil,
                "no_speech_threshold": nsp,
                "median_wer": round(median_wer, 4),
                "median_wer_normalized": round(median_wer_norm, 4),
                "best_wer": round(min(wers), 4),
                "worst_wer": round(max(wers), 4),
                "spread": round(max(wers) - min(wers), 4),
                "hallucinations": hallu_count,
                "total_runs": len(runs),
                "avg_finals": round(avg_finals, 1),
            })

        agg_rows.sort(key=lambda r: r["median_wer"])

        print(f"\n{'='*78}")
        print(f"{'VAD 阈值扫描结果 (按 median WER)':^78}")
        print(f"{'='*78}")
        print(f"  {'sil':>7}  {'nsp':>5}  {'Median':>8}  {'Norm':>8}  {'Best':>8}  {'Worst':>8}  {'Spread':>8}  {'Halluc':>7}  {'Finals':>7}")
        print(f"  {'-'*7}  {'-'*5}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*7}")
        for r in agg_rows:
            print(f"  {r['silence_threshold']:>7.3f}  {r['no_speech_threshold']:>5.2f}  "
                  f"{r['median_wer']*100:>7.2f}%  {r['median_wer_normalized']*100:>7.2f}%  "
                  f"{r['best_wer']*100:>7.2f}%  {r['worst_wer']*100:>7.2f}%  {r['spread']*100:>7.3f}%  "
                  f"{r['hallucinations']:>3}/{r['total_runs']}  {r['avg_finals']:>6.0f}")
        print(f"{'='*78}")

        # 写聚合 CSV
        agg_path = os.path.join(RUNS_DIR, "vad_scan_aggregated.csv")
        agg_fieldnames = ["silence_threshold", "no_speech_threshold", "median_wer", "median_wer_normalized",
                          "best_wer", "worst_wer", "spread", "hallucinations", "total_runs", "avg_finals"]
        with open(agg_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=agg_fieldnames)
            writer.writeheader()
            writer.writerows(agg_rows)
        print(f"\n聚合结果: {agg_path}")

        if agg_rows:
            best = agg_rows[0]
            print(f"最优 VAD 配置: sil={best['silence_threshold']}, nsp={best['no_speech_threshold']} "
                  f"(median WER {best['median_wer']*100:.2f}%)")
            best_config = dict(BASE)
            best_config["silence_threshold"] = best["silence_threshold"]
            best_config["no_speech_threshold"] = best["no_speech_threshold"]
            best_path = os.path.join(RUNS_DIR, "vad_best.json")
            with open(best_path, "w") as f:
                json.dump(best_config, f, indent=2)
            print(f"最优配置: {best_path}")


if __name__ == "__main__":
    main()
