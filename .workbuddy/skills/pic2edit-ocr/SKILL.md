---
name: pic2edit-ocr
description: 把用户上传的图片或 PDF 转成可编辑文件（Markdown / Word / 纯文本）的本地离线 OCR 技能。当用户在对话框上传截图、扫描件、发票、表格、文档照片、证照或 PDF，并希望得到可编辑的 Markdown、Word(.docx) 或纯文本(.txt)时使用本技能。基于 DeepSeek-OCR + llama.cpp，全程本机推理、不上传。
agent_created: true
---

# pic2edit-ocr — 本地离线图片/PDF 转可编辑文件

## 用途
把用户上传的图片或 PDF 转成可编辑的 Markdown / Word / 纯文本，全程本机推理、隐私不联网。

## 何时使用
- 用户上传了图片 / 扫描件 / PDF，并说"转成 Markdown / Word / 可编辑文本 / OCR 一下 / 识别文字"等。
- 关键词命中：OCR、识别、转文字、转 Word、转 Markdown、扫描件、表格识别、发票、证照、文档照片。

## 使用流程
1. 从对话附件中取上传文件的**绝对路径**。若用户没给路径，直接问："请把图片或 PDF 发给我。"
2. 解析用户意图，映射到 helper 参数：
   - 输出格式 `--format`：Word / docx → `docx`；纯文本 / txt → `txt`；Markdown / md（默认）→ `md`。
   - 识别模式 `--mode`：表格 / 键值表 → `table`；图表 / 公式 → `figure`；纯文本 OCR → `ocr`；默认 → `markdown`。
3. 用项目虚拟环境运行 helper（`<skill>/scripts/pic2edit_convert.py` 相对本技能目录）：
   ```
   <项目根>/.venv/bin/python3 <skill>/scripts/pic2edit_convert.py <附件路径> --format <fmt> --mode <mode>
   （注意：已装好全部依赖的虚拟环境在项目根目录 .venv，不是 ocr_app/.venv；ocr_app/.venv 是空环境，用它跑会报 ModuleNotFoundError）
   ```
   - 容器内无 GPU 自测时追加 `--gpu-layers 0`（CPU 模式）；用户原生桌面**不加**（自动用 GPU）。
   - 引擎未启动会被 helper 自动拉起（首次约 1–3 分钟加载进显存）；已启动则复用，不重复拉起。
4. helper 把最终文件的**绝对路径**打印到 stdout（唯一 stdout 输出）。用 `present_files` 把该文件推给用户预览 / 下载。

## 关键约束（已用真实服务端验证，切勿违背）
- **不要在提示词里写 `<image>` 或任何 media marker**：DeepSeek-OCR 的 llama.cpp 原生模板会**自动**注入 marker 并绑定图像，客户端多写会触发 HTTP 400（"N markers vs M bitmaps"）。helper 已处理，无需手动加。
- 引擎启动**不能**加 `--chat-template deepseek-ocr`（会让多模态失效）。
- 支持输入：jpg / jpeg / png / bmp / webp / tif / tiff 图片、整页 PDF、多页 TIF（多页逐页识别并拼接为一份输出）。
- 输出落点默认与输入同目录、同名替换扩展名（与 GUI 行为一致）；可用 `--out` 覆盖。

## 参考
接口契约、提示词表与已知坑详见 `references/api_contract.md`。
