"""Target language normalization for Hy-MT2 multi-language translation.

Hy-MT2 支持 33 种语言。target_lang 使用语言全名（中文 prompt 用中文名，英文 prompt 用英文名）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Hy-MT2 官方支持的 33 种语言
_LANGUAGES = {
    "zh":    ("zh-cn",  "中文",    "Chinese"),
    "en":    ("en",     "英语",    "English"),
    "fr":    ("fr",     "法语",    "French"),
    "pt":    ("pt",     "葡萄牙语", "Portuguese"),
    "es":    ("es",     "西班牙语", "Spanish"),
    "ja":    ("ja",     "日语",    "Japanese"),
    "tr":    ("tr",     "土耳其语", "Turkish"),
    "ru":    ("ru",     "俄语",    "Russian"),
    "ar":    ("ar",     "阿拉伯语", "Arabic"),
    "ko":    ("ko",     "韩语",    "Korean"),
    "th":    ("th",     "泰语",    "Thai"),
    "it":    ("it",     "意大利语", "Italian"),
    "de":    ("de",     "德语",    "German"),
    "vi":    ("vi",     "越南语",  "Vietnamese"),
    "ms":    ("ms",     "马来语",  "Malay"),
    "id":    ("id",     "印尼语",  "Indonesian"),
    "tl":    ("tl",     "菲律宾语", "Filipino"),
    "hi":    ("hi",     "印地语",  "Hindi"),
    "zh-tw": ("zh-tw",  "繁体中文", "Traditional Chinese"),
    "pl":    ("pl",     "波兰语",  "Polish"),
    "cs":    ("cs",     "捷克语",  "Czech"),
    "nl":    ("nl",     "荷兰语",  "Dutch"),
    "km":    ("km",     "高棉语",  "Khmer"),
    "my":    ("my",     "缅甸语",  "Burmese"),
    "fa":    ("fa",     "波斯语",  "Persian"),
    "gu":    ("gu",     "古吉拉特语", "Gujarati"),
    "ur":    ("ur",     "乌尔都语", "Urdu"),
    "te":    ("te",     "泰卢固语", "Telugu"),
    "mr":    ("mr",     "马拉地语", "Marathi"),
    "he":    ("he",     "希伯来语", "Hebrew"),
    "bn":    ("bn",     "孟加拉语", "Bengali"),
    "ta":    ("ta",     "泰米尔语", "Tamil"),
    "uk":    ("uk",     "乌克兰语", "Ukrainian"),
    "bo":    ("bo",     "藏语",    "Tibetan"),
    "kk":    ("kk",     "哈萨克语", "Kazakh"),
    "mn":    ("mn",     "蒙古语",  "Mongolian"),
    "ug":    ("ug",     "维吾尔语", "Uyghur"),
    "yue":   ("yue",    "粤语",    "Cantonese"),
}

# 名称→code 映射（支持中英文名、code）
_NAME_TO_CODE = {}
for code, (tag, zh_name, en_name) in _LANGUAGES.items():
    _NAME_TO_CODE[code.lower()] = code
    _NAME_TO_CODE[tag.lower()] = code
    _NAME_TO_CODE[zh_name] = code
    _NAME_TO_CODE[en_name.lower()] = code
    _NAME_TO_CODE[en_name.lower().replace(" ", "")] = code


@dataclass(frozen=True)
class TargetLanguage:
    """规范化的目标语言。

    code: 语言标签 (e.g., "zh-cn", "ja")
    prompt_name: Hy-MT2 prompt 用的全名 (e.g., "日语", "Japanese")
    """
    code: str
    prompt_name: str


def normalize_target_language(value: str) -> TargetLanguage:
    """规范化目标语言：接受 code、中文名或英文名。

    >>> normalize_target_language("ja")
    TargetLanguage(code='ja', prompt_name='日语')
    >>> normalize_target_language("Japanese")
    TargetLanguage(code='ja', prompt_name='日语')
    """
    raw = value.strip()
    code = _NAME_TO_CODE.get(raw.lower()) or _NAME_TO_CODE.get(raw)
    if code is None:
        raise ValueError(
            f"Hy-MT2 不支持语言 '{raw}'。"
            f"支持: {', '.join(sorted(set(v[2] for v in _LANGUAGES.values())))}"
        )
    tag, zh_name, en_name = _LANGUAGES[code]
    return TargetLanguage(code=tag, prompt_name=en_name)


def normalize_target_language_en(value: str) -> TargetLanguage:
    """用英文 prompt_name 规范化。"""
    raw = value.strip()
    code = _NAME_TO_CODE.get(raw.lower()) or _NAME_TO_CODE.get(raw)
    if code is None:
        raise ValueError(f"Unsupported language: '{raw}'")
    tag, _, en_name = _LANGUAGES[code]
    return TargetLanguage(code=tag, prompt_name=en_name)


# UI 显示用的简短标签
UI_LABELS = {
    "auto": "自动",
    "zh-cn": "中",
    "zh-tw": "繁",
    "en": "EN",
    "ja": "JA",
    "ko": "KO",
    "fr": "FR",
    "de": "DE",
    "es": "ES",
    "it": "IT",
    "pt": "PT",
    "ru": "RU",
    "ar": "عر",
    "hi": "HI",
    "th": "TH",
    "vi": "VI",
    "nl": "NL",
    "pl": "PL",
    "tr": "TR",
    "id": "ID",
    "ms": "MS",
    "uk": "UA",
    "cs": "CS",
    "he": "עב",
    "tl": "TL",
    "km": "KM",
    "my": "MY",
    "fa": "FA",
    "bn": "BN",
    "ta": "த",
    "te": "TE",
    "mr": "MR",
    "gu": "GU",
    "ur": "UR",
    "bo": "藏",
    "kk": "KK",
    "mn": "MN",
    "ug": "UG",
    "yue": "粤",
}

# 按区域分组
LANGUAGE_GROUPS = [
    ("自动", ["auto"]),
    ("亚洲", ["zh-cn", "zh-tw", "yue", "ja", "ko", "hi", "vi", "th", "id", "ms",
              "tl", "km", "my", "ta", "te", "mr", "gu", "bn", "bo", "mn", "ug"]),
    ("欧洲", ["en", "fr", "de", "es", "it", "pt", "ru", "nl", "pl", "cs", "tr", "uk"]),
    ("中东", ["ar", "he", "fa", "ur", "kk"]),
]
