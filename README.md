# whicc

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS 26+](https://img.shields.io/badge/Platform-macOS%2026%2B-blue.svg)](https://developer.apple.com/macos/)
[![Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-M1%2FM2%2FM3%2FM4-black.svg)](https://support.apple.com/en-us/116943)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/)
[![Swift 5.9+](https://img.shields.io/badge/Swift-5.9%2B-orange.svg)](https://www.swift.org/)

macOS 上的实时语音识别 + 翻译字幕浮岛。本地 ASR 模型转写系统音频 / 麦克风，自动语言检测，自学习术语库，33 种语言翻译，macOS 26 SwiftUI 字幕浮窗。

**一句话定位**：纯本地算力，识别 + 翻译多国语言，比 YouTube 直播的自动翻译**更准、更快、可定制**。

运行于 Apple Silicon (MLX)。打包成单 `.app` bundle，双击即可使用。

> 如果你是开发者，想从源码运行或自己打包 `.app`，看 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 为什么用 whicc

- **纯本地算力**：ASR（语音转文字）在本地 Apple Silicon 上跑，不依赖云端转写服务。音频不出本机。
- **多国语言**：识别 + 翻译覆盖 33 种语言（中英日德法西俄韩阿等），识别部分支持自动语言检测。
- **可联网的翻译后端**：本地 ASR 把音频变成文字后，翻译交给 LM Studio 。可以是本机的、可以是局域网的另一台机器、可以用LM Link在世界任何地方调用家里的闲置算力，把闲置设备盘活。

## 系统要求

- **macOS 26+**（MLX wheel 硬绑定 `macosx_26_0_arm64`）
- **Apple Silicon**（M1 / M2 / M3 / M4）
- **约 1.5 GB 本机磁盘**（首次启动会下载 2 个 ASR 模型：Nemotron 1.2GB 英文 + Qwen3 680MB 中文）
- **翻译服务**：需要一台跑 LM Studio / vLLM 的机器提供 OpenAI 兼容 HTTP 接口
  - 本机：装 [LM Studio](https://lmstudio.ai/)，加载 `tencent/Hy-MT2-1.8B-GGUF`
  - 局域网/外网：另一台机器装 LM Studio 加载更大的 `tencent/Hy-MT2-7B-GGUF`（这样你本机就不用装了，翻译质量会显著提升）

## 安装

1. 从 [Releases](../../releases) 下载 `whicc.app.zip`，解压
2. 把 `whicc.app` 拖拽到 `/Applications/`
3. 双击 `whicc.app` 启动，或终端跑：
   ```bash
   open /Applications/whicc.app
   ```

首次启动请进入设置面板（也可按 ⌘, 或点设置按钮打开）：

1. **下载 ASR 模型**：HuggingFace 自动下载（需要流畅的网络环境）。等显示完成后继续，如果下载失败，请删掉模型文件重新点击下载。
2. **配置翻译服务**：填 LM Studio 的URL（例 `http://192.168.1.10:1234`）

**停止**：⌘Q 关闭字幕窗体（自动 SIGTERM 后端进程）。万一卡住：

```bash
pkill -f whicc.py          # 杀 ASR 后端
pkill -f Applications/whicc # 杀整个 .app
```

## 架构

```
系统音频 / 麦克风
    ↓  16kHz PCM 段文件
whicc.py (ASR: Qwen3-ASR / Nemotron)  ← 本地 Apple Silicon 算力
    ↓  /tmp/whicc-out/events.jsonl         (partial + final 字幕事件)
translate_stream.py (Hy-MT2 翻译)  ← 本机 LM Studio / 局域网闲置算力 / 云端
    ↓  /tmp/whicc-out/translation_events.jsonl
whicc-macui (SwiftUI 字幕浮岛)
    ↓
glossary_refresher.py (jieba + Hermes Agent 自学习术语)
```

**关键设计点**：ASR（重）+ 翻译（轻，HTTP 调用）解耦。ASR 必须在本地（音频流不能出本机），翻译可以甩到任何有 HTTP 的机器上。

详细架构（含打包模式、BackendLauncher 进程树）见 [DEVELOPMENT.md](DEVELOPMENT.md#架构图-打包模式)。

日志位置和翻译观测指标见 [DEVELOPMENT.md → 日志与排查](DEVELOPMENT.md#日志与排查)。

## 翻译配置

### 启用翻译

第一次启动翻译默认是关闭的。在 macui 设置面板（齿轮按钮）里：

1. **服务配置 → 启用翻译**：打开开关
2. **主 URL**：vLLM / LM Studio 的 OpenAI 兼容地址（例：`http://192.168.1.10:1234`）
3. **备用 URL**：主节点不通时 fallback 的本机 LM Studio（例：`http://localhost:1234`）
4. **模型名**：远端 LM Studio 实际加载的模型 ID

配置文件 `lang_config.json`（写在 `/tmp/whicc-out/`）4 个键：

```json
{
  "translation_enabled": true,
  "translation_url": "http://192.168.1.10:1234",
  "translation_fallback_url": "http://localhost:1234",
  "translation_model": "hy-mt2-7b"
}
```

主 URL 不通时自动 fallback 到备用；所有候选都不可达时进程 loud-fail 退出并在字幕窗体提示"翻译服务不可用"。

### 目标语言

macui 工具栏有语言选择器（"自动" 按钮），可实时切换目标语言（有延迟），无需重启。
默认自动模式：英文 ↔ 中文互译。Hy-MT2 官方支持 33 种语言（`Japanese` / `German` / `Traditional Chinese` 等）。

### 翻译场景

设置面板里可以填写"翻译场景"，如 `AI访谈` / `NBA季后赛总决赛`。场景描述会注入到翻译 prompt 中，帮助模型理解上下文。

### 事件识别

设置面板里有"🎯 事件识别"功能，自动推断当前正在观看的事件（足球赛、发布会、财报会等），生成临时术语表注入翻译，不污染永久词库，2 小时后自动过期。

置信度 ≥80% 直接应用；55%-80% 弹窗询问确认。

## ASR 模型

模型文件**不打包进 app**——首次启动自动从 HuggingFace 下载（ML 项目的标准做法）。模型缓存在 `~/Library/Application Support/whicc/models/`。

| 模型 | 大小 | 流式 | 两遍校正 |
|------|------|------|----------|
| Qwen3-ASR-0.6B-4bit | 680MB | ✓ | ✗ |
| nemotron-3.5-asr-streaming-0.6b | 1.2GB | ✓ | ✓ |

通过设置界面「中文识别」/「非中文识别」槽位切换。

### 中英文自动切换

默认以 Nemotron 启动。当检测到中文内容（CJK 字符占比 > 30%）时，自动切换到 Qwen3（中文 / 方言识别更好）；切回英文时自动恢复 Nemotron。

标题栏左侧会显示当前 ASR 模型切换状态（3 秒自动消失）。

## 字幕窗体

- **位置**：屏幕顶部居中悬浮
- **自动隐藏**：非焦点 / 非 hover 时整组 `opacity(0)` + 不响应点击
- **双语字幕**：可现场切换"原文上 / 译文上"
- **7 个 accent 颜色**（White / Ice / Gold / Neon / Coral / Violet / Cyan），应用于字幕文字
- **液态玻璃**：macOS 26 SwiftUI `Window` + `GlassEffectContainer`

## 后续开发方向

- **同声传译模式**：后续将引入TTS，把翻译结果直接播出来，形成完整的同传体验。
- **外置 Agent 词库进一步优化**：术语库可在 Agent 中训练，形成自学习闭环

## 常见问题

| 现象 | 原因 | 修复 |
|------|------|------|
| 字幕窗体无字幕 | ASR 还没启动或没识别到声音 | 等启动 banner "准备就绪" 后说话；查看 `whicc.log` |
| 翻译不工作 | LM Studio 未启动 / 网络不通 / 配置未启用 | 在设置面板确认 `translation_enabled=true` + URL 可达 |
| 翻译整段出现 | 后端没走 partial 同传模式 | 重启 app；如果还不行看 [DEVELOPMENT.md](DEVELOPMENT.md) CLI 参数确认 `--mode partial` |
| ASR 不识别中文 | 后端没启用 auto 语言检测 | 在设置面板确认中文识别槽位是 Qwen3 |
| 切换语言不生效 | `lang_config.json` 没更新 | 检查 macui 设置面板写入是否正常 |
| `retry=True` / `leak=True` 频繁 | 模型输出质量差 / prompt 需要调整 | 看 [DEVELOPMENT.md](DEVELOPMENT.md) 翻译防护机制 |
| 字幕窗体不响应点击 | 当前是 hover-hide 状态 | 鼠标移到字幕区域唤醒 |

## 开发者

- 从源码运行：[DEVELOPMENT.md → 开发模式启动](DEVELOPMENT.md#开发模式启动)
- 完整 CLI 参数：[DEVELOPMENT.md → CLI 参考](DEVELOPMENT.md#cli-参数)
- 项目结构：[DEVELOPMENT.md → 项目结构](DEVELOPMENT.md#项目结构)
- 自己打包 `.app`：[DEVELOPMENT.md → 打包成-macos-app](DEVELOPMENT.md#打包成-macos-app)
- 核心机制（断句 / 翻译防护 / 术语库）：[DEVELOPMENT.md → 核心机制](DEVELOPMENT.md#核心机制)

## 许可证

本项目代码以 **MIT License** 发布 — 见 [LICENSE](LICENSE)。

使用了以下第三方组件，详见 [NOTICE](NOTICE)：
- **AudioTee** (MIT, by Nick Payne) — 编译进 `bin/audiotee` 的 macOS 系统音频采集
- **Qwen3-ASR** (Apache 2.0) — 中文 ASR 模型
- **Nemotron 3.5 ASR** (NVIDIA Open Model License) — 英文 ASR 模型
- **Hy-MT2** (Tencent Model License) — 翻译模型（在 LM Studio / vLLM 中加载）
- 详见 [NOTICE](NOTICE)