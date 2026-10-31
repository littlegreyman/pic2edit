# pic2edit 安装指南

> 本地离线 OCR 桌面应用 · 把图片 / PDF 转成可编辑文档（Markdown / Word / 纯文本）
> 当前版本：**alpha 0.19**（与安装包版本号一致）
> 许可证：**MIT**（详见文末「许可证与发行说明」）

---

## 目录

1. [简介](#1-简介)
2. [系统要求](#2-系统要求)
   - [2.1 硬件要求](#21-硬件要求)
   - [2.2 软件环境要求](#22-软件环境要求)
   - [2.3 所需磁盘空间](#23-所需磁盘空间)
3. [许可证与发行说明](#3-许可证与发行说明)
4. [安装方式一：Windows 安装包（推荐，普通用户）](#4-安装方式一windows-安装包推荐普通用户)
5. [安装方式二：从源码运行（开发 / Linux / macOS）](#5-安装方式二从源码运行开发--linux--macos)
6. [首次运行与模型下载](#6-首次运行与模型下载)
7. [验证安装](#7-验证安装)
8. [卸载](#8-卸载)
9. [常见问题与排错](#9-常见问题与排错)
10. [隐私说明](#10-隐私说明)

---

## 1. 简介

**pic2edit（Picture to Editable Document）** 是一款完全在本机运行的离线 OCR 桌面软件，基于
[DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR) 视觉语言模型 +
[llama.cpp](https://github.com/ggml-org/llama.cpp) 推理引擎。它能把
`jpg / jpeg / tif / tiff / png / bmp / webp` 图片和 PDF 转成可编辑的
**Markdown / Word(.docx) / 纯文本**，并保留版式、表格与坐标信息。

- **完全离线**：模型与引擎下载到本地后断网即可运行，转换过程**无任何网络上传**。
- **隐私友好**：文档与图片不出本机。
- **三后端可切换**：llama.cpp（默认、最轻量）、Ollama（可选）、Transformers（可选）。

---

## 2. 系统要求

### 2.1 硬件要求

| 项目 | 最低要求 | 推荐配置 |
| --- | --- | --- |
| **CPU** | x86-64 架构，支持 **AVX2** 指令集（llama.cpp 硬性要求） | 4 核及以上 |
| **内存** | **8 GB** | **16 GB** 及以上 |
| **显卡（GPU）** | 集成显卡可用（CPU 推理，较慢） | **NVIDIA 显卡，显存 ≥ 4 GB**（默认后端用 CUDA 12.x 加速；8 GB 显存可流畅跑 Q4_K_M） |
| **硬盘** | 见 [2.3 所需磁盘空间](#23-所需磁盘空间) | 建议 SSD，预留 ≥ 5 GB |

**说明：**
- 默认后端（llama.cpp）在 **Windows / Linux** 上使用 **CUDA** 版引擎，因此这两类平台**推荐搭配 NVIDIA 显卡**以获得实时速度。
- **macOS** 使用 **Metal** 加速（Apple 芯片 / 支持 Metal 的 Intel Mac）。
- 没有独显时仍可工作：可用 **Ollama 后端**（CPU 推理，较慢），或自行将引擎替换为 llama.cpp 的 CPU 版。性能会明显下降，但对小批量文档仍然可用。

### 2.2 软件环境要求

**使用 Windows 安装包（方式一）时，普通用户无需任何前置软件：**
- 安装包已通过 PyInstaller 冻结，**内置 Python 运行环境与全部依赖**，不要求系统已装 Python。
- **NVIDIA 用户**：需安装较新的 **NVIDIA 显卡驱动**（支持 CUDA 12.x 即可；`cudart` 运行库已随引擎打包，**无需单独安装 CUDA Toolkit**）。
- **网络**：仅在**首次运行**时需要联网以下载模型（约 2 GB）；下载完成后完全离线。

**可选后端的前置软件（非默认，按需安装）：**
- **Ollama 后端**：需安装 [Ollama](https://ollama.com) **v0.13.0 及以上**，并 `ollama pull deepseek-ocr`。
- **Transformers 后端**：需独立的 **Python 3.11** 虚拟环境（GUI 主体用 3.13，但 Transformers + bitsandbytes 在 3.13 缺预编译包，故单独隔离）。

**操作系统支持：**
- 安装包分发：**Windows 10 64-bit / Windows 11**。
- 源码运行（方式二）：额外支持 **Linux / macOS**（见第 5 节）。

### 2.3 所需磁盘空间

| 阶段 | 占用 | 说明 |
| --- | --- | --- |
| 安装包（`install.exe`） | **104 MB**（109,150,294 字节） | 经 `lzma2` 压缩的单文件安装程序 |
| 安装后程序本体 | **359 MB** | 即 `dist/pic2edit`，包含 Python 运行时、PySide6、RapidOCR 权重等 |
| 首次下载：模型 | **3.24 GB** | DeepSeek-OCR **Q4_K_M GGUF（1.95 GB）+ mmproj（0.83 GB + 0.46 GB）**；下载器当前会一并拉取两个 mmproj 变体（运行时只需其一，后续可优化） |
| 首次下载：推理引擎 | **1.2 GB** | llama.cpp 官方预编译 `llama-server`（Windows / Linux 为 CUDA 12.4 版，含 `ggml-cuda.dll` / `cublas*.dll` 等运行库；macOS 为 Metal 版） |
| **合计（首次就绪后）** | **≈ 4.8 GB** | 程序（359 MB）+ 模型（3.24 GB）+ 引擎（1.2 GB） |
| **建议预留** | **≥ 8 GB** | 含解压临时空间与后续缓存余量 |

> **重要**：模型与引擎默认下载到**安装目录**内（跟随你选择的盘符）。如果你把软件装到**非系统盘**（如 `D:\pic2edit`），模型也会落在该盘，不会占用 C 盘空间。详见第 4 节。

---

## 3. 许可证与发行说明

| 组件 | 许可证 | 说明 |
| --- | --- | --- |
| **pic2edit（本项目源码与打包 GUI）** | **MIT License** | Copyright (c) 2026 littlegreyman。可自由使用、修改、分发，需保留许可证声明。 |
| **PySide6（Qt 官方 Python 绑定，GUI 框架）** | **LGPL-3.0** | 以动态链接方式分发（符合要求）。安装包已随附 **`THIRD_PARTY_NOTICES`**；依 LGPL，你有权将 PySide6 替换为自行编译的版本或获取其源码。 |
| **llama.cpp（推理引擎）** | **MIT** | 官方预编译二进制，运行时下载，不随安装包内置。 |
| **DeepSeek-OCR 模型权重** | 依模型仓库许可证 | 由 [deepseek-ai/DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR) 发布，运行时下载，**不随安装包分发**，请以其仓库许可证为准。 |
| **RapidOCR / pypdfium2 等依赖** | Apache-2.0 / BSD / MIT | 判别式 OCR 与 PDF 渲染相关组件，MIT 兼容。 |

**发行结论：** 本项目整体可按照 **MIT License** 进行二进制分发（GUI 以动态链接方式使用 LGPL 的 PySide6，符合 LGPL 要求；`THIRD_PARTY_NOTICES` 已随安装包提供）。

**不收集、不上传：** 软件不向任何服务器上传你的文档或图片，也不含遥测；离线即可完成全部功能。

---

## 4. 安装方式一：Windows 安装包（推荐，普通用户）

适用于 Windows 10 / 11 的普通用户，无需配置 Python 环境。

1. **获取安装包**
   - 从项目的 GitHub Releases 页面下载 `install.exe`；
   - 或从仓库构建产物 `installer_out/install.exe` 取得。

2. **运行安装程序**
   - 双击 `install.exe`。
   - 安装向导会显示 **MIT 许可证**页面，阅读后继续。
   - 在「选择安装位置」页，可点击「浏览」把软件安装到**任意盘符（含非系统盘）**，例如 `D:\pic2edit`。
     默认位置为 `%LOCALAPPDATA%\pic2edit`（用户级、免管理员权限）。

3. **完成安装**
   - 可选择「创建桌面快捷方式」（默认不勾选）。
   - 完成后在开始菜单生成「pic2edit」入口；勾选「启动 pic2edit」可立即打开。

4. **首次启动与模型下载**
   - 第一次打开 pic2edit 时，程序会**自动下载**推理引擎与模型（约 2–2.5 GB，进度显示在 GUI 状态栏）。
   - 下载完成后即可离线使用。详见第 6 节。

> 安装过程**不需要管理员权限**，也不会写入系统注册表的高危项；自带标准卸载程序。

---

## 5. 安装方式二：从源码运行（开发 / Linux / macOS）

适合开发者，或需要在 Linux / macOS 上运行。此方式要求本机已安装 **Python 3.10+**（GUI 主体在 3.13 验证可用）。

```bash
# 1) 克隆仓库
git clone https://github.com/littlegreyman/pic2edit.git
cd pic2edit

# 2) 创建虚拟环境并安装依赖（Windows 用 .bat，Linux/macOS 用 .sh）
cd ocr_app
setup_venv.bat        # Windows
# ./setup_venv.sh     # Linux / macOS

# 3) 下载推理引擎与模型（只需一次，之后可离线）
download_models.bat   # Windows
# ./download_models.sh  # Linux / macOS

# 4) 一键启动 GUI（自动定位 venv，无需系统 PATH 上有 python）
#    Windows：双击仓库根的 launch_gui.bat
#    Linux / macOS：./launch_gui.sh
```

更完整的目录说明、三种后端配置与排错，见项目根 [`README.md`](README.md) 与 [`ocr_app/README.md`](ocr_app/README.md)。

---

## 6. 首次运行与模型下载

- **触发时机**：首次启动 GUI 且本地尚未就绪时，程序会自动拉起下载（也可在 GUI 工具栏点「下载模型」手动触发）。
- **下载内容**：
  1. 官方预编译 llama.cpp 引擎（Windows/Linux = CUDA 版，macOS = Metal 版）；
  2. DeepSeek-OCR 的 GGUF（**Q4_K_M**）+ mmproj。
- **耗时**：取决于网速，模型约 3.2 GB、引擎约 1.2 GB，通常十几分钟到半小时。
- **网络容错**：若 `github.com` 或 `huggingface.co` 不通，脚本会自动回退到国内镜像（如 `hf-mirror.com`）；仍失败时会给出**手动下载链接**，你也可手动把 GGUF 与 mmproj 放入安装目录下的 `models/` 后重试。
- **离线验证**：下载完成后断开网络，重新打开 pic2edit，转换功能应完全正常。

---

## 7. 验证安装

**图形界面方式（推荐）：**
1. 打开 pic2edit（开始菜单或快捷方式）。
2. 把一张图片（如 `jpg` / `png`）拖入左侧待转换列表。
3. 选中后点「转换选中」，右侧应出现可编辑的 Markdown 文本。
4. 尝试「导出」为 `.md` / `.docx` / `.txt`，确认文件可正常打开。

**命令行方式（开发者 / 无显卡也可跑大部分）：**
```bash
cd ocr_app
.venv\Scripts\python.exe _smoke_test.py     # 无界面冒烟测试（PDF/TIF/导出/三后端）
.venv\Scripts\python.exe verify_backend.py   # 后端契约自检（严格 mock，无需模型）
# 真机直连：先启动引擎，再执行
.venv\Scripts\python.exe verify_backend.py --live http://localhost:8080
```

---

## 8. 卸载

- **Windows 安装包用户**：通过「控制面板 → 程序和功能」或开始菜单的「pic2edit → 卸载」即可，会自动清理程序与卸载项。
- **残留数据**：
  - 默认安装到用户目录时，模型、引擎与配置文件（如 `config.json`、`*.log`）都在**安装目录内**，卸载或删除安装目录即可一并清除。
  - 若你曾把软件装到**只读位置**（如 `C:\Program Files`），配置会回退到 `%LOCALAPPDATA%\pic2edit`，可手动删除该目录以彻底清理。
- **源码运行用户**：直接删除仓库目录与 `ocr_app/.venv`、`ocr_app/models`、`ocr_app/bin` 即可。

---

## 9. 常见问题与排错

| 现象 | 可能原因 | 处理建议 |
| --- | --- | --- |
| 首次启动后一直「下载中」或失败 | 网络受限 | 等待自动镜像回退；或按提示手动下载 GGUF/mmproj 放入 `models/`。 |
| 转换时报错 / 无输出 | 未装 NVIDIA 驱动或显存不足 | 确认驱动支持 CUDA 12.x；改用 Ollama 后端；或减少并发。 |
| 杀毒软件报毒 / 拦截 | PyInstaller 冻结程序偶发误报 | 将安装目录加入杀毒软件白名单；程序不含任何上传行为。 |
| 打开 GUI 闪退 | 显卡/驱动不兼容或残留旧配置 | 查看安装目录内 `pic2edit_crash.log`；删除 `config.json` 后重试。 |
| 想装到其他盘 | — | 安装向导「选择安装位置」页浏览到目标盘；模型会跟随落在该盘。 |

更多后端细节与参数调优（如解决密集页面「尾部复读 / 整篇重列」的采样参数），见 [`README.md`](README.md) 的「关键技术要点」。

---

## 10. 隐私说明

- 所有 OCR 推理均在**本机**完成，图片与文档**不会被上传**到任何服务器。
- 用户配置持久化在安装目录（或 `%LOCALAPPDATA%\pic2edit`），不入库、不含个人路径或密钥。
- 模型权重与引擎二进制在运行时下载到本地，不随源码仓库分发。

---

*本指南对应 pic2edit alpha 0.19。如有疑问或建议，欢迎到项目仓库提交 Issue。*
