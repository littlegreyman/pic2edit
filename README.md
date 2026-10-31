# pic2edit

<p align="center">
  <img src="pic2edit-logo.png" alt="pic2edit logo" width="160">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Offline-100%25-brightgreen.svg" alt="Offline">
  <img src="https://img.shields.io/badge/Engine-DeepSeek--OCR%20%2F%20llama.cpp-orange.svg" alt="Engine">
</p>

> **本地离线 OCR 桌面应用**：把图片 / PDF 转成可编辑文档（Markdown / Word / 纯文本）。
> 模型完全在**本机**加载与推理，**不联网、不上传**，隐私不出本机。

**pic2edit (Picture to Editable Document)** is a local, offline OCR desktop app that turns
images and PDFs into editable Markdown / Word / plain text using the
[DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR) vision-language model.
Everything runs on your machine — no cloud, no uploads.

---

## ✨ 功能特性

- **完全离线、隐私友好**：模型与引擎下载到本地后断网即可运行；转换过程无任何网络上传。
- **丰富输入**：图片 `jpg / jpeg / tif / tiff / png / bmp / webp`；PDF 整页高 DPI 渲染做版面 OCR，并抽取 PDF 内嵌图形作为独立图像项；多页 TIF 逐帧拆分。
- **DeepSeek-OCR 视觉语言模型**：保留版式、表格与坐标感知输出（`<|grounding|>`）。
- **三后端可切换**：
  - **llama.cpp（默认、最轻量）**：官方预编译 `llama-server` + 量化 GGUF（Q4_K_M 约 2GB，最省显存，适配 8GB 显卡）。
  - **Ollama（可选）**：`ollama pull deepseek-ocr` 后用本机 Ollama 推理（需注意官方模型为 6.7GB F16）。
  - **Transformers（可选）**：本地 HF 官方模型 + 4/8bit 量化，常驻显存，连续多页转换很快。
- **PySide6 双栏 GUI**：左侧待转换列表 / 图像预览，右侧可编辑 Markdown（编辑 / 预览双模式），一键导出 `.md / .docx / .txt`。
- **四种输出格式**：Markdown（默认） / 纯文本 OCR / 图表·公式 / 表格（键值→表）。
- **针对密集页面退化做了参数调优**：贪心解码 + 长程重复惩罚，稳定不崩、不截断（详见[关键技术要点](#关键技术要点)）。
- **跨平台**：Windows / Linux / macOS（启动与下载脚本均提供 `.bat` 与 `.sh`；一键启动 GUI：Windows 双击 `launch_gui.bat`、Linux / macOS `./launch_gui.sh`，均直接调用项目自带 venv 解释器，无需系统 PATH 上有 Python）。

---

## 📂 目录结构

```
pic2edit/
├── LICENSE                 # MIT 许可证
├── VERSION                 # 版本说明
├── .gitignore
├── launch_gui.py           # 跨平台一键启动 GUI（自动定位 ocr_app 与 venv）
├── launch_gui.bat          # Windows 一键启动 GUI（双击即用，直接调用 venv 解释器）
├── launch_gui.sh           # Linux/macOS 一键启动 GUI（直接调用 venv 解释器）
└── ocr_app/                # 应用主体
    ├── main.py             # 入口
    ├── gui.py              # PySide6 双栏主界面 + 输出格式下拉 + 运行状态面板
    ├── config.py           # 配置（后端/模型/分辨率/提示词），持久化到 ~/.pic2edit
    ├── worker.py           # 后台 OCR 线程
    ├── requirements.txt    # 主程序依赖
    ├── requirements_tf.txt # Transformers 后端专用依赖
    ├── download_models.py  # ★ 下载 llama-server 引擎 + DeepSeek-OCR GGUF/mmproj（跨平台逻辑）
    ├── run_llamacpp_server.py   # 自动定位 exe/模型/mmproj 并启动服务
    ├── run_transformers_server.py
    ├── verify_backend.py   # ★ 端到端验证：严格契约 mock + --live 直连真机
    ├── _smoke_test.py      # 无界面冒烟测试
    ├── test_text_utils.py / test_table_utils.py
    ├── backends/           # Ollama / llama.cpp / Transformers 后端实现
    ├── utils/              # 输入预处理 / docx 导出 / 日志
    └── docs/
        └── llamacpp_deepseek_ocr_api.md  # llama.cpp + DeepSeek-OCR 请求/返回契约（权威文档）
```

> 更完整的目录说明、后端细节与排错见 [`ocr_app/README.md`](ocr_app/README.md)。

---

## 🚀 快速开始

> 以下以 **llama.cpp 后端（默认、最省显存）** 为例；跨平台命令中 Windows 用 `.bat`，Linux / macOS 用同名 `.sh`。

### 1. 克隆仓库

```bash
git clone https://github.com/littlegreyman/pic2edit.git
cd pic2edit
```

### 2. 准备 Python 环境与依赖

```bat
cd ocr_app
setup_venv.bat        :: Windows
```
```bash
cd ocr_app
./setup_venv.sh       :: Linux / macOS
```
这会在 `ocr_app/.venv` 创建虚拟环境并安装主程序依赖（PySide6 / pypdfium2 / Pillow / python-docx / markdown / requests / huggingface_hub）。

### 3. 下载推理引擎与模型（只需一次，之后可离线）

```bat
download_models.bat   :: Windows
```
```bash
./download_models.sh   :: Linux / macOS
```
该脚本会：
1. 从 GitHub Release（`ggml-org/llama.cpp`，tag `b11853`）下载官方预编译的 `llama-server`（Windows/Linux 用 CUDA 版，macOS 用 Metal 版）到 `ocr_app/bin/`；
2. 用 `huggingface_hub` 把 DeepSeek-OCR 的 GGUF（Q4_K_M）+ mmproj 拉到 `ocr_app/models/`（`hf.co` 不通时自动切到 `hf-mirror.com`）。

> 若网络受限，脚本会给出手动下载链接；你也可以手动把对应 GGUF 与 mmproj 放进 `ocr_app/models/`。

### 4. 启动本地 OCR 服务

```bat
run_llamacpp_server.bat   :: 默认 http://localhost:8080
```
```bash
./run_llamacpp_server.sh
```
看到 `listening on http://127.0.0.1:8080` 即就绪。

### 5. 打开 GUI（一键启动，推荐）

**Windows**：直接**双击**仓库根目录的 `launch_gui.bat` 即可（它会用项目自带的 venv 解释器 `ocr_app\.venv\Scripts\python.exe` 启动 GUI，无需系统 PATH 上有 `python`）。或在命令行运行：

```bat
launch_gui.bat
```

> 说明：若你的 Windows 没有在 PATH 上安装系统 Python（PowerShell 里输入 `python` 会跳转到微软商店占位桩），请务必用 `launch_gui.bat`——它绕开了系统 `python`，直接调用项目内的 venv 解释器。

**Linux / macOS**：在仓库根目录运行（需先 `chmod +x launch_gui.sh` 一次，或始终用 `sh` 调用）：

```bash
./launch_gui.sh          # 直接调用 venv 解释器 ocr_app/.venv/bin/python3，无需系统 PATH 上有 python
```

> 注意：现代 Linux 上往往**没有 `python` 命令**（`python3` 才有），macOS 也可能缺少 `python`。所以和 Windows 同理——请优先用 `launch_gui.sh`，它本身只依赖 `/bin/sh`，直接调用项目自带的 venv 解释器，不依赖系统 Python 版本。

> 仅想检查环境是否就绪（不启动 GUI）：`launch_gui.bat --dry-run`（Windows）或 `./launch_gui.sh --dry-run`（Linux / macOS）。

GUI 启动后会**按需自动拉起本地 OCR 引擎**，无需手动执行第 4 步的服务脚本。`launch_gui.py` / `.bat` 的定位优先级：环境变量 `PIC2EDIT_OCR_APP` / `PIC2EDIT_ROOT` > 从脚本位置向上查找 > 常见用户目录（`~/Documents/pic2edit`、`~/文档/pic2edit`、`~/pic2edit`、`~/projects/pic2edit`）；若项目不在这些默认位置，设置上述环境变量最稳妥。

手动启动（等价，但需自己切到 `ocr_app` 并指定 venv python）：

```bat
cd ocr_app
.venv\Scripts\python.exe main.py   :: Windows
```
```bash
cd ocr_app
./.venv/bin/python main.py         :: Linux / macOS
```

GUI 默认后端即为 `llama_cpp`，服务起来后直接“转换选中 / 转换全部”即可。

---

## 🔧 三种后端

| 后端 | 显存占用 | 备注 |
| --- | --- | --- |
| **llama.cpp**（默认） | 最低（Q4_K_M ≈ 2GB） | 官方预编译单文件引擎 + 量化 GGUF，最适合 8GB 显卡 |
| **Ollama** | 较高（F16 ≈ 6.7GB） | 需 Ollama v0.13.0+；`ollama pull deepseek-ocr` |
| **Transformers** | 中高 | 独立 Python 3.11 环境 + 本地 HF 模型，常驻显存，连续转换快 |

Ollama / Transformers 的配置步骤见 [`ocr_app/README.md`](ocr_app/README.md)。

---

## 📤 输出格式

GUI 主窗口工具栏的“输出格式”下拉提供四种模式（仅提示词与后续处理不同，底层采样参数一致）：

| 选项 | 发送的提示词 | 说明 |
| --- | --- | --- |
| Markdown（默认） | `<|grounding|>Convert the document to markdown.` | 版式/坐标感知的 Markdown |
| 纯文本 OCR | `Free OCR.` | 无布局、纯文本 |
| 图表·公式 | `Parse the figure.` | 解析图表 / 公式 |
| 表格（键值→表） | `<|grounding|>Convert the document to markdown.` + 客户端 KV→表 | 真表格由模型原生输出 `\|` 表；对“键：值”标签版式，由 `kv_text_to_markdown_table` 做确定性后处理转成两列表 |

---

## 🧠 关键技术要点

### 1. media marker 不要手写（极易踩坑）
DeepSeek-OCR 在 llama.cpp 上的**原生模板会自动注入 media marker** 并把 `image_url` 块绑定到视觉编码器。因此客户端**文本里绝不能写任何 `<image>` / `<__media_...__>` 占位符**——多写一个就会因“2 markers vs 1 bitmap”而返回 **HTTP 400**。
并且：**启动 `llama-server` 时不要加 `--chat-template deepseek-ocr`**（裸模板既不自动注入 marker、也不绑视觉，必坏）。让 llama.cpp 按模型自动选原生模板即可。本项目 `run_llamacpp_server.py` 已默认不写该参数。

### 2. 生成参数调优（解决密集页面退化）
对证照 / 包装标签这类密集页面，DeepSeek-OCR 容易出现“尾部复读”或“整篇从头重列并截断”。经真机扫参验证的根因级结论：
- **`temperature = 0`（贪心 / 确定性）**：`temp > 0` 会让模型**随机**退化——同一组参数既可能干净输出，也可能崩成“同一块重复上百次”，不可靠；只有贪心在所有实验中稳定不崩。
- **`repeat_last_n = 4096`**：llama.cpp 默认仅 64，惩罚窗口够不到“整篇重列”的长程循环；放大到 4096 才能真正抑制。
- **`repeat_penalty = 1.10`**：比默认（1.0）强、又不会像 1.15+ 那样过度压制导致提前吐 EOS 并冒出英文营销语。
- **`max_tokens = 16384`**：给完整文档留余量（贪心不会 runaway）。

上述参数已在请求 payload 与启动器命令中固定，无需手动调整。

---

## 🧪 开发与测试

仓库内置三层验证（无需 GPU 即可跑大部分）：

```bat
:: 1) 无界面冒烟测试（PDF/TIF/导出/三后端/启动器）
.venv\Scripts\python.exe _smoke_test.py

:: 2) 后端契约自检（严格 mock llama-server，无需模型）
.venv\Scripts\python.exe verify_backend.py

:: 3) 单元：客户端文本后处理
.venv\Scripts\python.exe test_text_utils.py
.venv\Scripts\python.exe test_table_utils.py
```

- `verify_backend.py --live http://localhost:8080`：直连真机打印模型输出的真实 OCR 文本。
- 真实样例图片可通过环境变量 `PIC2EDIT_CLINIC` 指定（缺省时自动合成 PNG 跑契约测试，开箱即用）。
- 更深层的调用契约见 [`ocr_app/docs/llamacpp_deepseek_ocr_api.md`](ocr_app/docs/llamacpp_deepseek_ocr_api.md)。

---

## 🔒 隐私

- 所有 OCR 推理都在**本机**完成，图片与文档不会被上传到任何服务器。
- 用户配置持久化在 `~/.pic2edit/config.json`（本机，不在仓库内）。
- 模型权重与引擎二进制存放在 `ocr_app/models/` 与 `ocr_app/bin/`，均被 `.gitignore` 排除，**不入库**。
- 仓库源码中**不含任何个人路径或密钥**。

---

## ⚖️ 许可证与第三方署名

本项目（pic2edit 自身源码）以 **MIT License** 发布，见 [LICENSE](LICENSE)。

本项目**不打包、不内置**任何模型权重或第三方二进制：DeepSeek-OCR 模型与 llama.cpp 引擎均在**运行时**从官方来源下载到本地。上游组件的许可证与署名细节见 [`ocr_app/README.md`](ocr_app/README.md) 的“许可证与第三方署名”一节。

> ℹ️ **许可证说明**：GUI 框架已采用 **PySide6（LGPL-3.0，Qt 官方绑定）**，可闭源/商业分发（动态链接 Qt）。结合默认判别式 OCR 路径（pypdfium2 + RapidOCR 系列，均为 MIT/Apache-2.0/BSD），**本项目整体可按 MIT License 分发，含打包后的 GUI 二进制**。

---

## 📌 致谢

- [DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR) —— 本地离线 OCR 核心模型。
- [llama.cpp](https://github.com/ggml-org/llama.cpp) —— 轻量本地推理引擎。
- [pypdfium2](https://github.com/pypdfium2/pypdfium2) / [PySide6](https://doc.qt.io/qtforpython/) / [python-docx](https://github.com/python-openxml/python-docx) 等开源依赖。

---

## 📝 版本

当前为 **alpha 0.17**（参见 Git tag `alpha-0.17`）。项目仍处于早期阶段，欢迎 Issue / PR。
