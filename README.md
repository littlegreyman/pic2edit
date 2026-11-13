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

> **本地离线 OCR 桌面应用**：把图片 / PDF 转成可编辑文档（Markdown / Word / 纯文本 / HTML / JSON），
> 判别式 OCR 优先、VLM 仅作兜底，支持凭证 schema 与忠实版式还原。
> 模型完全在**本机**加载与推理，**不联网、不上传**，隐私不出本机。

**pic2edit (Picture to Editable Document)** is a local, offline OCR desktop app that turns
images and PDFs into editable Markdown / Word / plain text / HTML / JSON using
[DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR) with a discriminative-first,
zero-hallucination pipeline, plus drop-in document schemas.
Everything runs on your machine — no cloud, no uploads.

---

## ✨ 功能特性

- **完全离线、隐私友好**：模型与引擎下载到本地后断网即可运行；转换过程无任何网络上传。
- **判别式优先、零幻觉**：清晰印刷体先走 RapidOCR（纯 CPU、判别式，不会凭空生成文字）；
  仅在判别式失败时才由 DeepSeek-OCR VLM 兜底，兜底结果还要过幻觉筛查。GUI 与 CLI
  共用同一条 `ocr_pipeline`，不允许绕过流水线直接喂给生成式模型。
- **丰富输入**：图片 `jpg / jpeg / tif / tiff / png / bmp / webp`；PDF 整页高 DPI 渲染做版面
  OCR（不再抽取页内嵌图）；多页 TIF 逐帧拆分；目录 / 多文件批量转换。
- **凭证 schema 三态**：`none` / `auto` / `<id>`。schema 是 drop-in 包
  （内置 → `$PIC2EDIT_SCHEMA_DIR` → `~/.pic2edit/schemas`，同名覆盖），
  **新增一种凭证 = 写一个目录，不改一行转换代码**；
  `python -m schemas.cli` 提供 `list / validate / test / new / pack`。
  内置三个包：`invoice`（旧版纸电票，插件式）、`invoice_digital`（数电票，独立 id）、
  `asset_card`（声明式）；均满足 v2 五件套（README / standard.json / 输入输出样例），
  契约见 `ocr_app/docs/schema-technical-spec-v1.md` §3.1.1。
- **忠实版式导出**：命中 schema 走其版式；未命中时用「原图垫底 + 按 OCR 坐标叠透明
  文字层」（可搜索 PDF 的做法），不再用 HTML 重排版面冒充原件；三档降级，
  导出 HTML 与 GUI 预览统一过白名单清洗。
- **schema 生成器**：`python -m schema_gen` 从国标 XSD 生成「数据格式齐全、
  定位策略待补」的声明式骨架包。标准只描述数据模型、不含票面坐标与版式，
  因此生成器**不假装产出可直接上线的抽取规则**（详见
  [`ocr_app/docs/schema-technical-spec-v1.md`](ocr_app/docs/schema-technical-spec-v1.md)）。
- **四种 GUI 识别模式**：Markdown（默认） / 纯文本 OCR / 图表·公式 /
  表格（键值→表）。`html_table`（HTML/合并单元格）未接通 GUI 链路，已从下拉移除，
  仅 CLI / skill 的 `--mode html_table` 保留。导出文件支持
  `.md / .txt / .docx / .html / .json`，五个分支统一走 `export_by_ext()`，
  命中 schema 时 md/html/json 均带溯源、字段化正文一致。
- **三后端可切换**：
  - **llama.cpp（默认、最轻量）**：官方预编译 `llama-server` + 量化 GGUF（Q4_K_M 约 2GB，最省显存，适配 8GB 显卡）。
  - **Ollama（可选）**：`ollama pull deepseek-ocr` 后用本机 Ollama 推理（需注意官方模型为 6.7GB F16）。
  - **Transformers（可选）**：本地 HF 官方模型 + 4/8bit 量化，常驻显存，连续多页转换很快；
    分辨率模式 Tiny / Small / Base / Large / Gundam **仅此后端消费**，经 `--mode` 下发。
- **PySide6 双栏 GUI**：左侧待转换列表 / 图像预览，右侧可编辑 Markdown（编辑 / 预览双模式），
  一键导出 `.md / .txt / .docx / .html / .json`。
- **双前端共享内核**：GUI(Qt) 与 WorkBuddy skill(CLI) 都只经 `backend_api.py` 门面访问内核；
  引擎诊断、逐页路由策略、提示词与采样参数由两处共用，避免能力漂移。
- **针对密集页面退化做了参数调优**：贪心解码 + 长程重复惩罚，稳定不崩、不截断（详见[关键技术要点](#关键技术要点)）。
- **跨平台**：Windows / Linux / macOS（启动与下载脚本均提供 `.bat` 与 `.sh`；一键启动 GUI：Windows 双击 `launch_gui.bat`、Linux / macOS `./launch_gui.sh`，均直接调用项目自带 venv 解释器，无需系统 PATH 上有 Python）。

---

## 📂 目录结构

> 以下按 `git ls-files` 逐条对账；`test_*.py` 为全量回归测试，未逐一列出。

```
pic2edit/
├── LICENSE                     # MIT 许可证
├── VERSION                     # 版本说明 + 唯一变更记录入口
├── THIRD_PARTY_NOTICES         # 第三方许可证与署名索引（合规分发）
├── README.md                   # 项目总览（本文件）
├── INSTALL.zh-CN.md            # 面向普通用户的安装指南
├── .gitignore
├── ruff.toml                   # 静态检查规则（ruff 钉 0.17.4）
├── build_logos.py              # logo 资源生成
├── pic2edit-logo.png           # 图标资源（另有 horizontal / transparent / ico 变体）
├── launch_gui.py               # 跨平台一键启动 GUI（自动定位 ocr_app 与 venv）
├── launch_gui.bat / launch_gui.sh    # Windows / Linux·macOS 薄壳
├── packaging/                  # PyInstaller 冻结 + Inno Setup 安装包
│   ├── build_exe.py
│   ├── pic2edit.spec           # 冻结清单（含 RapidOCR 数据收集）
│   └── pic2edit.iss            # 安装包脚本（版本号在此）
├── .github/workflows/ci.yml    # 云端 CI：全量 pytest + ruff
├── .workbuddy/skills/pic2edit-ocr/   # WorkBuddy skill（CLI 前端）
│   ├── SKILL.md
│   ├── scripts/pic2edit_convert.py
│   └── references/api_contract.md
└── ocr_app/                    # 应用主体
    ├── README.md               # 技术细节深读
    ├── backend_api.py          # ★ GUI / skill 唯一门面
    ├── document_controller.py  # 文档分组 + 扁平页序 + 选择规约（零 Qt）
    ├── engine_launcher.py      # 引擎进程组回收（关停必走 stop）
    ├── gui.py / main.py / worker.py / config.py / about.py
    ├── conftest.py / pytest.ini    # offscreen 夹具 + 测试配置
    ├── test_version_consistency.py # 版本位 / CI 依赖 / 目录树对账门禁
    ├── requirements.txt / requirements.lock / requirements-dev.txt
    │   requirements_tf.txt / requirements_freeze.txt
    ├── setup_venv.bat / download_models.bat / download_models_tf.bat
    ├── run_llamacpp_server.py / run_transformers_server.py
    ├── verify_backend.py / _smoke_test.py
    ├── backends/               # 三后端 + 判别式 / 路由 / 流水线 / 引擎诊断（13 模块）
    │   └── routing.py / ocr_pipeline.py / engine_boot.py
    ├── schemas/                # ★ 凭证 schema 子系统（drop-in 包 + runtime/lint/cli）
    │   ├── asset_card/         #   声明式 v2 包（含 README / standard.json / samples）
    │   ├── invoice/            #   插件式 v2 包（extract.py / render.py / html_layout.py）
    │   ├── invoice_digital/    #   数电票独立 id（声明式，detect 与 invoice 互斥）
    │   └── runtime.py / lint.py / cli.py / registry.py
    ├── schema_gen/             # ★ 国标 XSD → schema 骨架包生成器
    │   ├── README.md
    │   └── sources.py / readers.py / ir.py / emit.py / verify.py / cli.py
    ├── utils/                  # 输入预处理 / 导出 / 忠实版式 / HTML 清洗（16 模块）
    │   └── input_prep.py / export_formats.py / faithful_html.py / html_safety.py
    └── docs/                   # 规格、审查报告、验证矩阵、原件台账（8 份）
        └── schema-technical-spec-v1.md
```

> 更完整的目录说明、后端细节与排错见 [`ocr_app/README.md`](ocr_app/README.md)；
> schema 包契约见 [`ocr_app/docs/schema-technical-spec-v1.md`](ocr_app/docs/schema-technical-spec-v1.md)，
> 生成器用法见 [`ocr_app/schema_gen/README.md`](ocr_app/schema_gen/README.md)。

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

> 注：首次实际下载约 **3.2 GB**——Q4_K_M 主模型约 1.95 GB，外加两个 mmproj 投影文件约 1.3 GB（下载器当前会一并拉取两个 mmproj 变体，运行时只需其一）。更完整的磁盘空间与系统要求见 [`INSTALL.zh-CN.md`](INSTALL.zh-CN.md)。

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

> **带图标的启动入口**：`launch_gui.bat` 本身在 Windows 资源管理器里只能显示脚本默认图标（`.bat` 无法绑定自定义图标）。仓库根目录的 `pic2edit.lnk` 是一个已绑定 `pic2edit-logo.ico`（pic2edit logo）图标的快捷方式，双击它即可启动 GUI，效果等同 `launch_gui.bat`。该 `.lnk` 含本机绝对路径，属本机便捷入口，不纳入版本管理。

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

## 🎛 识别模式与导出格式

GUI 主窗口工具栏的「**识别模式**」下拉提供四种模式（只影响提示词与后续处理，
底层采样参数一致）。它决定的是「怎么识别」，不是「导出成什么文件」：

| 选项 | 发送的提示词 | 说明 |
| --- | --- | --- |
| Markdown（默认） | `<|grounding|>Convert the document to markdown.` | 版式/坐标感知的 Markdown |
| 纯文本 OCR | `Free OCR.` | 无布局、纯文本 |
| 图表·公式 | `Parse the figure.` | 解析图表 / 公式 |
| 表格（键值→表） | `<|grounding|>Convert the document to markdown.` + 客户端 KV→表 | 真表格由模型原生输出 `\|` 表；对“键：值”标签版式，由 `kv_text_to_markdown_table` 做确定性后处理转成两列表 |

`html_table`（让模型输出带 `rowspan` / `colspan` 的 HTML）从未接通 GUI 链路，
已从下拉移除；仅 CLI / skill 的 `--mode html_table` 保留。

导出格式由导出时的文件扩展名决定：`.md / .txt / .docx / .html / .json` 五种，
全部统一走 `export_by_ext()`；命中 schema 时 md/html/json 带溯源与字段化正文。

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

在 `ocr_app/` 下用项目自带 venv 执行（Windows 把 `./.venv/bin/python` 换成
`.venv\Scripts\python.exe`）：

```bash
# 1) 全量回归（无 GPU 也能跑完，conftest 预设 offscreen 且限制 OpenCV 线程）
./.venv/bin/python -m pytest -q

# 2) 静态检查（ruff 钉 0.17.4，避免新规则无端变红）
./.venv/bin/python -m ruff check .

# 3) 无界面冒烟测试（PDF/TIF/导出/三后端/启动器）
./.venv/bin/python _smoke_test.py

# 4) 后端契约自检（严格 mock llama-server，无需模型）
./.venv/bin/python verify_backend.py

# 5) 真机直连：需本机有模型 + GPU，打印模型输出的真实 OCR 文本
./.venv/bin/python verify_backend.py --live http://localhost:8080
```

schema 子系统与生成器（详见各模块 README）：

```bash
./.venv/bin/python -m schemas.cli list          # 也可 validate / test / new / pack
./.venv/bin/python -m schema_gen --id ppe_card --version 1.0.0
```

- **真实 OCR 质量**只能靠第 5 步（或 GUI 真机转换）人工比对原图：沙箱
  `ulimit -v 4GB` + 拦 CUDA，自动化测试验证的是产物契约，不代表识别质量。
- 一致性与规格由静态对账测试钉住：`test_version_consistency.py`（版本位 / CI 依赖 /
  README 目录树）、`test_schema_spec.py`（规格常量 ↔ 源码）。
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

本项目有两种分发形态：**源码运行**与 **PyInstaller 冻结 + Inno Setup 安装包**（见 `packaging/`）。
DeepSeek-OCR 模型权重与 llama.cpp 引擎**不随仓库分发**，均在首次运行时从官方来源下载到本地；
安装包会内置 PySide6 动态链接库（LGPL-3.0）等第三方二进制，其许可证、署名与
「可替换/可获取源码」要求统一见 [`THIRD_PARTY_NOTICES`](THIRD_PARTY_NOTICES)。更细的上游组件说明见 [`ocr_app/README.md`](ocr_app/README.md) 的“许可证与第三方署名”一节。

> ℹ️ **许可证说明**：GUI 框架已采用 **PySide6（LGPL-3.0，Qt 官方绑定）**，可闭源/商业分发（动态链接 Qt）。结合默认判别式 OCR 路径（pypdfium2 + RapidOCR 系列，均为 MIT/Apache-2.0/BSD），**本项目整体可按 MIT License 分发，含打包后的 GUI 二进制**。

---

## 📌 致谢

- [DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR) —— 本地离线 OCR 核心模型。
- [llama.cpp](https://github.com/ggml-org/llama.cpp) —— 轻量本地推理引擎。
- [pypdfium2](https://github.com/pypdfium2/pypdfium2) / [PySide6](https://doc.qt.io/qtforpython/) / [python-docx](https://github.com/python-openxml/python-docx) 等开源依赖。

---

## 📝 版本

当前为 **alpha 0.20**（对应安装包版本 0.20.0，Git tag `alpha-0.20`）。项目仍处于早期阶段，欢迎 Issue / PR。
