#!/bin/bash
# 给 Ethan 的诊断 — 完整 .sh 文件, Ethan 跑 bash /tmp/diag.sh
# 不在命令行里嵌复杂字符串, 避开 zsh 各种 history/glob/event 问题

cat > /tmp/diag.sh <<'SHELL'
#!/bin/bash
echo "=========================================="
echo "  whicc 诊断 (输出整段发我)"
echo "=========================================="
echo ""
echo "=== [1] whicc.log (启动 log, 关键!) ==="
tail -50 /tmp/whicc-out/logs/whicc.log 2>&1
echo ""
echo "=== [2] events.jsonl 最近 5 行 ==="
tail -5 /tmp/whicc-out/events.jsonl 2>&1
echo ""
echo "=== [3] audiotee 文件存在? ==="
ls -la /Applications/whicc.app/Contents/Resources/bin/audiotee 2>&1
echo ""
echo "=== [4] 手动跑 whicc.py 5 秒看 stderr ==="
MODELS_DIR="$HOME/Library/Application Support/whicc/models"
/Applications/whicc.app/Contents/Resources/venv/bin/python3 /Applications/whicc.app/Contents/Resources/src/whicc.py --events-jsonl /tmp/whicc-out/events.jsonl --model-state /tmp/whicc-out/model_state.json --models-dir "$MODELS_DIR" --model mlx-community/nemotron-3.5-asr-streaming-0.6b --language auto --mode streaming --audio-source system --audio-bin /Applications/whicc.app/Contents/Resources/bin/audiotee 2>&1 &
WHICC_PID=$!
sleep 5
kill -9 $WHICC_PID 2>/dev/null
wait 2>/dev/null
echo ""
echo "=== [5] audiotee 单独跑 (测屏幕录制权限) ==="
/Applications/whicc.app/Contents/Resources/bin/audiotee --sample-rate 16000 2>&1 &
AUDIOTEE_PID=$!
sleep 3
kill -9 $AUDIOTEE_PID 2>/dev/null
wait 2>/dev/null
echo ""
echo "=========================================="
echo "  把从 ==== 开始到 ==== 结束整段输出发我"
echo "=========================================="
SHELL
chmod +x /tmp/diag.sh
echo "Script saved to /tmp/diag.sh"
echo "Run: bash /tmp/diag.sh"
