#!/bin/bash
# Prompt sweep: test different initial prompts
PROMPTS=(
    "v6_prm_A|The following is a conversation about AI, technology, and Anthropic."
    "v6_prm_B|The following is a conversation in English."
    "v6_prm_C|The following is a conversation."
    "v6_prm_D|"
)

for item in "${PROMPTS[@]}"; do
    IFS='|' read -r run_id prompt <<< "$item"
    run_dir="/tmp/whicc/runs/$run_id"
    events="$run_dir/events.jsonl"
    mkdir -p "$run_dir"
    rm -f "$events"
    rm -f /tmp/whicc-seg/*.pcm 2>/dev/null

    echo ""
    echo "========================================"
    echo "Testing $run_id"
    echo "Prompt: '$prompt'"
    echo "Start: $(date)"
    echo "========================================"

    python3 /tmp/whicc/src/whicc.py \
        --run-id "$run_id" \
        --min-chunk-sec 2.0 \
        --max-chunk-sec 5.5 \
        --overlap-sec 0.3 \
        --silence-threshold 0.008 \
        --silence-submit-sec 0.4 \
        --no-speech-threshold 0.45 \
        --temperature 0.0 \
        --prompt-mode tail \
        --prompt-tail-chars 160 \
        --initial-prompt "$prompt" \
        --events-jsonl "$events" \
        --audio-bin "python3 /tmp/whicc/tools/whicc_file_audio.py /tmp/whicc-audio-test.pcm" &
    WHICC_PID=$!

    sleep 300
    kill -INT $WHICC_PID 2>/dev/null
    sleep 2
    kill -9 $WHICC_PID 2>/dev/null
    pkill -f "whicc_file_audio" 2>/dev/null
    rm -f /tmp/whicc-seg/*.pcm 2>/dev/null

    echo "End: $(date)"

    # Quick metrics
    python3 -c "
import json, statistics
with open('$events') as f:
    events = [json.loads(l) for l in f]
finals = [e for e in events if e.get('event_type')=='final']
if finals:
    durations = [e.get('chunk_sec',0) for e in finals]
    infer = [e.get('transcribe_ms',0)/1000 for e in finals]
    print(f'  Finals: {len(finals)}, Chunk: {statistics.median(durations):.1f}s, Infer: {statistics.median(infer):.2f}s')
else:
    print('  No finals!')
"

    # Backtest
    # SUBS_FILE 通过环境变量传（默认空）。回测 WER 必需真实字幕文件，
    # 跑本脚本前先准备：export SUBS_FILE=/path/to/your.srt
    if [ -z "${SUBS_FILE:-}" ]; then
        echo "  ⚠ SUBS_FILE 未设置,跳过 backtest (export SUBS_FILE=...)"
    else
        python3 /tmp/whicc/tools/backtest.py \
            --events "$events" \
            --subs "$SUBS_FILE" \
            --out "$run_dir/backtest.csv" \
            --run-id "$run_id" \
            --stage 15min \
            --max-chunk-sec 5.5 \
            --silence-submit-sec 0.4 \
            --prompt-tail-chars 160 2>&1
    fi

    if [ -f "$run_dir/backtest.csv" ]; then
        tail -1 "$run_dir/backtest.csv" | awk -F',' '{printf "  WER=%s Norm=%s Finals=%s\n", $13, $14, $20}'
    fi
done

echo ""
echo "=== All prompt tests complete ==="
