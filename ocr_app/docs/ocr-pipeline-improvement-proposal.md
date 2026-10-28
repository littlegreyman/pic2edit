# pic2edit 本地 OCR 流水线改进技术方案（待确认）

> 目标：把 pic2edit 从「单一生成式 VLM（DeepSeek-OCR）直出」改造成「**判别式 OCR 优先 + 版面感知 + VLM 兜底**」的稳健流水线，
> 消除你在《深圳市注册会计师协会章程》PDF 上复现的**确定性幻觉**（如第 2 页整页被读成英文/模板文），并保持全离线、GUI + WorkBuddy skill 两条入口不变。
>
> 状态：**仅方案，未执行**。所有改动待你确认后实施。
>
> **分支策略（已确认）**：开发在**主线 `master`** 上进行，**不在 `feat/pic2edit-ocr-skill` 上改**。详见 §7。

---

## 0. 问题根因（已实测确认）

| 现象 | 根因 | 证据 |
|---|---|---|
| 章程 PDF 第 2 页整页英文/模板幻觉 | DeepSeek-OCR 是**生成式**模型，对清晰印刷体也会确定性编造；非分辨率/参数问题 | 第 1 页（清晰）正确、第 2 页（同样清晰）两次输出逐字一致地错误 |
| docx 里混入 270 段英文废话（35%） | `input_prep.prepare_pdf` 把**每页内嵌图形**也单独 OCR 一遍，模型对这些图读不到文字就脑补 | `ocr_app/utils/input_prep.py:69-85` 的 step 2 抽取逻辑 |
| `failed to restore kv cache` 报错 | 连发多页请求后 KV cache 状态复用区被占满 | llama.cpp server 日志，请求会回退重算但不致命 |
| 同类开源对比 | MinerU / Marker / Docling / Pix2Text 均用「判别式 OCR + 版面检测」流水线，几乎不幻觉；DeepSeek-OCR 在 LabelYourData 实测版面保持从基准 ~90% 跌到 75–80% | 见调研记录 |

---

## 1. 总体架构

```
输入(图片/PDF)
   │
   ├─[预处理]  input_prep：整页渲染 + 版面检测裁剪（剔除页眉/页脚/页码/logo/装饰）
   │
   ├─[识别层]  ★判别式 OCR 优先（RapidOCR / PaddleOCR）
   │              ├─ 成功(置信度高、字数足够) → 直接用
   │              └─ 失败(空/置信度低/手写/复杂表单) → 降级到 VLM
   │
   ├─[VLM 兜底] DeepSeek-OCR（仅处理判别式搞不定的“智能页”）
   │
   ├─[表格层]  表格区域 → 专用表格识别（pix2text-table-rec / TableFormer）→ Markdown/Word 表
   │
   └─[导出]    统一 Markdown 中间格式 → md / txt / docx（复用 utils/export_docx.py）
```

**核心原则**：干净印刷体（占真实文档 90%+）走判别式 OCR，零幻觉；DeepSeek-OCR 只保留给低显存机器的「智能页兜底」，不再是唯一引擎。

---

## 2. 分阶段方案

### 阶段 0 — 引擎稳健性（低风险速赢）
- **改文件**：`ocr_app/run_llamacpp_server.py` 的 `build_command()`（行 101-102）
- **做法**：在启动命令行追加 `--cache-reuse 0`，关闭 KV 状态缓存复用。
- **资源**：无（纯配置）。
- **验证**：重启引擎 → 连发 25 页 → 日志无 `failed to restore kv cache`。
- **工作量**：10 分钟。

### 阶段 1 — 修复「内嵌图被 OCR」bug（直接消除 35% 废话）
- **改文件**：`ocr_app/utils/input_prep.py:69-85`（`prepare_pdf` step 2）
- **做法**（二选一，推荐 b）：
  - a) 直接删除 step 2，只 OCR 整页（最简单，但丢失 PDF 中真正的独立插图文字）。
  - b) **只保留「大尺寸/独立」内嵌图**（面积 > 整页 30% 且非极小宽高比），跳过 logo/装饰/页码类小图。
- **资源**：无（纯代码）。
- **验证**：转章程 PDF → 英文废话段数应降到 0（配合阶段 2 后）。
- **工作量**：0.5–1 小时。

### 阶段 2 — 判别式 OCR 兜底（治幻觉的核心）
- **新增文件**：`ocr_app/backends/discriminative_backend.py`（封装 RapidOCR）
- **做法**：
  - `rapidocr_onnxruntime` 提供 det + rec + cls 三模型 ONNX 流水线，CPU/GPU 皆可，对中文印刷体近乎零幻觉。
  - 路由：每页先跑判别式；返回文本长度 ≥ 阈值 且 平均置信度 ≥ 阈值 → 采用；否则降级 VLM。
  - 在 `backends/__init__.py` 暴露 `DiscriminativeBackend`，与现有 `LlamaCppBackend` 并列。
- **资源**：
  - 包：`rapidocr_onnxruntime`（拉入 `onnxruntime`）。
  - 模型：PP-OCRv4 det(~4MB) + rec(~11MB) + cls(~3MB)，首次 import 从 PP 模型库/ModelScope 下载 → **需联网一次**，之后离线；应预置到 `models/` 保证真离线。
  - 显存：CPU 即可（数百 MB RAM）；`onnxruntime-gpu` 可选加速。
  - 磁盘：+~50MB。
- **风险**：`onnxruntime` 在 **Python 3.14** 的 wheel 需验证（见 §4 决策点 1）。
- **验证**：章程第 2 页 → 判别式应干净读出「第六条…第二章 党建工作…第十条」；与之前 VLM 输出对比。
- **工作量**：0.5–1 天。

### 阶段 3 — 版面检测（精准裁剪 + 智能路由）
- **做法**：
  - 轻量起步：**复用 RapidOCR 的检测框**作为伪版面（零新增依赖），按版面顺序拼接文本块、剔除页眉页脚/页码区域。
  - 进阶（如需）：引入 `DocLayout-YOLO`（opendatalab）区分 text/title/figure/table/header，输出结构化区块。
- **改文件**：`ocr_app/utils/input_prep.py`（用版面框替换「整页 + 内嵌图」逻辑，等价于阶段 1 的彻底版）
- **资源**：
  - 轻量方案：0（复用阶段 2 的检测框）。
  - DocLayout-YOLO：pip `doclayout-yolo`（依赖 ultralytics+torch，较重 ~1GB）；或用其 **ONNX 导出**免 torch。
  - 显存：ONNX 可纯 CPU。
- **验证**：页眉「深圳市注册会计师协会」、页码「2 / 25」不再串入正文；表格区被单独标出。
- **工作量**：轻量 0.5 天 / 进阶 1–2 天。

### 阶段 4 — 表格结构化（替换 VLM 直出表格）
- **做法**：版面检测标出的 table 区域 → 专用表格识别：
  - 推荐 `pix2text-table-rec`（breezedeus）→ 输出 LaTeX/HTML 表；
  - 或 MinerU 表格模块（TableMaster）。
  - 识别结果经 `ocr_app/utils/table_utils.py` 转 Markdown/Word 表（该模块已存在，复用）。
- **资源**：`pix2text-table-rec` pip + 模型 ~100MB；纯 CPU 可跑。
- **验证**：章程/发票类表格 → 合并单元格、跨页表还原正确。
- **工作量**：1–2 天。

### 阶段 5（中长期可选）— 高保真 VLM 后端
- **做法**：接入 **MinerU** 作为「≥16GB 显存机器」的高保真后端；DeepSeek-OCR 保留给 6GB 低显存机器。
- **资源**：MinerU 完整依赖较重（torch 等），显存 8GB+；不强制，按机器能力可选。
- **工作量**：3–5 天（且需 Python 版本协调）。

---

## 3. 资源汇总

| 组件 | 包 | 磁盘 | 显存 | 内存 | 联网(一次性) | Python 3.14 |
|---|---|---|---|---|---|---|
| 阶段0 引擎flag | 无 | 0 | 0 | 0 | 否 | ✅ |
| 阶段1 内嵌图修复 | 无 | 0 | 0 | 0 | 否 | ✅ |
| 阶段2 RapidOCR | rapidocr_onnxruntime | ~50MB | 0(CPU)/<1GB | ~500MB | 模型下载一次 | ⚠️ 验证 |
| 阶段3 版面 | 复用检测框(0) / doclayout-yolo | 0 / ~100MB | 0/1GB | 1GB | 模型一次 | ⚠️ 验证(torch) |
| 阶段4 表格 | pix2text-table-rec | ~100MB | 0/1GB | 1GB | 模型一次 | ⚠️ 验证 |
| 阶段5 MinerU(可选) | mineru | 大 | 8GB+ | — | 是 | 需 3.10–3.12 |

**总计（阶段 0–4）**：新增磁盘 ~250MB，纯 CPU 可跑；唯一硬约束是 Python 3.14 下 `onnxruntime/torch` 的 wheel 可用性（见下）。

---

## 4. 待你确认的决策点

1. **Python 版本**：当前 venv 是 **3.14.4**。`onnxruntime`/`torch` 在 3.14 的预编译 wheel 可能缺失。
   - 方案 A：先验证 3.14 wheel 是否存在，存在则直接装在现有 venv；
   - 方案 B（更稳）：新建一个 **Python 3.12 venv** 专跑判别式/版面/表格流水线，pic2edit 主程序仍用 3.14，两者通过子进程/HTTP 衔接。
   - **我倾向 B**，避免把重型依赖塞进主 venv 引发连锁冲突。

2. **离线模型分发**：RapidOCR/DocLayout/表格模型首次需联网下载。要真正“断网可用”，需把这些 ONNX 模型预置进 `models/` 并在代码里指向本地路径。是否现在就做离线预置？

3. **默认引擎**：建议把 **RapidOCR 设为默认、DeepSeek-OCR 降级兜底**（保真优先）；还是你仍想 DeepSeek-OCR 默认、判别式仅作失败回退？

4. **实施范围**：本次做阶段 0–4（根治幻觉，纯 CPU 可跑），还是连阶段 5（MinerU 高保真后端）一起？

5. **skill 同步**：改进后 `.workbuddy/skills/pic2edit-ocr/scripts/pic2edit_convert.py` 应改为调用新流水线（而非只调 VLM）。该更新在 **master** 上进行（见 §6 步骤 4），是否一并做？

---

## 5. 对现有 skill 的影响
- `pic2edit_convert.py` 的 `ensure_server()` 逻辑不变（仍探活/拉起引擎）；
- 新增判别式/版面/表格在「探活失败时」仍可降级到本地 CPU 推理（不依赖 :8080 的 GPU 引擎），意味着**即使不拉起 llama-server，也能产出可编辑文件**（只是“智能页”质量下降）。
- 导出与提示词契约（`references/api_contract.md`）保持不变。

---

## 6. 分支与提交策略（重要）

**结论：开发在主线 `master` 上进行，不在 `feat/pic2edit-ocr-skill` 上改代码。**

当前状态（已核实）：
- `master` = `42f2912`（含 logo / README / 窗口图标等）
- `feat/pic2edit-ocr-skill` = `773c3ae`（含 skill 三件套 + `.gitignore` 例外），**该提交尚未进入 master**
- 因此 master 上目前**没有** `.workbuddy/skills/pic2edit-ocr/`

推荐落地步骤：
1. **先把 skill 合回主线**：`git checkout master && git merge feat/pic2edit-ocr-skill`
   （把已有的 skill 源码 + `.gitignore` 调整并入 master，mainline 即具备 UI + Agent 两条入口）。
2. **在 master 上做流水线改进**：直接在 `master` 提交阶段 0–4，或另开 `feat/ocr-pipeline`（从 master 分出）再合回 master——**两个分支都基于 master，不基于 skill 分支**。
3. **`feat/pic2edit-ocr-skill` 分支冻结**：不再往它提交新改动；它作为「skill 初版」的历史分支保留，待 master 包含等价内容后可废弃/删除。
4. **skill 同步更新**（决策点 5）在 master 上进行：改 `.workbuddy/skills/pic2edit-ocr/scripts/pic2edit_convert.py` 调用新流水线，随阶段 2/3 的提交一起进 master。

> 不采用「在 skill 分支上继续开发」的原因：skill 分支定位是「skill 打包」，混进识别内核改动会让分支职责不清、且 master 主线拿不到改进。主线开发保证 OCR 内核与 skill 单一事实来源都在 master。

## 7. 工作量估算（阶段 0–4）
- 阶段 0 + 1：~1 小时
- 阶段 2：0.5–1 天
- 阶段 3：0.5–2 天
- 阶段 4：1–2 天
- 联调 + 回归（用章程 PDF + 发票样例验证无幻觉）：0.5–1 天
- **合计：约 3–7 个工作日**（取决于阶段 3 是否上 DocLayout-YOLO、决策点 1 选 A/B）。
