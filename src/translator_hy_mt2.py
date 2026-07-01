#!/usr/bin/env python3
"""翻译引擎：封装翻译模型的加载、prompt 构造、推理与后处理。

通过 HTTP 后端调用 vLLM / LM Studio (OpenAI 兼容 API)。
多 URL fallback 链由 VLLMBackend 内部处理，详见 _pick_healthy()。

增量翻译接口：
  classify_update(old, new) → {mode, delta_source_text, shared_prefix_len}
  detect_glossary_hits(text, glossary) → {en: zh, ...}
  HyMT2Translator.translate_delta(delta_text, ...) → (zh, ms, meta)
"""

import json
import os
import re
import time
from typing import Callable

# ── 术语表 ──────────────────────────────────────────────────────────────────────

# 缓存按 path 索引,值 (mtime, content)。单进程内多 translator 实例
# 各自访问不同 glossary.json 时,缓存互不干扰;文件被外部修改后
# mtime 变化触发重读。避免模块级单例 (C3)。
_GLOSSARY_CACHE: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}


def _load_glossary(path: str | None) -> dict[str, dict[str, str]]:
    """加载双向词库。返回 {"en2zh": {...}, "zh2en": {...}}。
    缓存按 path key,文件 mtime 变化时重新读盘 (用户改 glossary.json
    不重启进程也能生效 — 之前的模块级单例 + 无 mtime 检查会让改动
    看不到)。
    """
    if not path:
        return {"en2zh": {}, "zh2en": {}}

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        cached = _GLOSSARY_CACHE.get(path)
        return cached[1] if cached else {"en2zh": {}, "zh2en": {}}

    cached = _GLOSSARY_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        content = {
            "en2zh": dict(data.get("en2zh", {})),
            "zh2en": dict(data.get("zh2en", {})),
        }
    except (FileNotFoundError, json.JSONDecodeError):
        content = {"en2zh": {}, "zh2en": {}}

    _GLOSSARY_CACHE[path] = (mtime, content)
    return content


def detect_glossary_hits(source_text: str, glossary: dict,
                         target_lang: str = "Simplified Chinese") -> dict[str, str]:
    """在 source_text 中匹配术语，返回命中的 {源: 译} 子集。

    目前仅支持 en/zh 词库。其他语言跳过词库匹配。
    """
    if not glossary or not source_text:
        return {}

    # 检测源语言，决定用哪个词库
    src_is_zh = detect_language(source_text) == "zh"

    if target_lang in ("Simplified Chinese", "Traditional Chinese", "Chinese"):
        # 源→中文：用 en2zh 词库（源为英文时）
        if src_is_zh:
            return {}  # 中文→中文不需要翻译
        entries = glossary.get("en2zh", {}) if isinstance(glossary.get("en2zh"), dict) else {}
        if not entries and isinstance(glossary, dict) and "en2zh" not in glossary:
            entries = glossary
        hits = {}
        for en, zh in entries.items():
            pattern = re.compile(r'\b' + re.escape(en) + r'\b', re.IGNORECASE)
            if pattern.search(source_text):
                hits[en] = zh
        return hits
    elif target_lang == "English":
        # 源→英文：用 zh2en 词库（源为中文时）
        if not src_is_zh:
            return {}  # 英文→英文不需要翻译
        entries = glossary.get("zh2en", {}) if isinstance(glossary.get("zh2en"), dict) else {}
        if not entries and isinstance(glossary, dict) and "en2zh" not in glossary:
            entries = glossary
        hits = {}
        for zh, en in entries.items():
            if len(zh) >= 2 and zh in source_text:
                hits[zh] = en
        return hits
    else:
        # 其他语言：暂不支持词库匹配
        return {}


# ── 增量分类 ────────────────────────────────────────────────────────────────────

def classify_update(old_text: str, new_text: str) -> dict:
    """判定 new_text 相对于 old_text 的更新模式。

    返回:
      mode: "append_only" | "small_rewrite_tail" | "reset_full"
      delta_source_text: 需要翻译的增量文本
      shared_prefix_len: 共享前缀长度
    """
    if not old_text or not new_text:
        return {
            "mode": "reset_full",
            "delta_source_text": new_text,
            "shared_prefix_len": 0,
        }

    if new_text == old_text:
        return {
            "mode": "append_only",
            "delta_source_text": "",
            "shared_prefix_len": len(old_text),
        }

    # 字符逐个比较找最长公共前缀（O(min(n,m))，比 SequenceMatcher 可靠）
    prefix_len = 0
    for a, b in zip(old_text, new_text):
        if a == b:
            prefix_len += 1
        else:
            break

    if prefix_len == len(old_text):
        # 纯追加：new 以 old 为前缀
        return {
            "mode": "append_only",
            "delta_source_text": new_text[prefix_len:],
            "shared_prefix_len": prefix_len,
        }

    # 计算前缀占比
    prefix_ratio = prefix_len / max(len(old_text), 1)

    # 尾部小改写：前面 70%+ 一致
    # 注意：如果 new 比 old 短（尾部删减），delta 为空但文本确实变了 → reset_full
    delta = new_text[prefix_len:]
    if prefix_ratio >= 0.7 and delta:
        return {
            "mode": "small_rewrite_tail",
            "delta_source_text": delta,
            "shared_prefix_len": prefix_len,
        }

    # 差异太大（或尾部被删减），整段重翻
    return {
        "mode": "reset_full",
        "delta_source_text": new_text,
        "shared_prefix_len": 0,
    }


# ── Prompt 构造 ─────────────────────────────────────────────────────────────────

import re as _re

def detect_language(text: str) -> str:
    """检测文本主要语言：'zh'（含中文字符）或 'en'。"""
    zh_chars = len(_re.findall(r'[一-鿿㐀-䶿]', text))
    return "zh" if zh_chars > len(text) * 0.15 else "en"


# ── 多语言 prompt 系统 ────────────────────────────────────────────────────────

# 官方 prompt 模板（无 system prompt，Hy-MT2 不需要）
# 中文 prompt：将以下文本翻译为 {target_lang}，注意只需要输出翻译后的结果，不要额外解释
# 英文 prompt：Translate the following text into {target_lang}. Note that you should only output the translated result without any additional explanation

# 全局场景描述（用户通过 overlay 设置，注入到 prompt）
_scene_context: str = ""


def set_scene_context(scene: str):
    """设置翻译场景描述（由 overlay 调用）。"""
    global _scene_context
    _scene_context = scene.strip()


def get_scene_context() -> str:
    return _scene_context


# Hy-MT2 官方推荐参数
_OFFICIAL_PARAMS = {
    "temperature": 0.7,
    "top_p": 0.6,
    "top_k": 20,
    "repetition_penalty": 1.05,
}


# ── 语言全名映射 ────────────────────────────────────────────────────────────

_LANG_NAME_ZH = {
    "English": "英语", "Simplified Chinese": "简体中文",
    "Traditional Chinese": "繁体中文", "Chinese": "简体中文",
    "Japanese": "日语",
    "Korean": "韩语", "French": "法语", "German": "德语",
    "Spanish": "西班牙语", "Italian": "意大利语", "Portuguese": "葡萄牙语",
    "Russian": "俄语", "Arabic": "阿拉伯语", "Thai": "泰语",
    "Vietnamese": "越南语", "Turkish": "土耳其语", "Hindi": "印地语",
    "Indonesian": "印尼语", "Malay": "马来语", "Filipino": "菲律宾语",
}

_LANG_NAME_EN = {
    "English": "English", "Simplified Chinese": "Simplified Chinese",
    "Traditional Chinese": "Traditional Chinese", "Chinese": "Simplified Chinese",
    "Japanese": "Japanese",
    "Korean": "Korean", "French": "French", "German": "German",
    "Spanish": "Spanish", "Italian": "Italian", "Portuguese": "Portuguese",
}


def resolve_prompt_language(source_text: str, target_lang: str) -> str:
    """决定 prompt 语言：zh↔en 用中文 prompt，其他用英文。"""
    src_lang = detect_language(source_text)
    if src_lang == "zh" or target_lang in ("Simplified Chinese", "Traditional Chinese", "Chinese"):
        return "zh"
    if src_lang == "en" or target_lang == "English":
        return "en"
    return "en"


def _lang_name(target_lang: str, prompt_lang: str) -> str:
    """根据 prompt 语言返回目标语言全名。"""
    if prompt_lang == "zh":
        return _LANG_NAME_ZH.get(target_lang, target_lang)
    return _LANG_NAME_EN.get(target_lang, target_lang)


# ── 术语表注入 ────────────────────────────────────────────────────────────────

def build_glossary_block(hits: dict[str, str], prompt_lang: str) -> str:
    """术语表注入：官方示例格式。"""
    if not hits:
        return ""
    lines = []
    if prompt_lang == "zh":
        lines.append("参考下面的翻译：")
        for src, tgt in hits.items():
            lines.append(f"{src} 翻译成 {tgt}")
    else:
        lines.append("Reference the following translations:")
        for src, tgt in hits.items():
            lines.append(f"{src} translates to {tgt}")
    return "\n".join(lines)


# ── 上下文规范化 ──────────────────────────────────────────────────────────────

def normalize_context_pairs(context_pairs) -> list[dict]:
    """规范化上下文配对：支持新格式 [{"source": ..., "target": ...}]
    和旧格式 list[str] 降级兼容。"""
    if not context_pairs:
        return []
    cleaned = []
    for item in context_pairs[-2:]:
        if isinstance(item, dict):
            src = (item.get("source") or "").strip()
            tgt = (item.get("target") or "").strip()
        else:
            # 旧格式兼容：list[str] → 只有 target
            src = ""
            tgt = str(item).strip()
        if src or tgt:
            cleaned.append({"source": src, "target": tgt})
    return cleaned


# ── 全量翻译 prompt ───────────────────────────────────────────────────────────

def build_messages(source_text: str, glossary_hits: dict[str, str] | None = None,
                   context=None,
                   target_lang: str = "Simplified Chinese",
                   extra_instruction: str = "") -> list[dict]:
    """构造 ChatML messages（无 system prompt）。

    context: 支持新格式 [{"source": ..., "target": ...}] 和旧格式 list[str]。
    extra_instruction: 额外约束指令（如重试时的强约束），不污染 source_text。
    """
    prompt_lang = resolve_prompt_language(source_text, target_lang)
    lang_name = _lang_name(target_lang, prompt_lang)
    glossary_block = build_glossary_block(glossary_hits or {}, prompt_lang)
    ctx = normalize_context_pairs(context)
    scene = _scene_context
    parts = []

    if prompt_lang == "zh":
        if glossary_block:
            parts.append(glossary_block)
            parts.append("")
        if ctx:
            parts.append("下面的背景信息仅供理解上下文，不要复述，不要续写，不要照抄。")
            for i, item in enumerate(ctx, 1):
                if item["source"]:
                    parts.append(f"第{i}句原文：{item['source']}")
                if item["target"]:
                    parts.append(f"第{i}句译文：{item['target']}")
            parts.append("")
        if extra_instruction:
            parts.append(extra_instruction)
        parts.append(f"请将以下文本翻译为{lang_name}。")
        parts.append("只输出翻译结果，不要解释，不要添加额外内容。不要复述背景信息。")
        parts.append("")
        parts.append(source_text)
    else:
        if glossary_block:
            parts.append(glossary_block)
            parts.append("")
        if ctx:
            parts.append("The following background is for disambiguation only. Do not repeat, continue, or copy it.")
            for i, item in enumerate(ctx, 1):
                if item["source"]:
                    parts.append(f"Previous source {i}: {item['source']}")
                if item["target"]:
                    parts.append(f"Previous translation {i}: {item['target']}")
            parts.append("")
        if extra_instruction:
            parts.append(extra_instruction)
        parts.append(f"Translate the following text into {lang_name}.")
        parts.append("Output only the translated result without explanation or extra text. Do not repeat background info.")
        parts.append("")
        parts.append(source_text)

    return [{"role": "user", "content": "\n".join(parts)}]


# ── 增量翻译 prompt ───────────────────────────────────────────────────────────

def build_delta_messages(delta_text: str,
                         prev_source_tail: str = "",
                         prev_target_tail: str = "",
                         glossary_hits: dict[str, str] | None = None,
                         target_lang: str = "Simplified Chinese",
                         extra_instruction: str = "") -> list[dict]:
    """增量翻译 prompt（无 system prompt）。"""
    prompt_lang = resolve_prompt_language(delta_text, target_lang)
    lang_name = _lang_name(target_lang, prompt_lang)
    glossary_block = build_glossary_block(glossary_hits or {}, prompt_lang)
    scene = _scene_context
    parts = []

    if prompt_lang == "zh":
        if glossary_block:
            parts.append(glossary_block)
            parts.append("")
        if prev_source_tail or prev_target_tail:
            parts.append("以下前文仅供衔接参考，不要重复，不要续写。")
            if prev_source_tail:
                parts.append(f"前文原文：...{prev_source_tail}")
            if prev_target_tail:
                parts.append(f"前文译文：...{prev_target_tail}")
            parts.append("")
        if extra_instruction:
            parts.append(extra_instruction)
        parts.append(f"请将以下新增文本翻译为{lang_name}。")
        parts.append("只输出这次新增部分对应的译文，不要解释。不要复述背景信息。")
        parts.append("")
        parts.append(delta_text)
    else:
        if glossary_block:
            parts.append(glossary_block)
            parts.append("")
        if prev_source_tail or prev_target_tail:
            parts.append("The previous context is for reference only. Do not repeat or continue it.")
            if prev_source_tail:
                parts.append(f"Previous source: ...{prev_source_tail}")
            if prev_target_tail:
                parts.append(f"Previous translation: ...{prev_target_tail}")
            parts.append("")
        if extra_instruction:
            parts.append(extra_instruction)
        parts.append(f"Translate the following new text into {lang_name}.")
        parts.append("Output only the translation of the new part without explanation. Do not repeat background info.")
        parts.append("")
        parts.append(delta_text)

    return [{"role": "user", "content": "\n".join(parts)}]


# ── 输出校验 ───────────────────────────────────────────────────────────────────

_EXPLANATION_PREFIXES = [
    "here is the translation",
    "the translation is",
    "translated text",
    "翻译如下",
    "译文如下",
    "以下是翻译",
]

# _CONTEXT_ECHO_PATTERNS — 当前为空。
# 实测 1.8B / 7B 50 轮 (温度 0.7),5 步清洗全部 0 命中。
# 原列表 (10 条 "最近上下文" "仅供参考" "英文前文" 等) 是早期 prompt
# 不干净时的过度防御 — 当前 prompt 改用 "前文原文" "背景信息" 等不同措辞,
# 模型不再泄漏这些短语。
# 函数保留,列表清空 — 未来 prompt 退化 (如改成 "以下是参考") 1.8B 可能
# 重新泄漏,届时把模式加回来。
_CONTEXT_ECHO_PATTERNS: list[str] = []

# prompt 标签泄漏 — 当前为空。理由同上。
# 原列表 (14 条 "待翻译文本" "source text" "只输出翻译" 等) 是 vLLM 0.x
# 早期版本的过度防御。当前 prompt 已经显式说 "只输出翻译结果"。
_PROMPT_LABEL_LEAKS: list[str] = []


def _strip_prompt_leak(text: str) -> tuple[str, bool]:
    """清理输出开头的 prompt 标签泄漏。返回 (清理后文本, 是否命中)。"""
    out = text.strip()
    original = out
    lower = out.lower()
    for leak in _PROMPT_LABEL_LEAKS:
        if lower.startswith(leak.lower()):
            out = out[len(leak):].strip()
            lower = out.lower()
            if out.startswith((":", "：", "\n")):
                out = out.lstrip(":\n ：")
            break
    return out, (out != original)


def _is_bad_output(text: str, target_lang: str = "Simplified Chinese") -> bool:
    """判断模型输出是否包含解释性前缀或明显异常。"""
    lower = text.strip().lower()
    for prefix in _EXPLANATION_PREFIXES:
        if lower.startswith(prefix):
            return True
    # 英中/中英专用校验：检查输出是否匹配目标语言的脚本
    text_stripped = text.strip()
    if text_stripped:
        has_zh = bool(re.search(r'[一-鿿]', text_stripped))
        has_en = bool(re.search(r'[a-zA-Z]', text_stripped))
        if target_lang in ("Simplified Chinese", "Traditional Chinese", "Chinese"):
            # 翻译成中文但输出全英文 → 异常
            if not has_zh:
                return True
        elif target_lang == "English":
            # 翻译成英文但输出全中文 → 异常
            if has_zh and not has_en:
                return True
        # 其他语言不做脚本校验（模型可能输出拉丁/非拉丁混合）
    return False


def _strip_context_echo(text: str) -> tuple[str, bool]:
    """剥离模型输出中的 context 回显前缀。返回 (清理后文本, 是否命中)。"""
    original = text.strip()
    lines = original.splitlines()
    if not lines:
        return text, False

    cleaned = []
    removed_any = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            removed_any = True
            continue
        if re.match(r'^\d+\.\s', stripped):
            removed_any = True
            continue
        if any(pat in stripped for pat in _CONTEXT_ECHO_PATTERNS):
            removed_any = True
            continue
        cleaned.append(stripped)

    if cleaned and removed_any:
        return "\n".join(cleaned), True
    if cleaned and len(cleaned) < len(lines):
        return "\n".join(cleaned), True
    return original, False


# ── 翻译输出防护（借鉴 livecaption）──────────────────────────────────────────

# _BOILERPLATE_RE — 当前为空。
# 实测 1.8B / 7B 50 轮 0 命中,原列表 (4 条正则 "根据背景信息" "以下是翻译"
# "here is the translation" 等) 都是早期 prompt 不干净时的过度防御。
# 当前 prompt 显式说 "只输出翻译结果" + "不要复述背景信息",模型听话。
# 函数保留,正则改为永远不匹配的空 pattern。
_BOILERPLATE_RE = re.compile("(?!x)x")  # negative lookahead — never matches
_QUOTE_PAIRS = {"“": "”", '"': '"', "「": "」", "'": "'"}


def _strip_boilerplate(zh: str) -> tuple[str, bool]:
    """去掉 LLM 输出的模板前缀。返回 (清理后文本, 是否命中)。"""
    out = zh.strip()
    m = _BOILERPLATE_RE.match(out)
    if not m:
        return out, False
    out = out[m.end():].strip()
    closing = _QUOTE_PAIRS.get(out[:1])
    if closing and len(out) >= 2 and out.endswith(closing):
        out = out[1:-1].strip()
    return out or zh.strip(), True


def _looks_like_context_echo(zh: str, prev_zh: str) -> bool:
    """检测"模型翻译了上下文而非原文"的情况：当前译文与上一句译文的
    字符 bigram 重叠 ≥ 45%，说明模型大概率在重复翻译上下文。
    连续同主题的真实翻译通常远低于这个阈值。"""
    if len(prev_zh) < 16:
        return False
    prev_bi = {prev_zh[i:i+2] for i in range(len(prev_zh) - 1)}
    zh_bi = {zh[i:i+2] for i in range(len(zh) - 1)}
    if not prev_bi:
        return False
    return len(prev_bi & zh_bi) >= 0.45 * len(prev_bi)


def _strip_scene_echo(text: str) -> tuple[str, bool]:
    """清理输出中的场景描述回显。"""
    out = text.strip()
    scene = _scene_context
    if not scene:
        return out, False
    # 场景文本出现在开头
    if out.startswith(scene):
        out = out[len(scene):].strip()
        return out, True
    # "翻译场景：" 前缀
    for prefix in ("翻译场景：", "翻译场景:"):
        if out.startswith(prefix):
            rest = out[len(prefix):]
            if rest.startswith(scene):
                rest = rest[len(scene):].strip()
            out = rest
            return out, True
    return out, False


def postprocess_translation(text: str) -> tuple[str, dict]:
    """统一后处理链。返回 (清理后文本, 观测指标)。

    指标包含: context_echo_removed, prompt_leak_removed, boilerplate_removed, scene_echo_removed
    """
    text0, scene_echo_removed = _strip_scene_echo(text)
    text1, context_echo_removed = _strip_context_echo(text0)
    text2, prompt_leak_removed = _strip_prompt_leak(text1)
    text3, boilerplate_removed = _strip_boilerplate(text2)
    text4 = _apply_glossary(text3)
    return text4, {
        "scene_echo_removed": scene_echo_removed,
        "context_echo_removed": context_echo_removed,
        "prompt_leak_removed": prompt_leak_removed,
        "boilerplate_removed": boilerplate_removed,
    }


# 短句不加上下文模板的词数阈值
CONTEXT_MIN_WORDS = 5
CONTEXT_MIN_ZH_CHARS = 6

# 上一次翻译结果，用于回声检测
_last_translation: str = ""


def should_use_context(source_text: str) -> bool:
    """判断是否启用上下文。中文按字符数，英文按词数。"""
    if not source_text or not source_text.strip():
        return False
    if detect_language(source_text) == "zh":
        zh_chars = len(_re.findall(r'[一-鿿㐀-䶿]', source_text))
        return zh_chars >= CONTEXT_MIN_ZH_CHARS
    return len(source_text.split()) >= CONTEXT_MIN_WORDS

_HARD_REPLACEMENTS = {
    "Dario Amadei": "Dario Amodei",
    "Dario Amade": "Dario Amodei",
    "Amadei": "Amodei",
    "Amade": "Amodei",
    "Open AI": "OpenAI",
    "Anthropic公司": "Anthropic",
}


def _apply_glossary(text: str) -> str:
    """对译文做强约束术语替换。"""
    for wrong, right in _HARD_REPLACEMENTS.items():
        text = text.replace(wrong, right)
    return text


# ── vLLM Backend ────────────────────────────────────────────────────────────────

class VLLMBackend:
    """OpenAI-compatible 翻译服务后端 (vLLM / LM Studio)。

    支持多个 base_url 按序 fallback:
    1. 第一个连得上的胜出（避免远端不可达时阻塞超时）
    2. 全部不可达才 raise
    这样默认配置"远程 LM Studio 优先 + 本机 LM Studio 兜底"
    不需要上层做 try/except 重试链。
    """

    # CLI / lang_config 链路负责传具体值
    def __init__(self, base_url: str | list[str] = "",
                 model_id: str = "",
                 model_map: dict[str, str] | None = None,
                 connect_timeout: float = 3.0):
        # 接受单 URL (str) 或多 URL (list) — 兼容旧 API
        urls = [base_url] if isinstance(base_url, str) else list(base_url)
        if not urls or not urls[0]:
            raise ValueError(
                "VLLMBackend: 至少需要一个 base_url,空串会让 vLLM 无 URL 可连。"
                "调用方应从 lang_config.json:translation_url / --vllm-url 显式传入"
            )
        self._connect_timeout = connect_timeout
        # 每个都解析成 IPv4 字面量(避免 v6 连接问题) + 去尾斜杠
        self._candidates = [self._resolve_ipv4(u.rstrip("/")) for u in urls]
        self.base_url = self._pick_healthy()  # 选中的那个
        # 默认 model_id (未在 model_map 中显式映射的 URL 都用它)
        # 空串 = 不在请求体里塞 model 字段,由 vLLM 服务端选默认
        self.model_id = model_id
        # per-URL model 覆盖 — key 跟 self._candidates 同格式(IPv4 + 去尾
        # 斜杠),确保 lookup 命中。fallback 链允许远端和本机用不同模型
        # (比如远端 32B,本机 1.8B)
        self._model_per_url: dict[str, str] = {
            self._resolve_ipv4(k.rstrip("/")): v
            for k, v in (model_map or {}).items()
        }
        # 当前 base_url 实际用的 model_id (生成请求时用)
        self._active_model_id = self._model_per_url.get(self.base_url, model_id)

    def _pick_healthy(self) -> str:
        """按顺序探活,首个健康 URL 胜出。全失败 raise 包含每个 URL 的具体错误。"""
        errors: list[str] = []
        for url in self._candidates:
            try:
                self._check_health(url, timeout=self._connect_timeout)
                return url
            except Exception as e:
                errors.append(f"  {url}: {e}")
                continue
        raise RuntimeError(
            f"VLLMBackend: 所有候选 URL 都不可达 ({len(self._candidates)} 个):\n"
            + "\n".join(errors)
        )

    @staticmethod
    def _resolve_ipv4(url: str) -> str:
        """将 URL 中的主机名解析为 IPv4 地址，避免 IPv6 连接问题。"""
        import socket
        from urllib.parse import urlparse, urlunparse
        try:
            parsed = urlparse(url)
            ip = socket.getaddrinfo(parsed.hostname, parsed.port or 80,
                                    socket.AF_INET)[0][4][0]
            return urlunparse(parsed._replace(
                netloc=f"{ip}:{parsed.port}" if parsed.port else ip
            ))
        except Exception:
            return url  # 解析失败则用原始 URL

    def _check_health(self, base_url: str, timeout: float = 3.0):
        """探测单个 URL 的健康状态。失败抛异常。

        用 GET /v1/models 探活,而不是 /health — 原因:
        - /health 只测 server alive,不测模型已加载
        - vLLM 刚启动还没加载完模型时 /health 也返回 200,
          但 /v1/chat/completions 会立即 503/timeout
        - /v1/models 返回的 data 列表非空 = 模型真的可用
          (LM Studio / vLLM / Ollama 都返回 OpenAI 兼容格式)
        """
        import urllib.request
        try:
            req = urllib.request.Request(f"{base_url}/v1/models", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                # 解析响应,要求 data 列表非空
                body = json.loads(resp.read())
                data = body.get("data", [])
                if not data:
                    raise RuntimeError("/v1/models 返回空列表(模型未加载)")
                print(f"[translator] 翻译服务已连接 ({base_url}, {len(data)} 模型可用)。",
                      flush=True)
        except Exception as e:
            raise RuntimeError(f"健康检查失败 ({base_url}): {e}") from e

    def generate(self, messages: list[dict], temperature: float = 0.7,
                 top_p: float = 0.6, top_k: int = 20,
                 repetition_penalty: float = 1.05,
                 max_new_tokens: int = 80) -> str:
        """非流式生成。失败时自动重选候选 URL (M2: 远端中途 OOM
        重启时 session 不会翻车) — 重试一次仍失败再 raise。
        """
        import urllib.request
        # 第一次试 self.base_url,失败时 _pick_healthy() 选下一个健康的
        for attempt in range(2):
            try:
                # 未配置 model_id (空串) → 不在请求体里塞 model 字段,
                # 让 vLLM 服务端选默认 (LM Studio/vLLM 都有"first loaded
                # model"兜底)。硬塞 "" 会让 vLLM 返回 400 "model not found"。
                body: dict = {
                    "messages": messages,
                    "temperature": temperature,
                    "top_p": top_p,
                    "top_k": top_k,
                    "repetition_penalty": repetition_penalty,
                    "max_tokens": max_new_tokens,
                }
                if self._active_model_id:
                    body["model"] = self._active_model_id
                payload = json.dumps(body).encode()
                req = urllib.request.Request(
                    f"{self.base_url}/v1/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    result = json.loads(resp.read())
                # 检查 vLLM 返回的 error 字段(OOM / prompt 过长)
                if "error" in result:
                    raise RuntimeError(f"vLLM 错误: {result['error']}")
                return result["choices"][0]["message"]["content"].strip()
            except (urllib.error.URLError, RuntimeError, ConnectionError) as e:
                if attempt == 1:
                    raise
                print(f"[translator] {self.base_url} 失败 ({e}),重选候选...",
                      flush=True)
                self.base_url = self._pick_healthy()
                self._active_model_id = self._model_per_url.get(
                    self.base_url, self.model_id)

    def generate_streaming(self, messages: list[dict],
                           on_token: Callable[[str, str], None],
                           temperature: float = 0.7,
                           top_p: float = 0.6, top_k: int = 20,
                           repetition_penalty: float = 1.05,
                           max_new_tokens: int = 80) -> str:
        """Generate with SSE streaming from vLLM.

        on_token receives (piece, full_so_far) per token. The first
        argument is the new delta.content from the SSE chunk; the
        second is the cumulative text including this piece. Callers
        that only want the running total can ignore the first
        argument with `lambda piece, full: handler(full)`.
        """
        import urllib.request
        # 同 _generate 的处理: model_id 空就跳过 model 字段
        body: dict = {
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "repetition_penalty": repetition_penalty,
            "max_tokens": max_new_tokens,
            "stream": True,
        }
        if self._active_model_id:
            body["model"] = self._active_model_id
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        full = ""
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data: "):
                    continue
                data = line[len("data: "):]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    # vLLM/LM Studio 在 OOM / prompt 过长 / 模型下架时会
                    # 返回 {"error": {...}} (没有 choices 字段)。原来代码
                    # 被 except KeyError 吞掉,变成"流继续,UI 收到空字符串"
                    # 然后被 _is_bad_output("") 当合法翻译返回。
                    if "error" in chunk:
                        raise RuntimeError(f"vLLM 错误: {chunk['error']}")
                    delta = chunk["choices"][0].get("delta", {})
                    piece = delta.get("content", "")
                    if piece:
                        full += piece
                        on_token(piece, full)  # (piece, cumulative)
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
        return full.strip()


# ── 统一 Translator 接口 ────────────────────────────────────────────────────────

class HyMT2Translator:

    # 默认值都是 "" — 配置由 translate_stream.py 从 lang_config / CLI注入
    def __init__(self, model_id: str = "",
                 vllm_url: str | list[str] = "",
                 model_map: dict[str, str] | None = None,
                 glossary_path: str | None = None,
                 temperature: float = 0.7,
                 top_p: float = 0.6,
                 top_k: int = 20,
                 repetition_penalty: float = 1.05,
                 max_new_tokens: int = 80):
        self.glossary_path = glossary_path
        self.glossary = _load_glossary(glossary_path)
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.repetition_penalty = repetition_penalty
        self.max_new_tokens = max_new_tokens
        # vllm_url / model_id / model_map 全部透传给 VLLMBackend
        self._backend = VLLMBackend(vllm_url, model_id=model_id, model_map=model_map)

    def _generate(self, messages: list[dict]) -> tuple[str, float]:
        """底层推理，返回 (raw_output, elapsed_ms)。"""
        t0 = time.monotonic()
        raw = self._backend.generate(
            messages,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            repetition_penalty=self.repetition_penalty,
            max_new_tokens=self.max_new_tokens,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        raw, _ = _strip_context_echo(raw.strip())
        return raw, elapsed_ms

    def _generate_streaming(self, messages: list[dict],
                            on_token: Callable[[str, str], None]) -> tuple[str, float]:
        """流式推理，on_token(piece, full_so_far) 随每个 token 触发。

        第一参数是 SSE / TextStreamer 给的 token piece，第二参数
        是累计全文。调用方可以忽略 piece（lambda piece, full:
        handler(full)），也可以利用 piece 做细粒度流式 UI 更新。
        返回 (raw_output, elapsed_ms)。
        """
        t0 = time.monotonic()
        raw = self._backend.generate_streaming(
            messages,
            on_token=on_token,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            repetition_penalty=self.repetition_penalty,
            max_new_tokens=self.max_new_tokens,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        raw, _ = _strip_context_echo(raw.strip())
        return raw, elapsed_ms

    def _retry_if_bad(self, raw: str, source_text: str,
                      glossary_hits: dict, elapsed_ms: float,
                      is_delta: bool = False,
                      prev_source_tail: str = "",
                      prev_target_tail: str = "",
                      target_lang: str = "Simplified Chinese") -> tuple[str, float, bool]:
        """如果输出异常，重试一次。返回 (result, elapsed_ms, retried)。"""
        if not _is_bad_output(raw, target_lang):
            return raw, elapsed_ms, False

        # 用 extra_instruction 加强约束，不污染 source_text
        if target_lang == "English":
            retry_instruction = "Only output the English translation. No explanation."
        elif target_lang in ("Simplified Chinese", "Traditional Chinese", "Chinese"):
            retry_instruction = "只输出中文翻译，不要任何解释。"
        else:
            retry_instruction = f"Only output the {target_lang} translation. No explanation."

        if is_delta:
            retry_messages = build_delta_messages(
                source_text,
                prev_source_tail=prev_source_tail,
                prev_target_tail=prev_target_tail,
                glossary_hits=glossary_hits,
                target_lang=target_lang,
                extra_instruction=retry_instruction,
            )
        else:
            retry_messages = build_messages(
                source_text,
                glossary_hits=glossary_hits,
                target_lang=target_lang,
                extra_instruction=retry_instruction,
            )

        t0 = time.monotonic()
        raw2 = self._backend.generate(
            retry_messages,
            temperature=max(0.1, self.temperature - 0.2),
            top_p=self.top_p,
            top_k=self.top_k,
            repetition_penalty=self.repetition_penalty,
            max_new_tokens=self.max_new_tokens,
        )
        retry_ms = (time.monotonic() - t0) * 1000
        raw2, _ = _strip_context_echo(raw2.strip())
        return raw2, elapsed_ms + retry_ms, True

    # ── 全量翻译（保留兼容） ──

    def translate(self, source_text: str,
                  context: list[dict] | list[str] | None = None,
                  target_lang: str = "Simplified Chinese") -> tuple[str, float]:
        """全量翻译，返回 (译文, 耗时ms)。"""
        global _last_translation
        hits = detect_glossary_hits(source_text, self.glossary, target_lang)
        effective_ctx = context if should_use_context(source_text) else None
        messages = build_messages(source_text, glossary_hits=hits, context=effective_ctx, target_lang=target_lang)
        raw, elapsed_ms = self._generate(messages)
        initial_bad = _is_bad_output(raw, target_lang)
        raw, elapsed_ms, retried = self._retry_if_bad(raw, source_text, hits, elapsed_ms, target_lang=target_lang)
        result, post_meta = postprocess_translation(raw.strip())
        if effective_ctx and _looks_like_context_echo(result, _last_translation):
            messages_nc = build_messages(source_text, glossary_hits=hits, context=None, target_lang=target_lang)
            raw2, elapsed2 = self._generate(messages_nc)
            raw2, elapsed2, _ = self._retry_if_bad(raw2, source_text, hits, elapsed2, target_lang=target_lang)
            result, post_meta = postprocess_translation(raw2.strip())
            elapsed_ms += elapsed2
        _last_translation = result
        print(f"[translate] {detect_language(source_text)}→{target_lang} "
              f"bad={initial_bad} retry={retried} "
              f"leak={post_meta['prompt_leak_removed']} "
              f"boiler={post_meta['boilerplate_removed']} "
              f"echo={post_meta['context_echo_removed']} "
              f"{elapsed_ms:.0f}ms", flush=True)
        return result, elapsed_ms

    def translate_streaming(self, source_text: str,
                            on_token: Callable[[str, str], None],
                            context: list[dict] | list[str] | None = None,
                            target_lang: str = "Simplified Chinese") -> tuple[str, float]:
        """流式全量翻译。

        on_token 接收 (piece, full_so_far)：
        - piece: 新增的 token 片段（delta.content / TextStreamer yield）
        - full_so_far: 累计全文（包含本 piece）
        调用方如只需累计全文可写 lambda piece, full: handler(full)。
        """
        global _last_translation
        hits = detect_glossary_hits(source_text, self.glossary, target_lang)
        effective_ctx = context if should_use_context(source_text) else None
        messages = build_messages(source_text, glossary_hits=hits, context=effective_ctx, target_lang=target_lang)
        raw, elapsed_ms = self._generate_streaming(messages, on_token)
        initial_bad = _is_bad_output(raw, target_lang)
        raw, elapsed_ms, retried = self._retry_if_bad(raw, source_text, hits, elapsed_ms, target_lang=target_lang)
        if retried:
            on_token(postprocess_translation(raw.strip())[0])
        result, post_meta = postprocess_translation(raw.strip())
        if effective_ctx and _looks_like_context_echo(result, _last_translation):
            messages_nc = build_messages(source_text, glossary_hits=hits, context=None, target_lang=target_lang)
            raw2, elapsed2 = self._generate_streaming(messages_nc, on_token)
            raw2, elapsed2, retried2 = self._retry_if_bad(raw2, source_text, hits, elapsed2, target_lang=target_lang)
            if retried2:
                on_token(postprocess_translation(raw2.strip())[0])
            result, post_meta = postprocess_translation(raw2.strip())
            elapsed_ms += elapsed2
        _last_translation = result
        print(f"[stream] {detect_language(source_text)}→{target_lang} "
              f"bad={initial_bad} retry={retried} "
              f"leak={post_meta['prompt_leak_removed']} "
              f"boiler={post_meta['boilerplate_removed']} "
              f"echo={post_meta['context_echo_removed']} "
              f"{elapsed_ms:.0f}ms", flush=True)
        return result, elapsed_ms

    def translate_delta(self, delta_source_text: str,
                        prev_source_tail: str = "",
                        prev_target_tail: str = "",
                        target_lang: str = "Simplified Chinese") -> tuple[str, float, dict]:
        """增量翻译：只翻译新增尾部文本。

        返回 (译文, 耗时ms, meta_dict)。
        """
        hits = detect_glossary_hits(delta_source_text, self.glossary, target_lang)
        messages = build_delta_messages(
            delta_source_text,
            prev_source_tail=prev_source_tail,
            prev_target_tail=prev_target_tail,
            glossary_hits=hits,
            target_lang=target_lang,
        )
        raw, elapsed_ms = self._generate(messages)
        initial_bad = _is_bad_output(raw, target_lang)
        raw, elapsed_ms, retried = self._retry_if_bad(
            raw, delta_source_text, hits, elapsed_ms,
            is_delta=True,
            prev_source_tail=prev_source_tail,
            prev_target_tail=prev_target_tail,
            target_lang=target_lang,
        )
        result, post_meta = postprocess_translation(raw.strip())
        meta = {
            "target_lang": target_lang,
            "source_lang": detect_language(delta_source_text),
            "is_delta": True,
            "glossary_hits": list(hits.keys()),
            "initial_bad_output": initial_bad,
            "retried": retried,
            "final_bad_output": _is_bad_output(result, target_lang),
            "elapsed_ms": round(elapsed_ms, 1),
            **post_meta,
        }
        return result, elapsed_ms, meta

    def translate_delta_streaming(self, delta_source_text: str,
                                  prev_source_tail: str = "",
                                  prev_target_tail: str = "",
                                  on_token: Callable[[str, str], None] = lambda piece, full: None,
                                  target_lang: str = "Simplified Chinese") -> tuple[str, float, dict]:
        """增量翻译的流式版本。

        与 translate_delta 走同一 prompt 构造 + retry 逻辑，但底层
        inference 走 backend 的 generate_streaming —— on_token 收到
        (piece, full_so_far)，调用方按需利用 piece 触发 UI 更新。

        meta 字段多了 is_streaming=True 标记，供调用方区分流式 vs
        整段 partial 事件。
        """
        hits = detect_glossary_hits(delta_source_text, self.glossary, target_lang)
        messages = build_delta_messages(
            delta_source_text,
            prev_source_tail=prev_source_tail,
            prev_target_tail=prev_target_tail,
            glossary_hits=hits,
            target_lang=target_lang,
        )
        raw, elapsed_ms = self._generate_streaming(messages, on_token)
        initial_bad = _is_bad_output(raw, target_lang)

        # Streaming 流式推理时，retry 走非流式路径（已经有了一个
        # 完整响应，retry 时如果再做流式可能让 UI 闪烁）。rebuild
        # on_token 走非流式 path：把 on_token 重新包成只接受
        # cumulative 形式。
        def _on_token_full_only(_piece: str, full: str) -> None:
            on_token(full, full)

        # 注意：retry 是非流式 fallback，必须保证只触发一次
        # on_token（cumulative 形式），避免 piece + full 同时传给
        # 真实 on_token 导致重复。
        raw2, elapsed_ms2, retried = self._retry_if_bad(
            raw, delta_source_text, hits, elapsed_ms,
            is_delta=True,
            prev_source_tail=prev_source_tail,
            prev_target_tail=prev_target_tail,
            target_lang=target_lang,
        )
        result, post_meta = postprocess_translation(raw2.strip())
        meta = {
            "target_lang": target_lang,
            "source_lang": detect_language(delta_source_text),
            "is_delta": True,
            "is_streaming": True,
            "glossary_hits": list(hits.keys()),
            "initial_bad_output": initial_bad,
            "retried": retried,
            "final_bad_output": _is_bad_output(result, target_lang),
            "elapsed_ms": round(elapsed_ms + elapsed_ms2, 1),
            **post_meta,
        }
        # retry 触发时把最终 cumulative 推一次到 on_token，让调用方
        # 看到更新（避免 retry 后 UI 还卡在 retry 前的最后一次）
        if retried:
            on_token(result, result)
        return result, elapsed_ms + elapsed_ms2, meta
