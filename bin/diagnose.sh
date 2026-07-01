#!/bin/bash
# whicc 诊断脚本 — 定位"没字幕"是哪一层断了

echo "============================================"
echo "  whicc 诊断 (跑这一条把输出发我)"
echo "============================================"
echo ""

echo "=== [1] 后端进程 (应该 5 个) ==="
ps aux | grep "whicc.app" | grep -v grep | awk '{print "  PID="$2, $11, $12}'
echo ""

echo "=== [2] 后端 log (最后 10 行) ==="
for f in whicc translate-stream glossary-refresher model-downloader; do
  echo "--- $f.log ---"
  tail -10 /tmp/whicc-out/logs/$f.log 2>&1 | sed 's/^/  /'
  echo ""
done

echo "=== [3] events.jsonl 状态 ==="
ls -la /tmp/whicc-out/events.jsonl 2>&1 | sed 's/^/  /'
echo "  事件数: $(wc -l < /tmp/whicc-out/events.jsonl 2>/dev/null)"
echo "  最近 3 个事件:"
tail -3 /tmp/whicc-out/events.jsonl 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        print(f\"    {d.get('event_type', '?')}: {d.get('source_text', '')[:50]} → {d.get('translated_full_text', '')[:50]}\")
    except: pass
" 2>&1
echo ""

echo "=== [4] macOS 权限 (audiotee 能不能录系统音频) ==="
ls -la /Applications/whicc.app 2>&1 | head -2 | sed 's/^/  /'
echo "  关键: 朋友需要去"
echo "    系统设置 → 隐私与安全 → 屏幕录制与系统音频 → 启用 whicc"
echo "  否则 audiotee 拿到 0 字节的音频"
echo ""

echo "=== [5] 翻译服务连通性 ==="
echo "  translation_url: $(grep -o 'translation_url[^,]*' /tmp/whicc-out/lang_config.json 2>&1 | head -1)"
echo "  测试主 URL:"
URL1=$(grep -o '"translation_url"[^,]*' /tmp/whicc-out/lang_config.json | sed 's/.*"http/http/' | sed 's/".*//')
curl -s --max-time 3 "$URL1/v1/models" 2>&1 | head -1 | sed 's/^/    /'
echo "  测试 fallback URL:"
URL2=$(grep -o '"translation_fallback_url"[^,]*' /tmp/whicc-out/lang_config.json | sed 's/.*"http/http/' | sed 's/".*//')
curl -s --max-time 3 "$URL2/v1/models" 2>&1 | head -1 | sed 's/^/    /'
echo ""

echo "=== [6] macui 端 Console.app (最近 30s whicc log) ==="
/usr/bin/log show --predicate 'process == "whicc"' --info --debug --last 30s 2>&1 | grep -iE "error|fail|crash|watcher" | head -10 | sed 's/^/  /'
echo ""

echo "============================================"
echo "  复制上面所有输出发我"
echo "============================================"
