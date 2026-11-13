# 凭证 schema 包 v2：故障诊断、方案评估与实施计划（草案）

> 状态：**待确认草案**。本轮为只读取证 + 评估 + 计划，未修改任何项目代码，未提交。
> 基线：`master` / `alpha-0.20` = `999e4bd`，工作区干净。
> 触发：用户用 `上海发票-样.png` 选「表格（HTML/合并单元格）」+「增值税发票（invoice）」，
> 导出得到 `上海发票-样.md`，反馈「字段抽取不正确、看不出用了 schema、没生成 html」。
> 相关既有文档：`schema-technical-spec-v1.md`（契约）、`schema-gen-plan.md`（生成器）、
> `schemas/README.md`（教程）。冲突以规格为准。

---

## 0. 结论速览

1. **三个症状的根因都在「消费链路」，不在「schema 包内容」**：GUI 的 md/txt/docx 导出
   走了一条绕过 schema 的旁路；`html_table` 模式在 worker 里被降级成 markdown 并被
   残留坐标重建的表格覆盖；schema 抽取结果既不进编辑框也不进 md 产物。
2. **另有一处独立真缺陷**：即使走正规路径，`extract_invoice` 对这张票也会把
   `buyer.address` 抽成「密码区」、把「合计」行误当作明细项。真跑复现。
3. **5 点方案方向合理、建议采纳**，且与规格 §3.1 已写的目录契约同向（不是推倒重来）；
   但需做 4 处收敛，且**必须排在链路修复之后**——只按 5 点补齐包内容，这次的症状一个都不会消失。
4. 验收必须落到「真跑 GUI 全链路 + 逐项核对产物」，单测全绿不作为完成标准。

---

## 1. 证据链（全部为真机日志 / 磁盘产物 / 本地重跑）

### 1.1 日志时间线（`~/.pic2edit/pic2edit.log`）

| 时间 | 事件 | 关键字段 |
|---|---|---|
| 19:20:54 | 转换 #1 | `mode=markdown skip_disc=False` → `source=disc chars=553`，`schema hit: invoice v1.0.0` |
| 19:21:17 | 导出 | `format=md` |
| 19:22:52 | 转换 #2 | `mode=markdown skip_disc=True` → `source=vlm chars=45`，`schema hit` |
| 19:24:26 | 转换 #3 | 同上，`source=vlm chars=45`，`schema hit` |
| 19:25:06 | 导出 | `/home/michael/下载/上海发票-样.md`，`format=md` |

`skip_disc=True` 来自 `backends/routing.py:18`：`_SKIP_DISC_PAGE_TYPES = {"table","figure","html_table"}`。
截图那次（19:25:16 / 19:25:24）的日志已随 GUI 重启被轮转，但产物仍在磁盘。

### 1.2 产物实测

`/home/michael/下载/上海发票-样.md`：**1254 字节 / 20 行**，首行 `<!-- 第 1 页 -->`，
其余为 `|` 分隔的 pipe 表格；`schema:` 溯源 **无**，任何 HTML 标签 **无**。
同目录 **不存在** `.html` / `.json` 文件。表格中「购 名 称」与值错列、「密码区」混进
买方地址行——这是**几何行框重建**的特征，不是字段化抽取。

### 1.3 对照实验 A：同图重跑判别式 OCR → invoice 抽取

`DiscriminativeBackend.ocr_image_with_lines('/home/michael/下载/上海发票-样.png')`
→ `chars=553`，`lines=58`，`W/H=1015x655`，与日志 `chars=553` 逐字吻合。

喂给 `schemas/invoice/extract.py::extract_invoice(text, lines, W, H)`：

| 字段 | 实测值 | 判定 |
|---|---|---|
| `buyer.name` | 苏州迈思德超净科技有限公司 | 正确 |
| `buyer.address` | **密码区** | ❌ 把票面标签当地址 |
| `seller.*` | 名称/税号/地址/电话/开户行/账号 | 基本正确 |
| `header.invoice_type` | 上海普通发票 | 可接受 |
| `items[1]` | `{"name": "合", "amount": "￥49.94", ...}` | ❌ 「合计」行被当成明细 |

### 1.4 对照实验 B：正规导出路径

用同一份 OCR 原文 + 坐标构造 `InputItem`，调 `utils/export_formats.py::export_by_ext`：

| 格式 | 字节数 | schema 溯源 |
|---|---|---|
| md | 1602 | ✅ `<!-- schema: invoice@1.0.0 -->` |
| html | 8296 | ✅ `<meta name="pic2edit-schema">` + 6230 字符票面 HTML（含 `<table>` / `<style>`） |
| json | 4450 | ✅ `schema_id` / `schema_version` / 结构化字段 |

即：**能力本来就在，是 GUI 没调用它**。GUI 实际产物 1254 字节 pipe 表格 vs 正规 1602 字节
字段化 md，差异即本次故障。

### 1.5 对照实验 C：短文本下的 schema 命中

模拟 `chars=45` 的 VLM 输出喂给 `apply_schemas([...], "invoice")`：`hits=1`、`id=invoice`，
但 `buyer={}`、渲染仅剩 536 字符空骨架。解释「日志明明有 `schema hit`，用户却看不到任何字段」。

---

## 2. 根因（按优先级）

### 🔴 B1 — GUI 的 md/txt/docx 导出绕过 schema（主因）

- 位置：`gui.py:1227` `texts = [it.result for it in pages]`；`gui.py:1240-1247`
  `docx → md_pages_to_docx(texts)`、`md/txt → save_text(assemble_document(texts), path)`。
- 只有 `html` / `json` 分支（1243/1245）走 `export_html` / `export_json`。
- **为什么**：`export_formats.py:94 item_text()` 才是「命中 schema 就渲染字段化版式」的唯一入口，
  `export_markdown` 还会写 `<!-- schema: id@version -->`。旁路既不调它，也不写溯源。
- 后果：用户导出 md 时，`item.schema` 被静默丢弃；产物里找不到任何 schema 痕迹。
- 建议：GUI 的 5 个分支统一收口到 `export_by_ext(pages, path, _ext, doc.source_path)`，
  删掉手写的 `texts` 局部变量与三条旁路。

### 🔴 B2 — `html_table` 模式被降级，且被残留坐标覆盖

- 位置：`gui.py:1002` `as_table=(self.cfg.prompt_template == "table")`；
  `worker.py:109` `mode=("table" if self.as_table else "markdown")`。
- 选 `html_table` 时 `as_table=False` → pipeline 收到 `mode=markdown`；
  `worker.py:122` 走 `_maybe_layout_table()`，只要 `item.lines` 非空就用
  `build_table_from_lines()` **覆盖** `item.result`（`worker.py:239-252`）。
- 同时 `routing.py` 对 `html_table` 强制 `skip_disc=True`，VLM 只吐 45 字符；
  但 `item.lines` 是 #1 转换（`source=disc`）留下的**同一对象上的残留坐标**，
  于是「用上一轮的坐标 + 这一轮的空文本」重建出 1254 字节 pipe 表格。
- 后果：VLM 收到的 `html_table` 提示词（`config.py:48`）产出的 HTML 从未落地；
  `config.py:45-47` 注释承诺的 `utils/table_html.py` 解析 / `export_docx` 合并单元格，
  GUI 侧根本没接（`test_table_html.py` 只测了底层函数，没测 GUI 链路）。
- 建议：模式枚举统一由 `prompt_template` 透传，不再用 `as_table` 布尔降级；
  跨次转换前清理 `item.lines` 等派生坐标；`html_table` 要么接通 HTML 表格链路，
  要么在 UI 上明确标注其适用边界。

### 🔴 B3 — schema 抽的是 VLM 残文，且结果不进主线视图

- 位置：`worker.py:117-124` `raw_for_schema = item.result`（表格重建**之前**的文本），
  `self._apply_schema(item, raw_for_schema)`；`gui.py:1082` 编辑框 `setPlainText(item.result)`。
- **为什么**：本次 `raw_for_schema` 是 45 字符 VLM 残文（见实验 C），字段必然抽不到；
  `worker._apply_schema` 只挂 `item.schema*`，从不改 `item.result`；
  编辑框与预览只显示 `item.result`。
- 另：`gui.py:495` 注释写「不是导出时才生效」，与代码行为正好相反。
- 建议：`raw_for_schema` 固定取判别式全文（含 lines 坐标）；编辑框/预览增加
  「原文 / 字段化预览」切换，内部走 `item_text(it, "md"/"html")`；
  转换结束补一行 `schema hits=N/M` 汇总日志。

### 🔴 B4 — `extract_invoice` 本身存在字段错误（真跑新发现）

- 位置：`schemas/invoice/extract.py`（1349 行）。见实验 A 的两个 ❌。
- **为什么**：旧版纸电票票面的「密码区」是独立区块标签，抽取器在定位买方地址时
  沿视觉邻近取到了它；「合计」行未被排除在明细段之外。
- 加重因素：`invoice/manifest.json` 的 27 个字段仍是**旧版纸电票**模型
  （含 `header.invoice_code`、`buyer.address_phone`、`buyer.bank_account`）。
  而《国家税务总局关于推广应用全面数字化电子发票的公告》（2024 年第 11 号，
  2024-12-01 全国推广）明确数电票**删除发票代码、密码区、地址电话栏、银行账号栏**，
  号码改为 20 位。不分票种/版本，字段定义本身就会误导抽取。
- 建议：先修这两处错抽并补回归样例；再按第 3 节拆分「票种版本」。

### 🟡 S1 — 「输出格式」名字里的 HTML ≠ 导出文件格式

`表格（HTML/合并单元格）` 是 `config.py:57` 的**输出模式/提示词模板**；
导出文件格式由导出对话框的过滤器决定，本次选的是 `Markdown (*.md)`（日志 `format=md`）。
所以「没生成 html 文件」的直接原因是**没在导出对话框选 HTML**，与 schema 无关。
建议：UI 文案把「输出格式」改为「识别模式」，并在导出对话框提示
「字段化版式请选择 HTML / JSON」，避免两类「格式」混为一谈。

### 🟡 S2 — `schema hit` 日志语义误导

`worker.py:171` 的 `schema hit` 只表示 `detect` 通过（发票 `detect` 恒真，
`invoice/__init__.py:41-43`），不代表字段抽到了。建议改报
`schema hit id=invoice fields=N missing=[...]`，与「日志必须说真话」的既定纪律一致。

### 🟡 S3 — 单测绿但链路不可达

`test_capability_parity.py:6` 已记录 `html_table` 在 CLI 不可达；
`test_table_html.py` 只覆盖 `parse_html_tables` / `_add_html_table` 底层函数。
建议补一条端到端断言：GUI 选中 `html_table` 时必须真正把该 mode 传到 pipeline，
且产物中出现 `<table`。

---

## 3. 对 5 点方案的评估

**总评：合理，建议采纳。** 规格 `schema-technical-spec-v1.md` §3.1 已经列出
`layout.html`(MAY) / `samples/`(SHOULD) / `README.md`(MAY) 的目录契约，
`lint.check_samples()` 也已能跑样张命中率，`schemas.cli new` 已生成 4 件套骨架。
所以这是**把 MAY/SHOULD 升级为 MUST 并补齐内置包**，不是新架构，成本低、风险可控。

### 3.1 现状对照

| # | 方案要求 | invoice（插件式） | asset_card（声明式） | 差距 |
|---|---|---|---|---|
| 1 | `README.md` 说明/用法/特点/版本/版权 | ❌ | ❌ | 只有全局 `schemas/README.md`，包内无自解释文档 |
| 2 | 数据规范 `.xml`/`.json`（优先国标/行标） | ⚠️ `manifest.json.fields` 仅字段名列表 | ⚠️ `schema.yaml` 字段与定位策略混写 | 无独立数据字典；无标准编号/来源/版权声明；无字段类型与校验 |
| 3 | 抽取程序（1+ 个 py） | ✅ `extract.py` 1349 行 | ➖ 零 Python（声明式，由 `runtime.py` 通用引擎驱动） | 不应强制每包都有 Python |
| 4 | 版式模板 html + css | ⚠️ `html_layout.py` 用 Python 拼 | ✅ `layout.html` 84 行 | 插件包无模板文件；且 HTML 版式的合规定位未声明 |
| 5 | 输入 / 输出例子 | ❌ | ⚠️ 仅 `samples/*.txt` 输入 | 无期望输出（golden），无法锁住字段正确性 |

### 3.2 建议的 4 处收敛（避免过度设计）

**① 数据规范与定位策略必须分离（针对第 2 点）**
现有 `schema.yaml` 把「有哪些字段」与「怎么在票面上找到它」写在一起。标准是**输出数据模型**
（数电票：票面基本内容清单、20 位号码规则；T/CICPA 0110—2026：固定资产卡片数据元），
**不含任何坐标、版式或定位线索**——这正是生成器 P1–P3 已证实的结论
（规格 §6.6：自动生成字段须标 `locator: pending` 并排除 required）。
建议新增 `standard.json`（或 `.xsd`/`.xml`）只放：字段字典、类型、必填、枚举、
校验规则、标准编号、来源链接、版权与许可声明；`schema.yaml` 只放定位策略并引用它。
标准全文**不整份内嵌**（版权与体积），只引用编号 + 字段映射 + 溯源链接。

**② 不要强制每个包都有 Python 抽取程序（针对第 3 点）**
`asset_card` 零 Python 是 drop-in 设计的核心优点，强制会破坏 `registry` 的
`entry` / `schema` 双通道与 `schema_gen` 的产出。建议按 `kind` 分档写进契约：
`declarative` → `schema.yaml` + 通用 `runtime.py`（允许零自定义代码）；
`plugin` → `extract.py` / `provider.py`，并自带三套 `render_*`（规格 §3.9.3）。

**③ 版式模板改为「版式来源声明」（针对第 4 点）**
生成器明确「国标只有数据模型、没有版式 → 不生成 `layout.html`」（`schema_gen/emit.py:6`）。
对这类包强制编造版式，等于用猜测的版面冒充规范版式。建议契约改为：
每个包 MUST 在 `manifest.json` 声明 `layout` 来源之一 —— 本包 `layout.html` /
`generic`（`runtime._generic_html()` 兜底）/ `none`（仅输出结构化数据），
并在 README 写明**HTML 版式是人读预览，不是合规归档版式**。
> 合规边界：数电票入账归档需保存**含数字签名的 XML 数据电文**，版式文件为 OFD
> （国家税务总局公告 2024 年第 11 号及配套问答；电子会计档案另见 DA/T 94-2022）。
> 若用户要的是「合规版式文件」，应另立 OFD/XML 议题，不能拿 HTML 交付冒充。

**④ 输入输出例子必须包含期望输出（针对第 5 点）**
现有 `check_samples()` 只报命中率。建议 `samples/` 增加
`<name>.txt`（识别文本）+ 可选 `<name>.lines.json`（行框）+
`<name>.expected.json`（期望字段值，可声明 `skip` 字段）。
`schemas.cli test` 增加**逐字段对账**（report，不设抽取效果门禁——沿用规格 §6.6
「结构自检设门禁、效果只报告」的既定口径），并把它接入 CI。
本次 `buyer.address="密码区"` 这类错误，只有 golden 对账能长期锁住。

**⑤ 顺带：README 固定章节（针对第 1 点）**
MUST 含：功能与适用范围 / 使用方法（GUI 与 CLI 两种）/ 主要特点与已知边界 /
字段来源与标准编号 / 版本与变更记录 / 版权与许可（含第三方标准引用声明）。
由 `lint` 检查章节存在性 + 一条静态门禁测试守卫（对齐 `test_version_consistency.py` 风格）。

### 3.3 必须说清的一点

**5 点方案修不了本次 3 个症状。** 它规范的是「包里有什么」，而故障在
「转换与导出怎么消费包」。因此计划把链路修复排为 P0/P1，包结构排为 P2/P3。
若顺序颠倒，补齐 README / 数据规范 / 版式后，GUI 导出的仍是那 1254 字节 pipe 表格。

---

## 4. 实施计划

> 每 Phase 收口口径（既定纪律）：**全量 pytest 绿 + ruff 绿 + 独立自解释 commit +
> 工作区干净 + 真跑产物对账**，五者齐备才进入下一 Phase。
> 不改三级 drop-in 发现机制、不引模板引擎、不改 VLM 零幻觉优先级。

### P0 — 止血：统一导出入口（修 B1）

- `gui.py::export_selected` 删除手写 `texts` 旁路，5 个分支统一调
  `export_by_ext(pages, path, _ext, doc.source_path)`。
- `export_formats.py` 补一条 `schema_pages=N/M` 汇总日志（与 S2 一起做）。
- 验收：用同一 `InputItem` 分别经 GUI 导出函数与 `export_by_ext` 导出 md/txt/docx/html/json，
  **逐字节相同**；md 含 `<!-- schema: -->`、html 含 `<meta name="pic2edit-schema">`、
  json 含 `schema_id`。新增 `test_gui_export_uses_single_entry`（静态断言不再出现旁路调用）。

### P1 — 抽取正确性与模式路由（修 B2/B3/B4/S1/S3）

- `worker.py`：`prompt_template` 全量透传，去掉 `as_table` 布尔降级；
  每轮转换前清理 `item.lines/img_w/img_h` 派生坐标；`raw_for_schema` 固定取
  判别式全文 + 坐标。
- `gui.py`：编辑框/预览增加「原文 / 字段化预览」切换，走 `item_text()`，
  **默认原文**（决策 4）；修 `gui.py:495` 与代码相反的注释；
  「输出格式」改名「识别模式」并加导出提示。
- `schemas/invoice/extract.py`：修 `buyer.address="密码区"`、排除「合计」行入明细；
  补 3 份 golden 样张（含本次上海普票）。
- **`html_table` 从 GUI 下拉删除**（决策 2）：保留后端模式字典与 CLI/skill 支持，
  只过滤 GUI 白名单；新增静态门禁断言下拉不含该模式；S3 的端到端断言改为
  「GUI 可选模式集合 == 白名单」而非锁定 `<table>` 产物。
- 验收：用 `/home/michael/下载/上海发票-样.png` 真跑 CLI + 本地 GUI 导出，
  逐项对账 `buyer.address` 不再是「密码区」、`items` 无「合」、
  md/html/json 三种产物均含 schema 且字段一致；`source=disc` 页不再触发坐标重建覆盖。
  > 沙箱可跑判别式 OCR（本轮已验证），但 OCR 识别质量仍须用户原生桌面复核。

### P2 — 包结构契约 v2（规格 + 门禁 + 工具链）

- 修订 `schema-technical-spec-v1.md` §3.1：把 `README.md` / `standard.json` /
  `samples/*.expected.json` 提升为 MUST，新增 `kind` 分档与「版式来源声明」三态；
  §3.13 补 lint 规则表；`schemas/README.md` 同步教程。
- `lint.py`：新增 ① README 固定章节 ② `standard.json` 可解析且与 `schema.yaml`
  字段对账 ③ 版式来源声明合法 ④ expected 样例结构；`check_samples()` 增逐字段对账（report）。
- `schemas/cli.py new`：模板补齐 5 件套；`pack` 明确包含新文件并在缺件时拒绝（除非 `--force`）。
- `schema_gen/emit.py`：产出补 `README.md` / `standard.json` / expected 骨架；
  保持「无版式 → 不生成 layout.html」，改为写 `layout: none` 声明。
- 新增 `test_schema_package_contract.py` 静态门禁：所有内置包 + 生成器产物满足 v2 清单，
  缺件即测试红；负例真跑（故意删 README/standard，观察门禁变红后恢复）。

### P3 — 内置包补齐与票种分版

- `invoice/`：补 `README.md`、`standard.json`（区分**纸电票**与**数电票**两套字段模型，
  标注 2024 年第 11 号公告与 20 位号码规则）、`layout.html` 或 `layout: none` 声明、
  `samples/`（输入 + lines + expected，至少 3 张：本次上海普票、内置江苏样张、数电票骨架）。
  `manifest.json` 增 `standard` 引用、`ticket_types` 与 `license` 字段。
- `asset_card/`：补 `README.md`、`standard.json`（引用 T/CICPA 0110—2026，
  标注「字段取舍参考、非标准全文」）、补齐 expected 输出样例。
- `THIRD_PARTY_NOTICES` / `README.md` / `VERSION` 同步；按既有 Check List ⑥ 更新全部说明文档。

### P4 — 真机验收与发布收口

- 用户原生桌面执行：同一张发票分别导出 md / html / json / docx，核对：
  ① 字段化票面可见；② `buyer/seller` 逐字段正确；③ html 文件确实生成且可打开；
  ④ 溯源注释含 `invoice@<version>`；⑤ 切「不使用」导出与命中版**逐字节不同**；
  ⑥ 编辑器与预览能看到字段化视图（不再只有 OCR 散文）。
- 全量 pytest + ruff；远端 CI 在 HEAD 上 `conclusion=success`；
  `git status --short` 为空；版本位 / 说明文档 / 合规署名对账。

---

## 5. 验收清单（真跑，不以单测绿代替）

```
# 结构门禁
cd ocr_app && ./.venv/bin/python3 -m pytest -q && ./.venv/bin/python3 -m ruff check .
python -m schemas.cli list
python -m schemas.cli validate schemas/invoice
python -m schemas.cli validate schemas/asset_card
python -m schemas.cli test invoice        # 逐字段对账报告

# 产物对账（用真实发票）
python - <<'PY' ... # 判别式 OCR -> apply_schemas -> export_by_ext 导出 4 格式
grep -c 'schema: invoice' out.md
grep -c 'pic2edit-schema' out.html
sha256sum  # 「走缓存 vs 不走缓存」「命中 vs 不命中」逐字节对照
PY
```

判定：任一产物缺 schema 溯源、字段与 golden 不符、GUI 与 CLI 产物不一致 → 不算完成。

---

## 6. 风险与边界

- **HTML 版式不是合规归档文件**（无数字签名、无监制章套印）；入账归档需含数字签名的
  XML 数据电文，版式为 OFD。本计划只做「人读预览」，不做 OFD 生成。
- **标准引用**：只写编号、字段映射、来源链接与版权声明，不整份内嵌受版权保护的标准全文；
  P3 实施时需核实所引标准原文、版本年份与授权范围。
- **抽取效果不设 CI 门禁**（沿用规格 §6.6）：expected 样例只报告差异；
  否则不同机器 OCR 质量波动会让 CI 无故变红。结构完整性才设门禁。
- **不扩大改动面**：不动 `registry.py` 的三级 drop-in、`runtime.py` 的渲染降级、
  `ocr_pipeline.py` 的判别式优先顺序；上一轮诊断的「引擎按需拉起」缺口另立议题，
  不并入本计划（避免一次改动混入两个目标）。
- 预计改动文件：`gui.py`、`worker.py`、`config.py`、`schemas/invoice/*`、
  `schemas/asset_card/*`、`schemas/lint.py`、`schemas/cli.py`、`schema_gen/emit.py`、
  `docs/schema-technical-spec-v1.md`、`schemas/README.md`、新增门禁测试与样例。

---

## 7. 决策记录（2026-11-13 用户确认，取代原「待确认事项」）

| # | 决策 | 对计划的影响 |
|---|---|---|
| 1 | **数电票独立 schema id**（`invoice_digital`），不在 `invoice` 包内分版 | P3 新增独立包；两个 detect 必须互斥，见下方约束 |
| 2 | **`html_table` 从 GUI 输出格式下拉中删除** | P1 只删 UI 入口，**不删** `PROMPT_TEMPLATES` / `routing` 的后端支持（CLI/skill 保留）；新增门禁防止被加回 |
| 3 | **草案随 P0 首个 commit 一同提交** | P0 commit 含本文档，message 自解释 |
| 4 | **编辑器默认「原文」** | P1 切换默认档位 = 原文；命中 schema 时可切到字段化预览，但不自动跳 |

### 决策 1 的实施约束（重要）

`invoice.detect()` 当前**恒真**（`invoice/__init__.py:41-43`，票种判定放在 extract 里靠坐标）。
新增 `invoice_digital` 若也恒真，`auto` 模式会永远命中优先级高的那个，另一个不可达。
因此 P3 MUST：

- `invoice_digital.detect` 用数电票特征收窄：含「电子发票（普通发票）/电子发票（增值税专用发票）/
  数电票」等票名，或 20 位发票号码；排除「发票代码 / 密码区」等纸电票特征；
- `invoice.detect` 改为**排除数电票特征**（或至少不再恒真），使两包互斥；
- 优先级序保持 `asset_card=20 < invoice_digital < invoice=100`，新增静态门禁校验
  `list_schemas()` 无重复 id 且优先级不冲突；
- 两包共用抽取逻辑时抽到 `schemas/_invoice_common/`（下划线开头、无 `manifest.json`，
  registry 扫描自动跳过），**不复制** 1349 行 `extract.py`。

### 决策 2 的实施约束

删除 GUI 下拉项后能力边界变化：`html_table` 仍可由 CLI/skill 的 `--mode` 触发，
`test_capability_parity.py` / `test_skill_cli.py` 的门禁继续有效；GUI 侧新增静态断言
防止该模式被重新加回下拉。`config.py` 的 `PROMPT_LABELS` 保留全部 5 项作为模式字典，
GUI 填充时按白名单过滤，避免「删了下拉、后端字典也跟着残缺」。

### 决策 4 的实施约束

编辑器默认原文是**保持现有行为**（`setPlainText(item.result)`），
字段化预览作为显式切换；因此 P1 不改动 `_on_item_done` 的默认路径，
只新增一个受控入口，降低回归风险。

---

## 8. 实施进度

| Phase | 状态 | 验证 |
|---|---|---|
| P0 统一导出入口 | ✅ `61358a8` | 全量 323 passed ×2、ruff 绿；GUI 5 种格式与 `export_by_ext` 逐格式对账 |
| P1 抽取正确性与模式路由 | ✅ 本 commit | 全量 330 passed ×2、ruff 绿；脱敏上海票端到端 md 1445B 含 `<!-- schema: invoice@1.0.0 -->`、无「密码区」/「合」错值 |
| P2 包结构契约 v2 | ✅ 本 commit | 全量 345 passed ×2、ruff 绿；规格升 v1.2、lint/cli/emit 落地、15 项门禁（含 6 个缺件负例真跑） |
| P3 内置包补齐 | ⏳ 待做 | 含决策 1：**独立 `invoice_digital` 包**，与 `invoice` 的 detect 必须互斥 |
| P4 真机验收与发布收口 | ⏳ 待做 | 用户原生桌面；CI 在 HEAD 上绿；版本位与说明文档对账 |

P1 落地要点（与 §4 的差异说明）：
- `html_table` 按决策 2 只从 GUI 下拉移除（`config.GUI_MODE_KEYS` 白名单），
  后端 `PROMPT_TEMPLATES` / `routing` / CLI / skill 全部保留；
  配置里残留的非法模式在 `_sync_template_combo()` 显式回落到 markdown。
- 编辑器按决策 4 默认原文；新增只读的「字段化预览」勾选框，
  `_on_edit` 在只读态不回写，避免渲染结果覆盖原文。
- `worker.run()` 每页转换前清 `lines/img_w/img_h`，切断跨轮坐标污染。
- 抽取器两处修复 + 脱敏 golden 样张 `testdata/shanghai_invoice_ocr.json`
  （公司名/人名/地址/银行与所有长标识串均已脱敏，测试内含结构自检）。
- 草案 P1 原列「补 3 份 golden 样张」，本轮先落 1 份真机样张 + 既有
  `testdata/invoice_sample.py`；其余 2 份（数电票骨架等）随 P3 补齐。

P2 落地要点（与 §3.2 / §4 的差异说明，已同步进规格 v1.2 的 D4/D5）：

- **草案两处字段名与现有实现冲突，实施时已修正**：① §3.2 ② 原写「按 `kind`
  分档」，但 `kind` 是包自报的来源标签（`builtin`/`user`），规格 §3.3 与 C10
  明确禁止把它当能力判据，`test_list_shows_load_origin_not_declared_kind` 也钉住了；
  ② ③ 原写「`layout` 声明三态」，但 `layout` 已是**版式文件名**，
  `registry._build` / `runtime.DeclarativeProvider` 直接拿它当路径读。
  因此 v2 改用新增字段 **`form`**（`declarative|plugin`）与
  **`layout_source`**（`package|generic|none`），原有字段语义不变、装载零改动。
- **渐进纳管是 P2 能自身收口的必要设计**：P2 建立门禁、P3 才补齐内置包，
  若门禁在 P2 就要求所有包满足 v2，P2 必然测试红。故 v2 MUST 只对显式声明
  `contract_version: 2` 的包生效；`schemas.cli new` 与 `schema_gen.emit` 原生产 v2，
  内置包未声明时只给一条 `info` 升级提示。P3 补齐文件并声明后自动纳入硬检查，
  门禁测试 `test_undeclared_builtin_packages_stay_green_with_info` 已为那一刻留好断言。
- **生成器产物 4 → 7 文件**：新增 `README.md` / `standard.json` /
  `samples/skeleton.expected.json`；`manifest.json` 写 `layout_source: none`，
  **仍不生成 `layout.html`**（标准没有版式，不编造）。两处「产物文件集合」的
  既有精确断言（`test_schema_gen.py` / `test_schema_spec.py`）已同步。
- **golden 逐字段对账只报告、不设门禁**：`check_samples()` 增 `_flatten_doc()`
  展平两种 doc 形状，`cli test` 打印匹配/不符/缺失/跳过。差异不进 lint issues、
  不影响退出码，沿用规格 §6.6 口径，避免不同机器 OCR 波动让 CI 无故变红。
- **负例真跑证据**（完整骨架 `error=0`，逐项破坏后各得 1 条 error）：
  删 README / 删 standard.json / 删 expected / `form=bogus` /
  `layout_source=none` 却留 layout 文件 / schema.yaml 加标准外野字段。
  `pack` 在缺 standard.json 时退出 1 且不产 zip，`--force` 例外仍可打包。
- **CLI 真跑**：`new → validate（无问题）→ test（命中率 100%，golden 匹配 1）
  → pack（8 文件，sha256 打印）` 全绿；生成器 `verify()` 结构层与 auto 不误命中
  门禁全过，效果层仍诚实标注「未验证」（`gated=False`）。
