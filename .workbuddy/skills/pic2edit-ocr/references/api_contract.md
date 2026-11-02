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

| 输入 | md | txt | docx | json | html |
|---|---|---|---|---|---|
| 图片（jpg/png/...） | ✅ | ✅ | ✅ | ✅ | ✅ |
| 整页 PDF | ✅ | ✅ | ✅ | ✅ | ✅ |
| 多页 TIF | ✅（逐页拼接） | ✅ | ✅ | ✅ | ✅ |

- `html` 列指 `--format html`：仅对能结构化（如发票）的页面渲染为带真实 `<table>` 的离线 HTML；非结构化页面退化为 `<pre>` 原文本。

## 7. 环境约束

- 依赖 venv 在 **`ocr_app/.venv`**（主环境，含 requests / Pillow / pypdfium2 / python-docx / PySide6 / rapidocr>=3.10）。判别式 RapidOCR 由 helper 经子进程调用 `ocr_app/.venv`（rapidocr 已支持 Python 3.14，无需独立 `.venv_ocr`），无需手动切 venv。
- 选取 venv 里的 python（跨平台）：Windows 用 `ocr_app\.venv\Scripts\python.exe`，Linux / macOS 用 `ocr_app/.venv/bin/python3`。
  ```bash
  # Windows
  ocr_app\.venv\Scripts\python.exe <skill>/scripts/pic2edit_convert.py <输入> --format <fmt> --mode <mode>
  # Linux / macOS
  ocr_app/.venv/bin/python3 <skill>/scripts/pic2edit_convert.py <输入> --format <fmt> --mode <mode>
  ```
- helper 自动定位 `ocr_app`：优先 `PIC2EDIT_OCR_APP` / `PIC2EDIT_ROOT` 环境变量，否则按常见用户目录（`~/Documents/pic2edit`、`~/文档/pic2edit`、`~/projects/pic2edit` 等）兜底。
- 首次启动引擎约 1–3 分钟加载模型进显存；6GB 显存跑 Q4_K_M（≈2GB）可行。
- 引擎默认常驻；`--stop-server` 可在本次拉起后退出。
- 容器无 GPU：传 `--gpu-layers 0` 走 CPU（需足够内存加载模型）。

## 8. 新增开关与结构化输出（skill 层优化，不影响 ocr_app）

- `--cache`：按 `sha256(文件内容) + mode + 版本号` 缓存识别结果到 `临时目录/pic2edit_cache/<key>.json`；命中时直接复用并按请求格式重新渲染，重复预处理秒回。模型权重更新后 bump 脚本内 `CACHE_VERSION` 即可失效旧缓存。
- `--prov`：md / txt / docx 输出中，每个页面前插入 `<!-- engine:xxx -->` 注释（不破坏渲染），标明该页来自 `disc` / `vlm` / `table` / `unknown`。
- `--format json`：输出结构化 JSON（写入 `.json` 文件并打印路径）：
  ```json
  {
    "source": "<输入绝对路径>",
    "vlm_fallback_invoked": true,
    "pages": [
      {"page": 1, "text": "...", "engine": "disc"},
      {"page": 2, "text": "...", "engine": "vlm"}
    ]
  }
  ```
  该结构可直接喂给入库一致性校验脚本（切片↔原文相似度比对），`engine` 字段用于把 VLM 兜底段挑出来人工复审。
- 逐页 `engine` 判定方式（skill 层、零侵入）：当判别式不可用时，用「探针包装」VLM 后端记录哪些图像真正走了 VLM 兜底；跳过判别式时全部记 `vlm`；表格模式且表格识别可用时记 `table`。不修改 pic2edit 项目任何代码。

## 9. 密码区智能掩膜（skill 层，不修改 ocr_app）

- 启用：`PIC2EDIT_MASK_PASSWORD=1`。
- 定位（仅读 `DiscriminativeBackend._raw` 的带坐标输出，不改 pic2edit）：
  1. 找「密码区」竖排标签（兼容整词 / 单字竖排两种形态）；
  2. 取标签右侧褐色密码框（宽≈标签高 3~4 倍，y 不越过「税率/金额」表头，避免误伤货物明细）；
  3. 校验该区文字是否「全为数字/符号乱码（无汉字）」，通过则采用，否则回退 `DEFAULT_VAT_RECT=(0.50,0.20,0.95,0.44)`。
- 手动覆盖：`PIC2EDIT_MASK_RECT="x0,y0,x1,y1"`（比例，0~1）。
- 默认关闭（opt-in）。掩膜在主进程内用 PIL 涂白生成临时副本，再送 OCR，不写回原图。

## 10. 表格 / HTML 呈现（skill 层渲染）

- `--mode table` 时，若页面能重建为结构化单据（如发票，`_extract_invoice` 命中「发票」关键词），则：
  - md / docx → 真实 Markdown 表格（docx 经 `md_to_docx` 转 Word 表格）；
  - txt → 用 TAB 分隔列；
  - 非结构化页面回退原 `kv_text_to_markdown_table` 键值表。
- `--format html`：把结构化结果渲染为带 `<section>`/`<table>` 的离线 HTML（含 `engine` 溯源注释若 `--prov`），适合「表格套表格」/ 公式 / 图表等复杂对象的网页化核对。
- 结构化渲染依赖 RapidOCR 的逐行坐标（在 `--mode table` 或 `--format html` 时按需再调一次 `_raw`），与 ocr_app 既有逻辑无关。
