# pic2edit-ocr 接口契约与提示词表

本文件给 agent 在调用 `scripts/pic2edit_convert.py` 时参考，覆盖 llama.cpp / DeepSeek-OCR
的真实接口细节与已验证的坑。

## 1. 服务端点（OpenAI 兼容）

- 默认地址：`http://127.0.0.1:8080`（由 `run_llamacpp_server.py` 启动）。
- 探活：`GET /health` 或 `GET /v1/models` 返回 200 即就绪。
- 推理：`POST /v1/chat/completions`，请求体：
  ```json
  {
    "model": "deepseek-ocr",
    "messages": [
      {"role": "user",
       "content": [
         {"type": "text", "text": "<提示词，绝不写 media marker>"},
         {"type": "image_url", "image_url": {"url": "data:image/png;base64,<B64>"}}
       ]}
    ],
    "temperature": 0.0,
    "repeat_penalty": 1.10,
    "repeat_last_n": 4096,
    "max_tokens": 16384
  }
  ```
- 拆包：`choices[0].message.content` 即模型输出的 Markdown / 纯文本。

## 2. 已验证的坑（切勿违背）

- **文本里不要写 `<image>` 或任何 media marker**（也不要 GET `/props` 拿 marker）。
  DeepSeek-OCR 的 llama.cpp 原生模板会**自动**注入 marker 并绑定 image_url 到视觉编码器；
  客户端多写一个会变成 "2 markers vs 1 bitmap" 直接 400。
- **服务端启动不能加 `--chat-template deepseek-ocr`**：裸模板不自动注入 marker、
  也不把 image_url 接到视觉塔，结果要么 400 要么只回废话。`run_llamacpp_server.build_command`
  已故意省略该参数，沿用即可。
- 温度必须 0（贪心/确定性），否则密集页面会随机退化重列；`repeat_last_n` 必须放大到 4096
  才能抑制长程复读（默认 64 不够）。这些已在 helper 的请求 payload 与启动参数里设好。

## 3. 提示词表（PROMPT_TEMPLATES，来自 config.py）

| mode 参数 | 提示词 | 说明 |
|---|---|---|
| `markdown`（默认） | `<|grounding|>Convert the document to markdown.` | 带版面/坐标感知的 Markdown |
| `ocr` | `Free OCR.` | 纯文本 OCR |
| `figure` | `Parse the figure.` | 图表 / 公式 |
| `table` | `<|grounding|>Convert the document to markdown.` | 正常 OCR 后由 `kv_text_to_markdown_table` 做键值→表格后处理 |

> `<|grounding|>` 是模型 token，跨后端有效；仅 markdown/table 用。

## 4. 输入预处理（utils/input_prep.prepare_input）

- 图片（jpg/jpeg/png/bmp/webp）：直接作为一个图像项。
- TIF/TIFF：多帧逐帧拆成独立图像项。
- PDF：整页渲染为高 DPI 图像 + 抽取内嵌图形，均为图像项。
- 每个图像项落地为临时 PNG，便于后端调用；多页结果拼接为一份输出。

## 5. 导出（utils/export_docx）

- `md` / `txt`：`save_text(text, out)` 直接写 UTF-8。
- `docx`：`md_to_docx(text, out)` 覆盖标题 / 有序·无序列表 / `|` 表格 / `**加粗**`。

## 6. 支持矩阵

| 输入 | 输出 md | 输出 txt | 输出 docx |
|---|---|---|---|
| 图片（jpg/png/...） | ✅ | ✅ | ✅ |
| 整页 PDF | ✅ | ✅ | ✅ |
| 多页 TIF | ✅（逐页拼接） | ✅ | ✅ |

## 7. 环境约束

- 必须用 `ocr_app/.venv/bin/python` 运行（含 requests / Pillow / PyMuPDF / python-docx）。
- 首次启动引擎约 1–3 分钟加载模型进显存；6GB 显存跑 Q4_K_M（≈2GB）可行。
- 引擎默认常驻；`--stop-server` 可在本次拉起后退出。
- 容器无 GPU：传 `--gpu-layers 0` 走 CPU（需足够内存加载模型）。
