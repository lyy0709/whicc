#!/usr/bin/env python3
"""文件音频模拟器：替代 whicc-audio Swift 二进制，从 PCM 文件读取音频并写段文件"""
import sys
import os
import signal
import time

SEG_DIR = "/tmp/whicc-seg"
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 4
SEG_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE  # 1秒 = 64000 bytes

def main():
    if len(sys.argv) < 2:
        print("用法: whicc_file_audio.py <input.pcm>", file=sys.stderr)
        sys.exit(1)

    pcm_path = sys.argv[1]
    if not os.path.exists(pcm_path):
        print(f"文件不存在: {pcm_path}", file=sys.stderr)
        sys.exit(1)

    file_size = os.path.getsize(pcm_path)
    total_segs = file_size // SEG_BYTES
    duration_sec = file_size / (SAMPLE_RATE * BYTES_PER_SAMPLE)

    os.makedirs(SEG_DIR, exist_ok=True)
    for f in os.listdir(SEG_DIR):
        if f.endswith(".pcm"):
            try:
                os.unlink(os.path.join(SEG_DIR, f))
            except OSError:
                pass

    print(f"whicc-audio: OK", file=sys.stderr, flush=True)

    running = True
    def on_signal(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    with open(pcm_path, "rb") as f:
        for seg_idx in range(total_segs):
            if not running:
                break
            data = f.read(SEG_BYTES)
            if not data or len(data) < SEG_BYTES:
                break
            path = os.path.join(SEG_DIR, f"seg-{seg_idx:06d}.pcm")
            with open(path, "wb") as out:
                out.write(data)
            time.sleep(1)

    # 清理
    if os.path.isdir(SEG_DIR):
        for f in os.listdir(SEG_DIR):
            if f.endswith(".pcm"):
                try:
                    os.unlink(os.path.join(SEG_DIR, f))
                except OSError:
                    pass

if __name__ == "__main__":
    main()
