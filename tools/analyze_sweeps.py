#!/usr/bin/env python3
"""Analyze tail and VAD sweep results for latency optimization."""
import csv
import json
import os
import sys

RUNS_DIR = "/tmp/whicc/runs"


def load_leaderboard(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def analyze_latency(run_id):
    events_path = os.path.join(RUNS_DIR, run_id, "events.jsonl")
    if not os.path.exists(events_path):
        return None
    events = []
    with open(events_path) as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except:
                pass
    finals = [e for e in events if e.get("event_type") == "final" and e.get("accepted")]
    if not finals:
        return None

    inf_times = [e["transcribe_ms"] for e in finals if "transcribe_ms" in e]
    latencies = [e["relative_confirm_latency_sec"] for e in finals]
    chunks = [e["chunk_sec"] for e in finals]
    silence_runs = [e for e in finals if e.get("submit_reason") == "silence"]
    max_runs = [e for e in finals if e.get("submit_reason") == "max_chunk"]

    # Effective latency: silence_wait + inference
    eff = []
    for e in finals:
        trailing = e.get("trailing_silence_sec", 0)
        infer = e.get("transcribe_ms", 0) / 1000
        eff.append(trailing + infer)

    import statistics
    return {
        "n_finals": len(finals),
        "inf_median": statistics.median(inf_times) if inf_times else 0,
        "inf_p95": sorted(inf_times)[int(len(inf_times)*0.95)] if inf_times else 0,
        "lat_median": statistics.median(latencies) if latencies else 0,
        "chunk_median": statistics.median(chunks) if chunks else 0,
        "eff_median": statistics.median(eff) if eff else 0,
        "eff_p95": sorted(eff)[int(len(eff)*0.95)] if eff else 0,
        "silence_count": len(silence_runs),
        "max_chunk_count": len(max_runs),
    }


def print_tail_results():
    path = os.path.join(RUNS_DIR, "lb_tail2.csv")
    rows = load_leaderboard(path)
    if not rows:
        print("No tail results yet")
        return

    print("=" * 70)
    print("  Tail Prompt Sweep Results")
    print("=" * 70)
    print(f"{'Length':>8} {'WER':>8} {'Norm':>8} {'Finals':>8} {'Inf(ms)':>8} {'EffLat':>8} {'Sil%':>8}")
    print("-" * 70)
    for r in sorted(rows, key=lambda x: float(x.get("prompt_tail_chars", 0))):
        tl = r.get("prompt_tail_chars", "?")
        wer = float(r.get("avg_wer", 0)) * 100
        norm = float(r.get("avg_wer_normalized", 0)) * 100
        finals = r.get("n_finals", "?")
        run_id = r.get("run_id", "")
        lat = analyze_latency(run_id)
        if lat:
            print(f"{tl:>8} {wer:>7.1f}% {norm:>7.1f}% {finals:>8} {lat['inf_median']:>7.0f} {lat['eff_median']:>7.2f}s {lat['silence_count']/max(lat['n_finals'],1)*100:>7.0f}%")
        else:
            print(f"{tl:>8} {wer:>7.1f}% {norm:>7.1f}% {finals:>8} {'?':>7} {'?':>8} {'?':>8}")
    print()


def print_vad_results():
    path = os.path.join(RUNS_DIR, "leaderboard_vad.csv")
    rows = load_leaderboard(path)
    # Filter out corrupted runs (v6_vad_000, 001, 002)
    rows = [r for r in rows if r.get("run_id", "") not in ("v6_vad_000", "v6_vad_001", "v6_vad_002")]
    if not rows:
        print("No valid VAD results yet")
        return

    print("=" * 80)
    print("  VAD Threshold Sweep Results (excl. corrupted runs)")
    print("=" * 80)
    print(f"{'sil_thr':>8} {'nsp_thr':>8} {'WER':>8} {'Norm':>8} {'Finals':>8} {'Inf(ms)':>8} {'EffLat':>8} {'Rej%':>8}")
    print("-" * 80)
    for r in sorted(rows, key=lambda x: (float(x.get("silence_threshold", 0)), float(x.get("no_speech_threshold", 0)))):
        sil = r.get("silence_threshold", "?")
        nsp = r.get("no_speech_threshold", "?")
        wer = float(r.get("avg_wer", 0)) * 100
        norm = float(r.get("avg_wer_normalized", 0)) * 100
        finals = r.get("n_finals", "?")
        rej = float(r.get("reject_rate", 0)) * 100
        run_id = r.get("run_id", "")
        lat = analyze_latency(run_id)
        if lat:
            print(f"{sil:>8} {nsp:>8} {wer:>7.1f}% {norm:>7.1f}% {finals:>8} {lat['inf_median']:>7.0f} {lat['eff_median']:>7.2f}s {rej:>7.1f}%")
        else:
            print(f"{sil:>8} {nsp:>8} {wer:>7.1f}% {norm:>7.1f}% {finals:>8} {'?':>7} {'?':>8} {rej:>7.1f}%")
    print()


if __name__ == "__main__":
    print_tail_results()
    print_vad_results()
