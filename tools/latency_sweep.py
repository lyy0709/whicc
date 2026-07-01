#!/usr/bin/env python3
"""延迟优化回测：测试不同 max_chunk / silence_submit 对 WER 和延迟的影响。

用法：
    python3 tools/latency_sweep.py --subs <subtitle-file.srt>
"""
import argparse
import subprocess
import sys
import os
import time
import signal
import shutil
import json

WHICC_PY = "/tmp/whicc/src/whicc.py"
FILE_AUDIO_PY = "/tmp/whicc/tools/whicc_file_audio.py"
BACKTEST_PY = "/tmp/whicc/tools/backtest.py"
PCM_FILE = "/tmp/whicc-audio-test.pcm"
DURATION = 300  # 5 min wall-clock per run

CONFIGS = [
    # run_id, max_chunk, silence_submit
    ("v6_lat_000", 4.0, 0.25),
    ("v6_lat_001", 3.5, 0.25),
    ("v6_lat_002", 4.5, 0.30),
    ("v6_lat_003", 5.5, 0.40),  # champion baseline
]

BASE_PARAMS = {
    "min_chunk_sec": 2.0,
    "overlap_sec": 0.3,
    "silence_threshold": 0.008,
    "no_speech_threshold": 0.45,
    "temperature": "0.0",
    "prompt_mode": "tail",
    "prompt_tail_chars": 160,
}

SEG_DIR = "/tmp/whicc-seg"
RUNS_DIR = "/tmp/whicc/runs"
LEADERBOARD = "/tmp/whicc/runs/leaderboard_latency.csv"

def clean_seg_dir():
    if os.path.isdir(SEG_DIR):
        for f in os.listdir(SEG_DIR):
            if f.endswith(".pcm"):
                try:
                    os.unlink(os.path.join(SEG_DIR, f))
                except OSError:
                    pass

def run_one(run_id, max_chunk, silence_submit):
    run_dir = os.path.join(RUNS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)
    events_file = os.path.join(run_dir, "events.jsonl")

    # Clean old events
    if os.path.exists(events_file):
        os.unlink(events_file)

    params = dict(BASE_PARAMS)
    params["max_chunk_sec"] = max_chunk
    params["silence_submit_sec"] = silence_submit

    cmd = [
        sys.executable, WHICC_PY,
        "--run-id", run_id,
        "--min-chunk-sec", str(params["min_chunk_sec"]),
        "--max-chunk-sec", str(params["max_chunk_sec"]),
        "--overlap-sec", str(params["overlap_sec"]),
        "--silence-threshold", str(params["silence_threshold"]),
        "--silence-submit-sec", str(params["silence_submit_sec"]),
        "--no-speech-threshold", str(params["no_speech_threshold"]),
        "--temperature", params["temperature"],
        "--prompt-mode", params["prompt_mode"],
        "--prompt-tail-chars", str(params["prompt_tail_chars"]),
        "--events-jsonl", events_file,
        "--audio-bin", f"{sys.executable} {FILE_AUDIO_PY} {PCM_FILE}",
    ]

    clean_seg_dir()
    print(f"\n{'='*60}")
    print(f"Running {run_id}: max_chunk={max_chunk}, silence_submit={silence_submit}")
    print(f"{'='*60}")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Wait for model load
    print("  Waiting for model load...", end="", flush=True)
    ready = False
    for line in proc.stderr:
        line_str = line.decode("utf-8", errors="replace")
        if "model" in line_str.lower() and ("load" in line_str.lower() or "ready" in line_str.lower()):
            ready = True
            break
        if "whicc-audio: OK" in line_str:
            ready = True
            break
        if "开始录制" in line_str or "开始" in line_str:
            ready = True
            break
    print(" ready" if ready else " (continued)")

    # Run for DURATION seconds
    print(f"  Recording ({DURATION}s)...", end="", flush=True)
    try:
        proc.wait(timeout=DURATION)
        print(" finished naturally")
    except subprocess.TimeoutExpired:
        print(" timeout, stopping")
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # Cleanup segments
    clean_seg_dir()

    # Run backtest
    print(f"  Running backtest...")
    bt_cmd = [
        sys.executable, BACKTEST_PY,
        "--events", events_file,
        "--subs", SUBS_FILE,
    ]
    result = subprocess.run(bt_cmd, capture_output=True, text=True, timeout=120)
    print(result.stdout)

    # Parse results
    csv_file = os.path.join(run_dir, "backtest.csv")
    if os.path.exists(csv_file):
        with open(csv_file) as f:
            lines = f.readlines()
            if len(lines) > 1:
                print(f"  CSV: {lines[1].strip()}")

    return events_file

def analyze_latency(events_file, run_id):
    """Analyze latency metrics from events"""
    if not os.path.exists(events_file):
        return
    events = []
    with open(events_file) as f:
        for line in f:
            events.append(json.loads(line))

    finals = [e for e in events if e.get("event_type") == "final"]
    if not finals:
        print(f"  {run_id}: no finals!")
        return

    import statistics
    durations = [e.get("chunk_sec", 0) for e in finals]
    infer = [e.get("transcribe_ms", 0) / 1000 for e in finals]
    reasons = {}
    for e in finals:
        r = e.get("submit_reason", "unknown")
        reasons[r] = reasons.get(r, 0) + 1
    latency = [e.get("relative_confirm_latency_sec", 0) for e in finals if e.get("relative_confirm_latency_sec")]

    print(f"\n  === {run_id} Latency ===")
    print(f"  Finals: {len(finals)}")
    print(f"  Chunk duration: median={statistics.median(durations):.1f}s, mean={statistics.mean(durations):.1f}s")
    print(f"  Inference: median={statistics.median(infer):.2f}s, mean={statistics.mean(infer):.2f}s")
    print(f"  Submit reasons: {reasons}")
    if latency:
        print(f"  Effective latency: median={statistics.median(latency):.2f}s, mean={statistics.mean(latency):.2f}s")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="延迟优化回测（需 --subs 传字幕文件）"
    )
    parser.add_argument(
        "--subs", required=True,
        help="SRT 字幕文件路径（用来算 WER 基准）",
    )
    args = parser.parse_args()
    SUBS_FILE = args.subs  # noqa: F841 (referenced in run_one)

    os.makedirs(RUNS_DIR, exist_ok=True)

    for run_id, max_chunk, silence_submit in CONFIGS:
        events_file = run_one(run_id, max_chunk, silence_submit)
        analyze_latency(events_file, run_id)

    print("\n" + "=" * 60)
    print("All latency tests complete!")
    print("=" * 60)
