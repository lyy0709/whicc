"""模型状态共享文件：~/Library/Application Support/whicc/models/ 下的
模型清单 + 用户当前选择的模型 ID。

文件位置：/tmp/whicc-out/model_state.json
被 macui（Swift ModelState）写、被 whicc.py（这里）读。

设计原则（review #15 + 苹果最佳实践）：
- 只读自己关心的字段，不动其他键（与 lang_config.json 共享模式一致）
- 写盘用临时文件 + fsync + os.replace 原子替换，避免半写状态
- 文件不存在 / 解析失败时返回安全默认值，不抛异常
"""

import json
import os


# 当前项目主 ASR 模型。Nemotron 3.5 streaming 0.6B — 走 mlx-lm/MLX-Audio,
# 适配 Apple Silicon Metal。
DEFAULT_MODEL = "mlx-community/nemotron-3.5-asr-streaming-0.6b"

# 向上兼容：旧版 whicc 支持 Whisper / 早期模型 ID。本机 model_state.json
# 可能还残留这些字段（macui 写下来的）。新版读到后静默修正成 DEFAULT_MODEL。
DEPRECATED_MODELS = frozenset({
    "mlx-community/whisper-large-v3-turbo",
    "whisper-large-v3-turbo",
    "mlx-community/whisper-tiny",
    "whisper-tiny",
    "mlx-community/whisper-base",
    "whisper-base",
    "mlx-community/whisper-small",
    "whisper-small",
    "mlx-community/whisper-medium",
    "whisper-medium",
    "mlx-community/whisper-large-v3",
    "whisper-large-v3",
    "openai/whisper-large-v3",
    "openai/whisper-large-v3-turbo",
})


def read_model_state(path: str) -> dict:
    """读 model_state.json。文件不存在 / 解析失败时返回空 dict。

    current_model 为空或已废弃时，自动填 DEFAULT_MODEL。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    if isinstance(data, dict):
        cm = data.get("current_model", "")
        if not cm or cm in DEPRECATED_MODELS:
            data["current_model"] = DEFAULT_MODEL
    return data


def write_model_state(path: str, models_dir: str, current_model: str) -> None:
    """原子写 model_state.json（临时文件 + fsync + os.replace）。

    注意：写整个 dict 会覆盖文件已有字段。当前 model_state.json
    只有 models_dir + current_model 两个键，全量覆盖是安全的。
    """
    data = {
        "models_dir": models_dir,
        "current_model": current_model,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def resolve_model_id(model_state: dict) -> str:
    """从 model_state 拿 current_model，找不到/为空时回退到默认。"""
    cid = model_state.get("current_model", "")
    return cid if cid else DEFAULT_MODEL


def resolve_models_dir(model_state: dict, fallback: str) -> str:
    """从 model_state 拿 models_dir，找不到/为空时回退到 fallback。"""
    d = model_state.get("models_dir", "")
    return d if d else fallback
