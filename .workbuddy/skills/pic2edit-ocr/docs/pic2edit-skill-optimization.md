# pic2edit-ocr Skill 优化方案（待审阅，未实施）

> 范围说明：本文档为**方案**，不含任何代码改动。当前仅分析 `~/.workbuddy/skills/pic2edit-ocr/` 下三个文件（`SKILL.md`、`scripts/pic2edit_convert.py`、`references/api_contract.md`）的可优化点，并区分「skill 层可改」与「需改 pic2edit 项目内部」两类。
>
> 用户既有约束：**不改 pic2edit 项目源码（Documents/pic2edit）**，除非另行授权。因此 B 类（涉及 ocr_app 内部）仅列出，不实施。

---

## 0. 背景与目标

该 skill 是 pic2edit 项目的薄封装：通过 `_find_ocr_app()` 定位 `ocr_app`，复用其 `input_prep / export_docx / ocr_pipeline / llamacpp_backend / discriminative_backend` 等能力，跑「RapidOCR 判别式优先 + DeepSeek VLM 兜底 + 幻觉守卫」流水线，导出 md/txt/docx。

优化目标（对齐行业协会"无幻觉入库"诉求）：
1. **更稳**：修兜底逻辑漏洞，避免特定路径静默失败。
2. **更可追溯**：让下游 curation 工作流能识别"哪段是 VLM 兜底产生的（高风险）"。
3. **更快**：对反复采编的同一文档做结果缓存。
4. **更友好的入库**：结构化输出直接喂给入库一致性校验闸。

---

## A 类：仅改 skill 层（安全，不碰 pic2edit 项目）

### A1. 修复 VLM 兜底的服务启动漏洞（优先级：高）
**问题**：`convert()` 中只有 `if not skip_disc and not disc_ok:` 才拉起 VLM 服务。但一旦 `skip_disc=True`（用户设 `PIC2EDIT_SKIP_DISCRIMINATIVE=1` 强制跳过，或 Unix 内存受限自动跳过），`pipeline_run(use_vlm_fallback=True, skip_discriminative=True)` 仍会调 VLM，却**没有服务可连**，该路径会失败（除非外部已手动起了 :8080）。

**修复方向**：将"是否确保服务就绪"的判断从 `disc_ok` 改为"**VLM 兜底可能被用到**"——即 `use_vlm_fallback=True` 且 `skip_disc=True` 时，也必须 `ensure_server()`。建议新增独立判断：
```python
need_vlm_server = (not skip_disc and not disc_ok) or (skip_disc)
if need_vlm_server:
    backend, proc = ensure_server(url, gpu_layers)
```
并补一条文档说明：当强制跳过判别式时，本 helper 会自行拉起 CPU/GPU 的 llama-server。

### A2. 加结果缓存 `--cache`（优先级：高，对你反复采编极有价值）
**做法**：在 `convert()` 入口计算 `cache_key = sha256(输入文件字节 + mode + 引擎版本标识)`；若 `--cache` 开启且命中，直接打印缓存结果路径并退出。缓存落点用 `os.path.join(tempfile.gettempdir(), "pic2edit_cache", cache_key + "." + fmt)`。
**收益**：同一法规/通告多次重跑时秒回，省掉大量重复 OCR。

### A3. 输出加「来源引擎 + 置信度」溯源标记（优先级：高，直击无幻觉）
**做法**：新增 `--prov` 开关。开启时，导出文本在每页/每段前加行内标记，例如：
```
[engine:disc|vlm][conf:0.92] <该段文本>
```
**前提**：当前 `pipeline_run` 的 `it.result` 是否携带 engine/conf 未知——若 ocr_app 未透传，则 A3 需要 B3 配合（见下）。若暂不能拿到，可降级为：在 stderr 打印"本次共 N 段，其中 M 段来自 VLM 兜底，建议人工复核"，至少给出汇总级告警。
**收益**：curation 工作流的"切片↔原文一致性校验闸"可据此对 VLM 段提高抽检比例或直接转人工。

### A4. 加结构化 JSON 输出 `--format json`（优先级：中）
**做法**：`--format json` 时输出：
```json
{"source":"<输入绝对路径>","pages":[{"page":1,"text":"...","engine":"disc|vlm","conf":0.9}]}
```
**收益**：直接喂给 `curation-workflow.md` 里的入库脚本，比解析 md 文本干净。

### A5. 修脆弱的最后兜底路径（优先级：低）
**问题**：`return os.path.abspath(os.path.join(HERE, "..","..","..","..","ocr_app"))` 当 skill 位于 `~/.workbuddy/skills/pic2edit-ocr/scripts/` 时，解析为 `~/.workbuddy/ocr_app`（几乎必错）。
**修复**：删掉该兜底，或改为在找不到时打印明确错误并退出（提示设置 `PIC2EDIT_OCR_APP`）。

### A6. 补 SKILL.md 关键提醒（优先级：中）
- 明确写：**`table`/`figure` 模式会跳过判别式、直接走 VLM，幻觉风险更高**；对法规/处罚通告等带表格件，建议人工复核输出。
- 标注 `PIC2EDIT_SKIP_DISCRIMINATIVE=1` 的用法及其依赖（需本机已有 :8080 服务或由 helper 自动拉起）。
- 说明"Windows 上 `_vmem_limited_gb()` 因无 `resource` 模块恒返回 0，内存自跳过不生效"这一事实。
- 更新顶部 description：当前写"基于 DeepSeek-OCR + llama.cpp"，建议补"前置 RapidOCR 判别式，VLM 仅兜底"，与实际流水线一致。

### A7. 清理死代码（优先级：低）
`MODE_TO_PROMPT` 在 `convert()` 中未被使用（`pipeline_run` 内部按 `mode` 自行映射），可删除或改注释说明。

---

## B 类：需改 pic2edit 项目内部（需用户授权，当前不实施）

### B1. 判别式引擎从 PP-OCRv3 级 RapidOCR 升级到 PP-OCRv5 / PP-StructureV3（优先级：最高质量收益）
我们此前读 `ocr_pipeline.py` / `discriminative_backend.py` 确认，前置是 v3 代际 RapidOCR。升级后表格/版面/手写更强，**更多表格件不落到 VLM 兜底**，从源头再压低幻觉。
**风险**：需替换模型权重与推理封装，回归测试表格/多栏件。

### B2. 收窄 `table`/`figure` 跳过错判的适用范围
现状（GUI `worker.py`）：`skip_disc = self.as_table or (prompt_template in ('figure','html_table'))`，表格件直接走 VLM。应让表格识别尽量走判别式/PP-StructureV3，减少 DeepSeek VLM 出场。

### B3. 让 `ocr_pipeline` 透传「每页用的引擎 + 置信度」
这是 A3/A4 能真正生效的前提：在 `ImageItem` 上附加 `engine` 与 `confidence` 字段，供 helper 读写。

---

## 推荐落地顺序（若后续授权）

1. 先 A1（修漏洞）+ A6（补文档）——零风险、立即提升稳定性与可用性。
2. 再 A2（缓存）+ A5（修兜底）——提升反复采编效率与健壮性。
3. 然后 A3/A4（溯源/JSON）——需与 curation 工作流打通，价值最大。
4. 最后视需要谈 B 类（根因降幻觉），需你明确授权碰 pic2edit 项目。

---

## 工作量估计
- A1/A5/A6/A7：约 0.5–1 天，纯文档+脚本微调，无新依赖。
- A2/A3/A4：约 1–2 天，需确认 ocr_app 透传能力（决定 A3 是否降级）。
- B 类：约 3–5 天，含模型替换与回归测试，需项目源码改动授权。
