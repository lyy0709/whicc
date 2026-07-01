#!/usr/bin/env python3
"""
Event Context Agent — 识别用户当前正在观看的事件，生成临时场景和术语表。

用法：
  python3 src/event_agent.py              # 读 event_state.json 执行识别
  python3 src/event_agent.py --confirm    # 用户确认后，生成术语表和场景
  python3 src/event_agent.py --clear      # 清除事件，恢复用户手填场景

三文件架构：
  /tmp/whicc-out/event_state.json     ← overlay ↔ agent 交互状态
  /tmp/whicc-out/event_glossary.json  ← 纯临时词库 {"en2zh": {...}, "zh2en": {...}}
  /tmp/whicc-out/event_scene.json     ← 场景结果 {event_name, temp_scene_text, expires_at, status}
"""

import json
import os
import subprocess
import shlex
import shutil
import sys
import time
import datetime
import uuid

# ── 路径 ─────────────────────────────────────────────────────────────────────

OUT_DIR = "/tmp/whicc-out"
STATE_PATH = os.path.join(OUT_DIR, "event_state.json")
GLOSSARY_PATH = os.path.join(OUT_DIR, "event_glossary.json")
SCENE_PATH = os.path.join(OUT_DIR, "event_scene.json")
LANG_CONFIG_PATH = os.path.join(OUT_DIR, "lang_config.json")
EVENTS_PATH = os.path.join(OUT_DIR, "translation_events.jsonl")

# Hermes Agent 配置
# - HERMES_HOST: 用户的 Hermes 节点地址 (e.g. "user-mac-mini.local")。
#   默认 "" (未配置),用户必须在 macui 设置里填,否则跳过 Hermes 调用。
# - HERMES_INVOKE: 远端启动 hermes 的 shell 命令,在用户机器上要可用。
#   默认用 `which hermes` 探测;探测失败时 fallback 到 `~/.local/bin/hermes`。
#   注意:之前硬编码开发者机器上的绝对路径,在别人电脑
#   上不可用,改成本地探测。
HERMES_HOST = ""
HERMES_INVOKE = ""  # 由 _resolve_hermes_invoke() 在首次调用时探测并缓存

SAMPLE_LINES = 40


# ── 时间 ─────────────────────────────────────────────────────────────────────

def _now_with_tz() -> str:
    now = datetime.datetime.now()
    tz = time.strftime("%Z") or time.tzname[0]
    utc_offset = time.strftime("%z") or ""
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')} {tz} (UTC{utc_offset})"


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


# ── 文件读写 ─────────────────────────────────────────────────────────────────

def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ── ASR 文本采样 ─────────────────────────────────────────────────────────────

def load_recent_texts(n: int = SAMPLE_LINES) -> list[str]:
    """从 translation_events.jsonl 读取最近的 ASR source_text。"""
    try:
        with open(EVENTS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    sources = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("event_type") == "translation_final":
            src = e.get("source_text", "").strip()
            if src:
                sources.append(src)
            if len(sources) >= n:
                break
    return list(reversed(sources))


# ── Hermes Agent ─────────────────────────────────────────────────────────────

def _hermes_available() -> bool:
    """快速检测 Hermes Agent 是否可达（5s 超时）。

    host 没配 → 直接 False。ssh 探测只验 host 通不通,不验 hermes CLI
    是否真的可用 (那是 _call_hermes 的责任)。
    """
    host = _get_hermes_host()
    if not host:
        return False
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "StrictHostKeyChecking=no",
             host, "echo ok"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "ok"
    except Exception:
        return False


def _get_hermes_host() -> str:
    """从 lang_config.json 读取 hermes_host,没配则返回空字符串。

    设计:不写默认值。之前自动写入开发者机器的 mDNS hostname,
    别人电脑首次启动会被塞一个不属于他们的 host → UI 显示"可达"
    但实际不是。现在只读 — 用户没配就空,后续 _hermes_available() 检测
    到空直接返回 False 跳过 Hermes 调用。
    """
    cfg_path = "/tmp/whicc-out/lang_config.json"
    try:
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            host = (cfg.get("hermes_host") or "").strip()
            if host:
                return host
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return ""


def _resolve_hermes_invoke() -> str:
    """探测远端怎么调用 hermes CLI。

    返回 shell 字符串,e.g. "hermes" (PATH 里有) 或 "~/.local/bin/hermes"
    (用户装的 pipx/uv tool)。缓存到 HERMES_INVOKE 全局变量。

    之前硬编码开发者机器上的绝对 hermes 路径
    (在 .hermes/hermes-agent/venv/bin/python3 启动),那在别人电脑
    会 ssh 到对方机器执行 → 路径不存在 → 静默失败。

    探测顺序:
      1. `~/.local/bin/hermes` (pipx/uv tool 默认安装位置,Mac 普遍)
      2. `which hermes` (PATH 里有,Linux 服务器常见)
      3. 空字符串 (探测失败,调用方返回 None 不报错)
    """
    global HERMES_INVOKE
    if HERMES_INVOKE:
        return HERMES_INVOKE

    home = os.path.expanduser("~")
    candidates = [
        f"{home}/.local/bin/hermes",
        "hermes",  # 依赖 PATH
    ]
    for c in candidates:
        if os.path.isabs(c):
            if os.path.exists(c) and os.access(c, os.X_OK):
                HERMES_INVOKE = c
                return HERMES_INVOKE
        else:
            # 用 shlex 拼 shell 命令,远端 bash 自己解析 PATH
            # 探测只检查格式,不实际 ssh (避免每次启动都慢)
            HERMES_INVOKE = c
            return HERMES_INVOKE
    HERMES_INVOKE = ""
    return ""


def _call_hermes(query: str, timeout: int = 120) -> str | None:
    """调用 Hermes Agent，返回 stdout 文本。失败返回 None。

    host 没配或 invoke 探测失败 → 返回 None 跳过调用,不报错。
    之前用 HERMES_BIN + HERMES_CLI 拼绝对路径 (开发者机器),改成本地探测的 invoke。
    """
    host = _get_hermes_host()
    if not host:
        print("[event] Hermes 未配置 (lang_config.json 缺 hermes_host),跳过", file=sys.stderr)
        return None
    invoke = _resolve_hermes_invoke()
    if not invoke:
        print("[event] Hermes CLI 未找到 (~/.local/bin/hermes 或 PATH 里都没有),跳过", file=sys.stderr)
        return None
    try:
        result = subprocess.run(
            ["ssh", host,
             f"{invoke} chat -q {shlex.quote(query)} -Q"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            print(f"[event] Hermes 退出码 {result.returncode}: {result.stderr[:200]}",
                  file=sys.stderr, flush=True)
            return None
        output = result.stdout.strip()
        if not output:
            print("[event] Hermes 返回空", file=sys.stderr, flush=True)
            return None
        return output
    except subprocess.TimeoutExpired:
        print(f"[event] Hermes 超时（{timeout}s）", file=sys.stderr, flush=True)
        return None
    except Exception as exc:
        print(f"[event] Hermes 调用失败: {exc}", file=sys.stderr, flush=True)
        return None


def _parse_json_response(text: str) -> dict | None:
    """从 Hermes 输出中提取 JSON 块。"""
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ``` 块
    import re
    m = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试找第一个 { ... } 块
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ── Phase 1: 识别事件 ───────────────────────────────────────────────────────

def _build_identify_query(state: dict) -> str:
    """构造 Phase 1 的 Hermes 查询。"""
    now_str = _now_with_tz()
    parts = [f"当前时间：{now_str}。"]

    # 最高优先级：用户提示
    user_hint = state.get("user_hint", "").strip()
    if user_hint:
        parts.append(f"用户提示：{user_hint}。请以此为主要线索。")

    # 次高：窗口/页面标题
    for key in ("window_title", "page_title", "page_url"):
        val = state.get(key, "").strip()
        if val:
            parts.append(f"{key}：{val}。")

    # 第三：最近 ASR 文本
    recent = state.get("recent_asr_texts", [])
    if recent:
        sample = "\n".join(recent[-10:])
        parts.append(f"最近转录片段：\n{sample[:800]}")

    parts.append(
        "\n请根据以上信息推断用户当前正在观看/收听的事件。"
        "搜索当前时间附近正在进行或热门的事件。"
        "请严格以 JSON 格式返回，不要加任何其他文字：\n"
        '{"event_name": "事件名称", '
        '"event_type": "sports|launch|conference|interview|earnings|other", '
        '"confidence": 0.0~1.0, '
        '"question_for_user": "用于确认的自然语言问题"}'
    )
    return "\n".join(parts)


def identify_event(state: dict) -> dict:
    """Phase 1: 识别事件，返回候选结果。"""
    query = _build_identify_query(state)
    print(f"[event] Phase 1: 识别事件...", flush=True)

    output = _call_hermes(query, timeout=60)
    if not output:
        return {"status": "no_match", "reason": "Hermes 不可达或返回空"}

    result = _parse_json_response(output)
    if not result:
        print(f"[event] 无法解析 Hermes 输出为 JSON: {output[:200]}", file=sys.stderr, flush=True)
        return {"status": "no_match", "reason": "无法解析 Hermes 输出"}

    confidence = result.get("confidence", 0)
    event_name = result.get("event_name", "")
    question = result.get("question_for_user", "")

    if not event_name:
        return {"status": "no_match", "reason": "未识别到事件"}

    if confidence >= 0.80:
        # 高置信度：直接应用
        return {
            "status": "high_confidence",
            "event_name": event_name,
            "event_type": result.get("event_type", "other"),
            "confidence": confidence,
            "question_for_user": question or f"我确定你在看 {event_name}。",
        }
    elif confidence >= 0.55:
        # 中置信度：需要确认
        return {
            "status": "needs_confirmation",
            "event_name": event_name,
            "event_type": result.get("event_type", "other"),
            "confidence": confidence,
            "question_for_user": question or f"我猜你现在在看 {event_name}，对吗？",
        }
    else:
        return {
            "status": "no_match",
            "event_name": event_name,
            "confidence": confidence,
            "reason": f"置信度过低 ({confidence:.2f})",
        }


# ── Phase 2: 生成术语表和场景 ────────────────────────────────────────────────

def _build_glossary_query(event_name: str, event_type: str, recent_texts: list[str]) -> str:
    """构造 Phase 2 的 Hermes 查询（生成术语表和场景描述）。"""
    now_str = _now_with_tz()
    sample = "\n".join(recent_texts[-10:]) if recent_texts else ""

    type_hints = {
        "sports": "两支参赛球队的完整球员名单（首发+替补）、双方主教练、核心球员的官方中文译名、阵型、战术术语",
        "launch": "产品名、功能名、芯片名、技术规格、公司高管名",
        "conference": "会议名、演讲者名、议题术语、技术概念",
        "interview": "嘉宾名、公司名、行业术语、话题关键词",
        "earnings": "公司名、财务指标、高管名、行业术语",
        "other": "相关专业术语和专有名词",
    }
    hint = type_hints.get(event_type, type_hints["other"])

    # 体育比赛专用提示：要求 Hermes 搜索阵容
    sports_directive = ""
    if event_type == "sports":
        sports_directive = (
            "\n【重要】这是体育比赛。请使用 web 工具集（Web Search）主动搜索以下信息：\n"
            "- 两支参赛球队的完整球员名单（首发阵容+替补席）\n"
            "- 双方主教练\n"
            "- 关键球员的官方中文译名（不要音译，使用权威体育媒体译名）\n"
            "- 常用阵型（4-3-3、4-4-2 等）和战术术语\n"
            "- 联赛/杯赛名称的标准中文翻译\n"
        )

    parts = [
        f"当前时间：{now_str}。",
        f"已确认事件：{event_name}。",
        f"事件类型：{event_type}。",
        f"需要收集的术语类别：{hint}。",
    ]
    if sample:
        parts.append(f"最近转录片段（用于补充术语）：\n{sample[:600]}")

    parts.append(sports_directive)

    parts.append(
        "\n请为这个事件生成：\n"
        "1. 一段简短的场景描述（用于翻译模型的 context，20-40字，说明这是什么类型的事件）\n"
        "2. 该事件的术语表（至少 30 个，覆盖人名、品牌名、专业术语）\n\n"
        "请严格以 JSON 格式返回，不要加任何其他文字：\n"
        '{\n'
        '  "temp_scene_text": "场景描述",\n'
        '  "glossary": {\n'
        '    "en2zh": {"English term": "中文翻译", ...},\n'
        '    "zh2en": {"中文术语": "English translation", ...}\n'
        '  },\n'
        '  "ttl_sec": 7200\n'
        '}'
    )
    return "\n".join(parts)


def generate_glossary(event_name: str, event_type: str, recent_texts: list[str]) -> dict:
    """Phase 2: 生成术语表和场景描述。"""
    query = _build_glossary_query(event_name, event_type, recent_texts)
    print(f"[event] Phase 2: 生成术语表 ({event_name})...", flush=True)

    output = _call_hermes(query, timeout=120)
    if not output:
        return {}

    result = _parse_json_response(output)
    if not result:
        print(f"[event] 无法解析术语表 JSON: {output[:200]}", file=sys.stderr, flush=True)
        return {}

    return result


# ── 应用结果 ─────────────────────────────────────────────────────────────────

def apply_event(event_name: str, event_type: str, scene_text: str,
                glossary: dict, ttl_sec: int = 7200):
    """写入 event_glossary.json + event_scene.json。
    每个术语带 category（事件名）和 added（加入时间）。"""
    now_iso = _now_iso()
    now_short = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # 给每个术语加上 category 和 added 元数据
    enriched_en2zh = {}
    for en, zh in glossary.get("en2zh", {}).items():
        enriched_en2zh[en] = {
            "translation": zh,
            "category": event_name,
            "added": now_short,
        }

    enriched_zh2en = {}
    for zh, en in glossary.get("zh2en", {}).items():
        enriched_zh2en[zh] = {
            "translation": en,
            "category": event_name,
            "added": now_short,
        }

    # 写临时词库
    _write_json(GLOSSARY_PATH, {
        "event_name": event_name,
        "applied_at": now_iso,
        "en2zh": enriched_en2zh,
        "zh2en": enriched_zh2en,
    })

    # 写场景结果
    expires_at = (datetime.datetime.now() + datetime.timedelta(seconds=ttl_sec)).isoformat()
    _write_json(SCENE_PATH, {
        "event_name": event_name,
        "event_type": event_type,
        "temp_scene_text": scene_text,
        "expires_at": expires_at,
        "status": "applied",
        "applied_at": _now_iso(),
    })

    en_count = len(glossary.get("en2zh", {}))
    zh_count = len(glossary.get("zh2en", {}))
    print(f"[event] 已应用: {event_name} (场景: {scene_text[:30]}..., 术语: en2zh={en_count}, zh2en={zh_count}, TTL={ttl_sec}s)",
          flush=True)


def clear_event():
    """清除事件，恢复用户手填场景。"""
    # 清除临时词库
    _write_json(GLOSSARY_PATH, {"en2zh": {}, "zh2en": {}})

    # 清除场景
    _write_json(SCENE_PATH, {"status": "idle", "event_name": "", "temp_scene_text": ""})

    # 恢复用户手填场景
    lang_cfg = _read_json(LANG_CONFIG_PATH)
    user_scene = lang_cfg.get("scene", "")

    # 写回 lang_config.json 的 scene（translate_stream 会热重载）
    lang_cfg["scene"] = user_scene
    _write_json(LANG_CONFIG_PATH, lang_cfg)

    print(f"[event] 已清除事件，恢复用户场景: '{user_scene}'", flush=True)


# ── 主入口 ───────────────────────────────────────────────────────────────────

def run_identify():
    """Phase 1: 识别事件。"""
    state = _read_json(STATE_PATH)
    request_id = str(uuid.uuid4())[:8]
    now_iso = _now_iso()

    # 采集上下文
    recent_texts = load_recent_texts()
    lang_cfg = _read_json(LANG_CONFIG_PATH)

    state["request_id"] = request_id
    state["updated_at"] = now_iso
    state["recent_asr_texts"] = recent_texts[-20:]

    # 检查 Hermes 可达性
    if not _hermes_available():
        state["status"] = "no_match"
        state["reason"] = "Hermes Agent 不可达"
        _write_json(STATE_PATH, state)
        print("[event] Hermes Agent 不可达", flush=True)
        return

    state["status"] = "running"
    state["progress"] = "正在连接 Hermes..."
    _write_json(STATE_PATH, state)

    # Phase 1: 识别
    state["progress"] = "正在识别事件..."
    _write_json(STATE_PATH, state)
    result = identify_event(state)

    state["status"] = result.get("status", "no_match")
    state["event_name"] = result.get("event_name", "")
    state["event_type"] = result.get("event_type", "other")
    state["confidence"] = result.get("confidence", 0)
    state["question_for_user"] = result.get("question_for_user", "")
    state["reason"] = result.get("reason", "")
    state["progress"] = ""
    state["updated_at"] = _now_iso()
    state["last_run_at"] = _now_iso()
    _write_json(STATE_PATH, state)

    # 高置信度：直接进入 Phase 2
    if result.get("status") == "high_confidence":
        print(f"[event] 高置信度 ({result['confidence']:.2f}): {result['event_name']}", flush=True)
        run_confirm()
    else:
        print(f"[event] {result.get('status')}: {result.get('event_name', '?')} ({result.get('confidence', 0):.2f})",
              flush=True)


def run_confirm():
    """Phase 2: 用户确认后生成术语表。"""
    state = _read_json(STATE_PATH)
    event_name = state.get("event_name", "")
    event_type = state.get("event_type", "other")

    if not event_name:
        print("[event] 无事件可确认", flush=True)
        return

    state["status"] = "running"
    state["progress"] = "正在生成术语表..."
    _write_json(STATE_PATH, state)

    recent_texts = load_recent_texts()
    result = generate_glossary(event_name, event_type, recent_texts)

    if not result:
        state["status"] = "no_match"
        state["reason"] = "术语表生成失败，请重试"
        state["progress"] = ""
        state["updated_at"] = _now_iso()
        _write_json(STATE_PATH, state)
        print("[event] 术语表生成失败", flush=True)
        return

    scene_text = result.get("temp_scene_text", f"当前内容为{event_name}，涉及相关专业术语和专有名词。")
    glossary = result.get("glossary", {})
    ttl_sec = result.get("ttl_sec", 7200)

    apply_event(event_name, event_type, scene_text, glossary, ttl_sec)

    # 更新 state
    state["status"] = "applied"
    state["temp_scene_text"] = scene_text
    state["ttl_sec"] = ttl_sec
    state["reason"] = ""
    state["progress"] = ""
    state["updated_at"] = _now_iso()
    _write_json(STATE_PATH, state)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Event Context Agent")
    parser.add_argument("--confirm", action="store_true", help="用户确认后生成术语表")
    parser.add_argument("--clear", action="store_true", help="清除事件，恢复用户场景")
    args = parser.parse_args()

    if args.clear:
        clear_event()
    elif args.confirm:
        run_confirm()
    else:
        run_identify()


if __name__ == "__main__":
    main()
