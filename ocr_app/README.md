> 项目总览、功能特性与快速开始请看仓库根目录 [README.md](../README.md)；本文为技术细节深读。

# 图片转可编辑文档（DeepSeek-OCR 本地离线）

把 `jpg / jpeg / tif / tiff / pdf` 中的图文转换成**可编辑的 Markdown 文档**，并支持导出 `.docx / .md / .txt`。  
使用 DeepSeek 官方开源 OCR 模型 **DeepSeek-OCR**，**模型在本地加载、本地推理，下载一次后断网即可运行**。

## 功能特性

- **本地离线**：模型下载到本地后无需联网，GUI 左侧为待转换文件，右侧为转换后的可编辑文档。
- **三后端可切换（Ollama / llama.cpp / Transformers）**
  - **llama.cpp（代码默认且推荐，最轻量）**：直接跑量化 GGUF（Q4_K_M ~1.95GB / Q8_0 ~3.1GB），单文件推理引擎，最适合 8GB 显存，比 Ollama 默认的 6.7GB F16 省很多。
  - **Ollama（可选）**：`ollama pull deepseek-ocr` 后由本机 Ollama 推理，但官方模型为 6.7GB F16，8GB 显存放不下需部分卸载到内存、偏慢。
  - **Transformers**：直接加载本地 HF 官方模型，支持 4/8bit 量化（需独立 Python 3.11 环境）。模型由常驻本地服务（`transformers_server.py`）加载一次、驻留显存，多页连续转换很快。
- **输入格式**
  - 图片：`.jpg / .jpeg / .tif / .tiff / .png / .bmp / .webp`
  - **PDF 中的图形**：整页渲染为高 DPI 图像做完整版面 OCR，同时抽取 PDF 内嵌图形作为独立图像项。
  - **多页 TIF**：逐帧拆分，每帧单独转换。
- **分辨率模式**：Tiny / Small / Base / Large / Gundam（动态多尺度，适合复杂版面）。
- **提示词模板**：转 Markdown / 纯 OCR / 解析图表。
- **输出与导出**：右栏直接编辑 Markdown，可切换“编辑/预览”，一键导出 `.docx / .md / .txt`。

## 目录结构

```
ocr_app/
├── main.py                  # 入口
├── gui.py                   # PyQt6 双栏主界面 + 设置对话框 + 底部“运行状态”日志面板
├── config.py                # 配置（后端/模型/分辨率/提示词），持久化到 ~/.pic2edit
├── worker.py                # 后台 OCR 线程
├── requirements.txt         # 主程序依赖
├── requirements_tf.txt      # Transformers 后端专用依赖
├── download_models.py       # ★ 跨平台：下载 llama-server 引擎 + DeepSeek-OCR GGUF/mmproj（逻辑来源）
├── download_models_tf.py    # ★ 跨平台：建 3.11 venv + 下载 HF 官方模型到 models_tf/
├── setup_venv.sh            # Linux/macOS 一键建 venv 并安装
├── download_models.sh       # Linux/macOS 版的 download_models.py 薄壳
├── download_models_tf.sh    # Linux/macOS 版的 download_models_tf.py 薄壳
├── run_llamacpp_server.sh   # Linux/macOS 启动 llama.cpp 服务
├── run_transformers_server.sh   # Linux/macOS 启动 Transformers 服务
├── download_models.bat      # Windows 薄壳（调用 download_models.py）
├── download_models_tf.bat   # Windows 薄壳（调用 download_models_tf.py）
├── setup_venv.bat           # Windows 一键建 venv 并安装
├── run_llamacpp_server.bat  # Windows 薄壳（调用下方 .py）
├── run_llamacpp_server.py   # 自动定位 exe/模型/mmproj 并启动服务
├── run_transformers_server.bat  # Windows 薄壳（调用下方 .py）
├── run_transformers_server.py   # 自动定位 3.11 venv/模型目录并启动服务
├── verify_backend.py    # ★ 端到端验证：严格契约 mock + --live URL 直连真机出真实 OCR 文本
├── _smoke_test.py       # 无界面冒烟测试（PDF/TIF/导出/三后端/启动器/clinic_form.jpg 回归）
├── backends/
│   ├── base.py              # OCRBackend 抽象接口
│   ├── ollama_backend.py    # Ollama HTTP 后端
│   ├── llamacpp_backend.py  # llama.cpp OpenAI 兼容后端（默认；发送 image_url 块+提示词，不写 marker，交给原生模板注入）
│   ├── transformers_backend.py  # Transformers OpenAI 兼容客户端（自动加 <image> 占位符）
│   └── transformers_server.py   # Transformers 本地常驻服务（加载一次，常驻显存；cuda/mps/cpu 自适应）
├── utils/
│   ├── input_prep.py        # PDF/TIF/图片 -> 图像项列表
│   ├── export_docx.py       # Markdown -> docx / txt 导出
│   └── logging_setup.py     # 日志与运行环境快照（出错时记录环境信息）
└── docs/
    └── llamacpp_deepseek_ocr_api.md  # llama.cpp + DeepSeek-OCR 精确请求/返回契约（调用与拆包唯一权威）
```

> 跨平台说明：所有下载/启动的**真实逻辑都在 `.py` 脚本里**，`.bat` / `.sh` 只是调用它们的薄壳。Windows 用 `.bat`，Linux / macOS 用 `.sh`。


## 安装与运行

> **跨平台入口对照**：Windows 用 `.bat`，Linux / macOS 用同名 `.sh`（如 `setup_venv.sh`、`download_models.sh`、`run_llamacpp_server.sh`）。所有脚本的真实逻辑都在对应的 `.py` 里，薄壳只负责调用。

### 1. 主程序（GUI）

Windows：

```bat
cd ocr_app
setup_venv.bat
.venv\Scripts\python.exe main.py
```

Linux / macOS：

```bash
cd ocr_app
./setup_venv.sh
./.venv/bin/python main.py
```

> 默认后端为 `llama_cpp`。若用该后端，请先按【4. llama.cpp】准备引擎与模型并启动服务。

### 2. Ollama 后端（可选）

1. 安装 Ollama：<https://ollama.com>（**注意：`deepseek-ocr` 需要 Ollama v0.13.0 或更高版本**）。
2. 拉取模型（只需一次，之后离线）：
   ```bat
   ollama pull deepseek-ocr
   ```
3. 在程序“设置”确认后端为 `ollama`、模型名为 `deepseek-ocr`（会自动匹配 `deepseek-ocr:latest`）、服务地址 `http://localhost:11434`。

> Ollama 通过 `images` 字段传图，由 Ollama 的模板自动注入图像位置，提示词里**不需要**写 `<image>` 占位符；`<|grounding|>` 等模型 token 仍可使用。

### 3. Transformers 后端（可选）

`transformers + bitsandbytes` 在 Python 3.13 缺预编译 wheel，所以需要一个独立的 **Python 3.11** 环境；该后端把模型加载到本地常驻服务（`transformers_server.py`，模型只加载一次、常驻显存），由 `run_transformers_server.bat` 启动，比早期"每次调用重载模型"的版本快很多。

**一键准备（在你本机联网时，只需做一次）：**

```bat
download_models_tf.bat   :: 建 .venv_tf(3.11) + 装 torch/transformers + 下载 deepseek-ai/DeepSeek-OCR 到 models_tf/
```

> 前提：本机已装 Python 3.11 且 `py -3.11` 可用（<https://www.python.org/downloads/> 选 3.11.x）。

**日常启动（离线）：**

```bat
run_transformers_server.bat   :: 启动本地服务，默认 http://localhost:8001（首次加载模型约 1 分钟）
.venv\Scripts\python.exe main.py   :: 打开 GUI，设置里选后端=transformers
```

- `run_transformers_server.py` 会自动找 `.venv_tf/Scripts/python.exe` 与 `models_tf/` 下的模型目录，无需手填；量化默认 8bit（8GB 显存）或 4bit（更省显存，在设置里改 `tf_quant`）。
- 若想手动指定，可在程序“设置”里填“Python 解释器 / 模型目录 / 量化”，或用环境变量。

> 说明：相比 Ollama / llama.cpp（单文件量化引擎、最省显存），Transformers 后端用官方 HF 模型、需要 3.11 独立环境与更多显存，但无需转换 GGUF；三者任选其一即可。

### 4. llama.cpp 后端（代码默认 + 推荐，最轻量）

llama.cpp 直接跑量化 GGUF，体积和显存占用远小于 Ollama 默认的 6.7GB F16，单文件推理引擎，最适合 8GB 显卡（Q4_K_M 约 2GB 可完整进显存）。本程序使用 **llama.cpp 官方预编译的 `llama-server.exe`（CUDA 版）** 作为引擎——它是 OpenAI 兼容服务，支持 `-m` / `--mmproj`，正好匹配本程序，且 **无需在本机编译**（Python 3.13 上 `pip` 装 `llama-cpp-python` 会从源码编译，并因 Windows 长路径上限 / 缺 C++ 编译器而失败，所以改用官方二进制）。

> ⚠️ **启动 llama-server 时不要加 `--chat-template deepseek-ocr`**：该裸模板既不自动注入 media marker，也不把 `image_url` 块绑定到视觉编码器，会让每次 OCR 请求 400（或只回通用废话）。让 llama.cpp 按模型自动选择原生 deepseek-ocr 模板即可（本程序的 `run_llamacpp_server.py` 已经去掉该参数）。原生模板会自动注入 marker，所以客户端**不在文本里写任何 `<image>` / `<__media_...__>` 占位符**——多写反而会因“2 markers vs 1 bitmap” 而 400。

**一键准备（在你本机联网时，只需做一次）：**

```bat
setup_venv.bat          :: 建 venv 并装主程序依赖（若已做过可跳过）
download_models.bat     :: ① 从 GitHub 下载官方 llama-server.exe (CUDA) 到 bin\
                          ② 用 huggingface_hub 把 DeepSeek-OCR GGUF+mmproj 拉到 models\
```

> `download_models.bat` 第 ① 步从 llama.cpp 的 GitHub Release（`ggml-org/llama.cpp`，标签 `b11853`，CUDA 12.4 x64）下载官方预编译服务器并自动解压、把 CUDA 运行库 DLL 归集到 `bin\`；第 ② 步用 `huggingface_hub` 的 `snapshot_download` 拉模型（仅 `*.gguf`，约 2–5GB）。**全部就绪后断网也能跑。** 若 GitHub 访问受限，脚本会给出手动下载链接；模型也可手动放进 `models/`。

**日常启动（离线）：**

```bat
run_llamacpp_server.bat   :: 启动本地 OCR 服务（默认 http://localhost:8080）
.venv\Scripts\python.exe main.py   :: 打开 GUI
```

- `run_llamacpp_server.py` 会在 `bin/` 自动找 `llama-server.exe`，在 `models/` 自动找 `*Q4_K_M*.gguf`（排除 mmproj）和 `*mmproj*.gguf`，无需手填路径。**不要手动加 `--chat-template` 参数**（见上方 ⚠️ 说明）。

> **关于 media marker（重要）**：DeepSeek-OCR 的 llama.cpp **原生模板**会自动注入 media marker 并把 `image_url` 块绑定到视觉编码器，因此客户端在文本里**不要**写任何 marker（不要 `<image>`，也不要 `GET /props` 的随机 marker）。实测（build b11853）确认：image_url 块 + 纯提示词（无 marker）→ 200 且返回真实 OCR；一旦文本里多写一个 marker，就会出现“2 markers vs 1 bitmap” → 400。若服务端被强制加了 `--chat-template deepseek-ocr`（裸模板），则无论客户端怎么写都会坏（400 或只回通用废话）——这种请去掉该参数重启服务端。`run_llamacpp_server.py` 已默认不加该参数。

**可选的模型源（都是量化 GGUF + mmproj，二者缺一不可）：**

- `sabafallah/DeepSeek-OCR-GGUF`：Q4_K_M **1.95GB** / Q8_0 3.13GB（原版 DeepSeek-OCR，最稳妥，脚本默认用这个）
- `NexaAI/DeepSeek-OCR-GGUF`：Q4_0 1.65GB
- `42ailab/DeepSeek-OCR-2-GGUF`：DeepSeek-OCR-2，Q4_K_M 1.95GB + mmproj 512MB

若想换模型源，编辑 `download_models.py` 顶部的 `HF_REPO` 后重跑即可；或手动把对应 GGUF 与 mmproj 放进 `models/`。

> 若 `download_models.bat` 拉模型失败（公司网络/墙），可手动把上述 GGUF 与 mmproj 放进 `models/` 后直接 `run_llamacpp_server.bat`。

程序默认后端已是 `llama_cpp`，服务起来后直接“转换选中 / 转换全部”即可，无需进设置改动。

> 进阶 / 排错：脚本默认下载 CUDA 12.4 版（兼容性最好）。若你的显卡驱动过旧跑不起来，可编辑 `download_models.py` 顶部的 `TAG` / `CUDA_VER`（例如改成 `13.4`，或把仓库/标签换成其他 llama.cpp 发布）后重跑；也可以手动从对应 Release 下载 `llama-bXXXX-bin-win-cuda-*.zip`（Windows）/ `llama-bXXXX-bin-ubuntu-cuda-*-x64.tar.gz`（Linux）/ `llama-bXXXX-bin-macos-*.tar.gz`（macOS）解压，把 `llama-server` 及同目录运行库放进 `bin\`。

### 排错补充：Windows 智能应用控制 / SmartScreen 拦截

`llama-server.exe` 是从 GitHub 下载的**未签名**二进制，Windows 11 的 **智能应用控制（Smart App Control, SAC）** 或 **SmartScreen（声誉保护）** 可能直接拦截它，导致服务一启动就被杀、命令行瞬间跳到 `pause`。`run_llamacpp_server.bat` 现在会明确提示 `server process exited immediately`，而非静默退出。

按侵入性从低到高，任选其一：

1. **解除文件锁定（最轻量，先试这个）**：在资源管理器里右键 `ocr_app\bin\llama-server.exe` → 属性 → 底部“安全”处若有 **解除锁定（Unblock）** 勾选框，勾上并应用。这能去掉 SmartScreen 的“来自其他计算机”标记；若仍是 SAC 拦截则继续下一步。
2. **关闭 SmartScreen 对应用的检查**：Windows 安全中心 → 应用和浏览器控制 → 基于声誉的保护设置 → 关闭“检查应用和文件”。（仅在你本机跑自己下载的程序时关，用完可再开。）
3. **关闭智能应用控制（SAC）**：Windows 安全中心 → 应用和浏览器控制 → 智能应用控制 → **关闭**。注意 SAC 一旦关闭通常无法再切回“评估/开启”模式，属本机安全策略取舍；该 OCR 服务只监听 `127.0.0.1`、不外网暴露，风险可控。

> 若希望最小改动：先试第 1 步；若弹窗仍出现，再走第 3 步（彻底解决 SAC 拦截）。解除/关闭后重新运行 `run_llamacpp_server.bat`，看到 `listening on http://127.0.0.1:8080` 或本脚本打印的 `[ok] llama.cpp server is ready` 即成功。

## 运行状态与日志

- **GUI 底部“运行状态”面板**：实时滚动显示动作流水（导入、转换进度、导出结果）与错误摘要，便于直观了解当前在做什么。
- **运行日志文件**：所有模块统一写日志到 `~/.pic2edit/pic2edit.log`（按 5MB 滚动保留 3 份），出错时会附带**环境快照**（操作系统 / Python 版本 / 关键依赖版本 / 当前后端配置），方便精确定位问题。遇到报错时可先查看该日志。

## 使用步骤

1. 打开文件 / 打开文件夹，导入 `jpg/tif/pdf` 等。
2. 左侧列表选择某项，预览图像；右侧“编辑”页显示/编辑 Markdown，“预览”页渲染排版。
3. 点“转换选中”或“转换全部”开始本地 OCR（状态栏显示进度）。
4. 转换完成后，点“导出”保存为 `.docx / .md / .txt`。

## 离线说明

- **llama.cpp（默认）**：`download_models.bat` 从 GitHub 下载官方预编译 `llama-server.exe`（CUDA）到 `bin\`，并把模型拉到本地 `models/`，之后断网可用。
- Ollama：模型缓存在本机，`ollama pull` 一次后即可断网使用。
- Transformers：模型目录在本地，无需联网。
- 设置保存在 `~/.pic2edit/config.json`，持久化且离线可用。

## 已知限制

- 8GB 显存建议用 llama.cpp 的 Q4_K_M/Q8_0 量化；16bit（bf16）可能显存不足。
- 三种后端均可常驻显存（llama.cpp / Ollama 自带驻留；Transformers 由 `transformers_server.py` 加载一次后常驻），连续多页转换都很快。
- Markdown→docx 为轻量转换，覆盖标题/列表/表格/加粗；复杂排版建议直接用 `.md`。
- Transformers 后端在 Linux/macOS（Apple Silicon）上会自动改用 MPS、在纯 CPU 上用 float32，并跳过仅 CUDA 可用的 bitsandbytes 量化。

## 端到端验证（契约化）

所有调用格式与返回包拆包以 `docs/llamacpp_deepseek_ocr_api.md` 为唯一权威。验证分两层：

1. **契约自检（沙箱可跑，无需 GPU/模型）**：`verify_backend.py` 用严格按文档实现的 mock llama-server，对真实样例 `clinic_form.jpg`（可用环境变量 `PIC2EDIT_CLINIC` 覆盖为本机真实路径）跑完整流程，逐字段断言请求结构（image_url 块 + 纯提示词、**不含任何 media marker**、PNG base64 图片）与返回包拆包。

   ```bat
   .venv\Scripts\python.exe verify_backend.py
   ```

2. **真机输出真实文本（需本机有模型 + GPU）**：先让 `run_llamacpp_server.bat` 起的服务保持窗口开着，再用 `--live` 直连，打印模型输出的真实 OCR 文本，用于确认“能真正输出正确的文本”：

   ```bat
   .venv\Scripts\python.exe verify_backend.py --live http://localhost:8080
   ```

常规回归：`.venv\Scripts\python.exe _smoke_test.py`（含 PDF/TIF/导出/三后端/启动器/clinic_form.jpg 真实文件回归）。

## 许可证与第三方署名

本项目（pic2edit 自身源码）以 **MIT License** 发布，见仓库根目录 [LICENSE](../../LICENSE)。

本项目**不打包、不内置**任何模型权重或第三方二进制；DeepSeek-OCR 模型、llama.cpp 引擎均通过 `download_models*.py` / `run_llamacpp_server*` 在**运行时**从官方来源下载到本地（已被 `.gitignore` 排除，不入库）。因此本仓库仅分发我们自己的封装代码与脚本，相关上游组件按其各自许可证使用：

| 上游组件 | 用途 | 许可证（运行时获取） | 备注 |
| --- | --- | --- | --- |
| [DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR)（模型 + 官方推理代码） | 本地离线 OCR 核心 | 遵循 deepseek-ai 官方许可证 | 仅运行时依赖，按官方要求使用/署名 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp)（预编译 `llama-server`） | 本地推理引擎（默认后端） | MIT | 仅下载二进制，不修改/不打包源码 |
| [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) | GUI 框架 | **GPLv3 或商业许可** | ⚠️ 若分发打包后的 GUI 程序，PyQt6 的 GPLv3 会要求整体按 GPLv3 开源；如需闭源分发 GUI，请改用 PySide6（LGPL） |
| pypdfium2 / Pillow / python-docx / markdown / requests | 文档/图片处理与 HTTP 客户端 | 各自宽松许可证（pypdfium2 为 Apache-2.0，其余 MIT/BSD） | 经由 pip 安装，非本仓库内置 |

> 提示：若你修改并分发本工具，请保留上述 MIT 许可声明，并按上游各自许可证要求保留其版权/署名信息。
