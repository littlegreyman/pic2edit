# pic2edit 需求实现完整性审查

日期：2026-11-06
范围：11,183 行非测试源码 + 1,739 行 skill 前端 + 55 条需求项
方法：逐条读实现 → 与文档承诺/用户需求对账 → 关键项实测复现 → 核对测试覆盖
基线：`e018ffa`（本地 master，领先 origin 8 个提交），170 passed / ruff 全绿
**本次未改动任何源码。**

---

## 0. 需求基线怎么来的

这个项目没有独立的 SRS。可核查的"需求"散落在四处，我按这四处重建基线：

| 来源 | 性质 | 条目数 |
|---|---|---|
| 用户在本项目中的明确指令（双工作流、三态、三案例、目录输入、六条硬约束） | 硬需求 | 21 |
| `SKILL.md`（skill 对调用方的行为承诺，事实上的接口规格） | 契约 | 15 |
| `README.md` / `ocr_app/README.md` / `schemas/README.md`（对用户的公开承诺） | 承诺 | 15 |
| `pic2edit_refactor_plan_20261102.md` + Phase 3 重排计划（验收门禁） | 门禁 | 4 |

合计 **55 条**功能需求。逐条判定为 完整 / 部分 / 未实现。

---

## 1. 计分卡

```
完整     39  ████████████████████████████████████░░░░  71%
部分      8  ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  15%
未实现    8  ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  15%
另有 5 条文档与实现不符（不重复计入上面 55 条）
```

分域看：

| 需求域 | 完整 | 部分 | 未实现 | 判断 |
|---|---|---|---|---|
| 双工作流 / schema 三态 | 8 | 2 | 1 | 架构真正落地了，只差 CLI 收尾 |
| 三案例（国标/无版式/自定义） | 2 | 1 | 1 | ①②③ 都成立，但"从国标生成"没做 |
| 声明式运行时（Phase 4） | 8 | 1 | 0 | 质量最高的一块 |
| 零幻觉 / 路由统一 | 6 | 0 | 0 | 达标 |
| **能力对等（Phase 3 门禁）** | **3** | **1** | **3** | **只做了一半** |
| 导出与格式 | 2 | 1 | 1 | GUI 缺忠实版式 |
| skill CLI 契约 | 8 | 2 | 0 | 缓存一处真 bug |
| 输入形态 | 2 | 0 | 2 | 目录 / PDF 内嵌图形 |

一句话结论：**Phase 3/4 的设计重构是成功的——"schema 嵌进转换流程"那类病根确实拔掉了，代码里再找不到第二处同类问题。但收尾没做完：3 项已识别的 GUI→skill 能力缺口一项没补，1 处缓存路径让 schema 静默失效，5 处文档还在描述已经不存在的行为。**

---

## 2. 先说做对的（这不是一份只有问题的报告）

这几条我原本按"最可能翻车"去查，结果都是干净的：

**双工作流真的分离了。** `pic2edit_convert.py` 里 `grep 发票|invoice|增值税` 只剩注释，零票种知识。B 侧入口是独立的 `python -m schemas.cli`，与转换入口没有任何调用关系。`test_skill_imports_only_facade` 静态守着 skill 只许 import 门面。这正是用户点名的"不要把 schema 嵌进扫描流程"。

**三态的三个通路都是真的通的，不是写了参数没接。** GUI 下拉写 `cfg.schema_mode` → `OcrWorker` **逐页** `_apply_schema`；skill `--schema` 在**任何昂贵动作之前** `resolve_schema` 校验（写错 id 不会让用户等完 OCR 才报错）；`auto` 由注册表按 priority 逐页 detect-extract。`none` 时**连 `schema` 键都不写**——这个设计很关键，否则"没启用"和"启用了没命中"事后无法区分。

**声明式运行时的硬骨头啃下来了。** 三级取值（regex → 标签同行 → 坐标锚点）、表格段强制 `start`/`stop`、`strict` 整页未命中、渲染前剥离 HTML 注释、`{{ }}` 与 `data-repeat` 两种语法不引模板引擎——每一条都有测试且都是踩过坑才加的。`asset_card` 零 Python 跑通，样张命中率 100%，`auto` 不抢发票有测试守着。

**零幻觉架构没被破坏。** GUI 没有绕过 pipeline 直喂 VLM 的路径；`RoutingPolicy` 两侧同源；引擎关停全路径走 `EngineLauncher.stop()`（有 `test_gui_close_uses_process_group_stop` 守着）。

---

## 3. P1：三处会让人"按文档用，结果不对"的缺口

### 3.1 `--cache` 命中后 schema 静默失效（已实测复现）

**这是本次审查唯一一处"功能在特定开关下直接不工作"的问题，且文档明确承诺过它工作。**

文档怎么说：

- `SKILL.md`：「缓存**只存识别结果**……`schema` 是派生数据，写入缓存前被剔除、**每次读取时现算**。好处：改进抽取逻辑后不需要 bump `CACHE_VERSION` 重跑昂贵的 OCR，**缓存命中的页面立刻按新逻辑重新抽取**。」
- 代码注释 `pic2edit_convert.py:165-167` 也写着「命中时会补做重复护栏**与 schema 抽取**」。

代码实际怎么做（`pic2edit_convert.py:820-839`）：

```python
if os.path.isfile(cf):
    data = json.load(f)
    for _pg in data.get("pages") or []:        # 只补做了重复护栏
        _clean, _r = _trim_repetition(_rt)
        ...
    print(_render(data, fmt, out_path, input_path, prov, mode))   # 直接渲染
    return                                      # ← 全程没有 apply_schemas
```

而写缓存时 `schema` 被剔除（`:1021-1031`），`_render` 注释又明说「只有 `--schema none` 时 page 里才没有 schema 键，**此时绝不重抽**」。两处注释互相矛盾，结果是：**缓存命中 → `page["schema"]` 恒为 None → 一律走"无 schema"分支**。

**实测证据**（同一份 `asset_card` 样张，先证明它能命中，再走缓存路径）：

```
apply_schemas(auto) -> 1 {'asset_card': 1} schema_id= asset_card     ← 应得的
[cache] 命中，直接复用识别结果
[render] 命中凭证 schema 0/1 页 → 按原图版面还原                      ← 实得
包含 schema 标题『一、基本信息』: False
与原始样张逐字相同（=未做 schema 渲染）: True
```

**影响**（不是"少了个美化"，是产物契约被破坏）：

1. 同一份发票，第一次转出「严格按票面版式 + 中文栏目 + `（未识别）` 占位」，第二次（`--cache`）转出 OCR 原文。**同一输入两次导出不一致**。
2. 导出的 JSON 里 `"schema": {"mode":"auto","hits":0,...}` 是缓存里那份旧元数据——**元数据在说谎**，RAG 侧会据此判断"这页不是凭证"。
3. `--schema invoice` 强制指定时同样失效，用户会以为 schema 没写对。

**为什么 170 项测试没拦住**：`grep cache test_*.py` 无命中。缓存路径**零测试覆盖**。

**修法**（约 8 行）：缓存命中分支在 `_render` 之前补

```python
stats = apply_schemas(data.get("pages") or [], schema_mode)
data["schema"] = stats
if stats["mode"] == MODE_NONE:
    data["schema"]["note"] = "schema 未启用（--schema none）"
```

并加一条测试：`--cache` 两遍输出逐字节相同。建议同时把 `schema_mode` 纳入缓存键（见 4.6）。

---

### 3.2 skill 不支持目录 / 多文件输入（用户明确要求过，未实现）

用户在 2026-11-05 的双工作流定义里写得很明确：

> **工作流 A（消费/转换）**：输入 扫描图 / 多页 PDF / TIFF / **目录** → GUI 或 Skill 转换导出 → 闭环结束。
>
> **工作流 A 的入口缺口**：Skill 只收单文件（`add_argument("input", ...)`），GUI 已支持多选+文件夹；用户明确要求「目录」→ **Skill 需补**。

现状：`pic2edit_convert.py:1038` 仍是 `nargs="?"` 单个位置参数，`:711` 是 `os.path.isfile` 检查（传目录直接 `exit(2)`）。GUI 侧有「打开文件夹」（`gui.py:506`）。

这不是"没想到"，是**发现了、写进了计划、然后没做**。Phase 3 重排后 3.2~3.5 五项里没有它，收口清单里也没有它。

**影响**：批量场景（一个月的票据一个目录）只能由 Agent 层循环调用 N 次，每次重新 `prepare_input`、重复 `log_store.purge`、重复选模型。而 Agent 侧循环最容易踩的是**引擎被反复拉起**——虽然 `available()` 会复用，但 `--cache` 命中率会因目录级去重缺失而下降。

**修法**：`input` 改 `nargs="+"`；`os.path.isdir` 时 `os.walk` 收集受支持的扩展名；输出路径按 `--out` 是否给出分派（给目录则逐文件同名落盘）。需同步 `SKILL.md` 的用法段与 `--out` 语义。

---

### 3.3 GUI→skill 的 3 项能力缺口，一项都没补

2026-11-05 的能力审计明确列出"GUI 独有、且判定该下沉"的 3 项，连修法都写好了。Phase 3.3 只做了反向（skill→GUI：手写预筛 / 选模型 / 掩膜），这三项原样留着：

| 缺口 | GUI 有 | skill 现状 | 定位 |
|---|---|---|---|
| **`html_table` 模式不可达** | `config.py:57` 有「表格（HTML/合并单元格）」，`export_docx` 有 `cell.merge()` 还原合并单元格 | `--mode` choices 只有 4 个，无 `html_table` | `pic2edit_convert.py:1040-1041` |
| **采样参数不可调** | `SettingsDialog` 4 个参数 + preset，`worker.py:115` 传给 pipeline | 恒 `build_engine_command(exe, model, mmproj)`，无 `model_params` | `:253` |
| **引擎失败诊断缺失** | `free_vram_mib` / `estimate_vram_mib` 显存预检 + `[engine]` 输出落盘 + 平台化提示 | 自拉起时 `stdout=DEVNULL, stderr=STDOUT`，**引擎输出直接丢弃**，失败只报 `code=-6` | `:260-261` vs `server_launcher.py:62,83,155` |

第三项最要命：用户在 Linux 上遇到 `exit -6` 时，GUI 会告诉你"需约 2.6GB，空闲 1.0GB，可能被 Ollama 占用"，而 skill 只给一个 `code=-6`。同一台机器、同一个失败、两个入口的诊断质量差一个数量级。这正是 Phase 2.2"生命周期统一了、诊断没统一"的遗留。

`test_capability_parity.py` 只守了已下沉的三项（`test_facade_exposes_sunk_capabilities` 的清单里没有引擎诊断），所以这条缺口没有任何门禁会提醒。

**修法**：把 `ServerStarter` 里非 Qt 的「预检 + 拉起 + 输出落盘 + 失败提示」抽成 `backend_api.ensure_engine()`，两侧共用；`--mode` 加一个 choice；`--temperature/--repeat-penalty/--repeat-last-n/--max-tokens` 四个 flag 透传 `ModelParams`。

---

## 4. P2：局部成立，但承诺比实现多

### 4.1 GUI 侧没接忠实版式，案例②在 GUI 不成立

`SKILL.md` 写：「三档，从保真度高的往下退（实现在内核 `utils/faithful_html.py`，**两侧共用**）」。

实测调用方：只有 `pic2edit_convert.py`（`:585`）。`utils/export_formats.py:125-158` 的 `export_html` 在无 schema 时走 `markdown.markdown(...)`——**HTML 重排**，正是文档说"不该用"的做法。

用户的三案例里，②「只规定数据格式 → 抽取按 schema、**复现尽量按来源版式**」是明确需求。现在：
- skill 导 html → 原图垫底 + 透明文字层（符合）
- GUI 导 html → markdown 重排（不符合，且版面会被推断错）

同一份文件从两个入口导出，html 长得不一样。Phase 3 门禁「GUI 与 Agent 对同文件产出一致质量」未达成。

**修法**：`InputItem` 已有 `image_path`，`export_html` 无 schema 时先试 `page_with_image`，失败再退 `markdown`。约 6 行。

### 4.2 产物不记录 schema 版本（硬约束⑤只做到一半）

用户定的硬约束：「产物记录 schema id + **版本/hash**（可追溯）」。

`registry.py:235` 只写 `page["schema_id"]`；`export_formats.py:174` 只写 `row["schema_id"]`。**`version` 没有任何落点**，尽管 `base.py` 的协议注释写着「版本号；**产物会记录它**，便于解释「同一批文件两次导出为何不同」」。

后果与 3.1 是同一类：schema 从 1.0.0 改到 1.1.0（比如补了一个字段），历史产物无法回答"按哪版抽的"。

**修法**：`apply()` 加 `page["schema_version"]`；两处导出带 `schema_version`；json 顶层加 `schema` 汇总（id → version）。

### 4.3 schema CLI 缺 `pack`（硬约束⑥）

用户原文：「B 侧独立 CLI（`schema new/lint/test/pack`）与转换 CLI 不同入口」。

`cli.py:222-242` 只有 `list/validate/test/new`。缺 `pack`——即「把制作好的 schema 目录打成可分发的一个包」。当前要靠用户手动 `tar`/拷目录，且没有校验包完整性（manifest 的 id/version 与实际文件是否一致）。

严格说 `new/validate/test` 已覆盖"做→查→验"闭环，`pack` 只影响分发。但它是用户点名的四项之一。

### 4.4 `--list-schemas` 的"来源"列不可信

`SKILL.md`：「`--list-schemas` 列出（含 id / 版本 / **来源** / 标题）」。
`cli.py:137` 打印的是 `it['kind']`，而 `registry.py:172` 是 `provider.kind = manifest.get("kind", origin)`。

`asset_card/manifest.json` 里写死了 `"kind": "builtin"`。用户按 `schemas/README` 的说明「把内置 schema 拷出来改」到 `~/.pic2edit/schemas/asset_card` 后，`kind` 仍是 `builtin`——**列表告诉你这是内置的，实际生效的是你改过的那份**。

这不是小瑕疵：覆盖机制（"后者覆盖前者"）的全部意义就是让人敢改，而改完无法确认生效的是哪份，机制就废了一半。

**修法**：`info_of()` 加 `origin`（扫描时已知），CLI 打印 origin 而非 kind；kind 保留为"插件类型"（declarative/plugin）。

### 4.5 缓存键不含识别层参数

`SKILL.md` 自己承认：「想验证「识别层」的改动（**换模型**、改预处理）必须**换缓存键**，否则看到的是旧 OCR 结果。」

但 `:816` 的键是 `sha256(文件)|mode|CACHE_VERSION`，**不含 `--vlm-model`、不含 `PIC2EDIT_SKIP_DISCRIMINATIVE`**。用户 `--cache --vlm-model q4` 跑完再 `--cache --vlm-model q8`，第二遍直接命中第一遍的 Q4 结果，且日志说"命中"。

文档把责任推给用户（"必须换缓存键"），却没给换键的手段（只能手工清 `/tmp/pic2edit_cache`）。

**修法**：键里并入 `vlm_model 档位` 与 `skip_disc`；或加 `--no-cache`/`--cache-bust`。

### 4.6 skill 的 `txt` 与文档说的"同 md 分派"不一致

`SKILL.md`：「`txt` / `docx`：同 `md` 的分派逻辑（txt 用 `标签：值` + TAB，docx 走 md→docx）」。

`_render` 实际（`:667-676`）：`txt` 无 schema → `blk = raw`（**不做表格还原**）；`md`/`docx` 无 schema → `blk = _layout_table_md(p) or raw`。所以同一份两栏对照表，`--format md` 出表、`--format txt` 不出。

这可能是**刻意的**（txt 就不该有 md 表语法），但文档说不一致；要么改文档，要么改代码。2026-11-04 的记忆里也记了这条边界（"txt 导出仍为纯文本（不排表）"），说明是有意为之——**该改的是文档**。

---

## 5. 文档失真（承诺了不存在的行为）

这几条不影响代码正确性，但直接影响"用户按文档操作会不会得到预期结果"，且都是**可以一行改掉的**：

| # | 文档说 | 实际 |
|---|---|---|
| 1 | `README.md:27`、`ocr_app/README.md:17`：PDF「**抽取 PDF 内嵌图形作为独立图像项**」 | `input_prep.py:88` docstring 明写「**不再抽取**每页内嵌图」。功能已移除，两处 README 未同步 |
| 2 | `ocr_app/README.md:19`：「**分辨率模式**：Tiny / Small / Base / Large / Gundam（动态多尺度，适合复杂版面）」 | `config.py:95` 只有字段定义，**全项目零消费点**——GUI 下拉把值写进状态栏文字（`gui.py:599`），从不传给引擎。这是伪能力 |
| 3 | `README.md:175`：「下拉提供**四种**模式」 | 实际五种（`config.py:57` 多了「表格（HTML/合并单元格）」） |
| 4 | `README.md:254`：当前 alpha **0.19** | `VERSION:1` 首行是「pic2edit **0.18**」，且无 0.19 的变更记录 |
| 5 | `SKILL.md` 掩膜节：「该特性……属 **skill 层预处理**，**不修改 pic2edit 项目**」 | Phase 3.3 已下沉到 `backends/mask.py`，`pic2edit` 内核已改。措辞停留在 10 月底 |

第 2 条我最在意：一个"看起来很专业"的分辨率下拉，选了没反应，且 README 为它背书。用户会以为是模型能力不够，而不是参数没接。**要么接上（`transformers_server.py --mode` 已消费它），要么从 GUI 和 README 一起删掉。** 2026-11-05 的审计就写了「反而该记一个清理项（接上或删）」，没清。

第 4 条影响发布：`alpha-0.20` 的 tag 该配一份 0.19 之后的变更记录。

---

## 6. 讨论过、但没纳入任何阶段的需求（不算违约，但要知道）

### 6.1 从国标文档生成 schema —— 能力不存在

用户问过「如果从已经有国家标准格式的文档生成 schema 呢」，当时的分析给出了可落地方案：`schema_gen/{parse_pdf_datemeta, parse_xbrl, to_schema, lint}.py`，能把 T/CICPA 0110—2026 的 83 个数据元、PPE.xsd 的 342 字段机械转成 `schema.yaml`。

`grep schema_gen|xbrl|ElementTree` → **全项目零命中**。Phase 4 做的是"手写 yaml 的运行时"，不是"从标准生成 yaml 的生成器"。

这不影响已交付的三案例（①手工写两个文件也是成立的），但它意味着：**面对一个 342 字段的国标，用户仍要手写 342 条 yaml**。用户当初问这个问题的动机（"让用户新增工作更少"）没有被满足。

### 6.2 合规电子票据的内嵌 XML 被丢掉重识别

2026-11-05 的决定性发现：财政部推的财政电子票据是「**内嵌 XML 的 PDF**」（财会〔2025〕9 号），而 `input_prep.py` 对 PDF 一律 `page.render()` 成位图，**从不 `get_textpage()`、不解析 XML**。

这意味着：对**已经有精确结构化数据**的文件，本项目选择丢掉它、重新 OCR 一遍。这跟"schema 抽取"的整个方向是相反的——最准的抽取是"不抽取，直接读"。

### 6.3 PDF 文本层未利用

同一处：有文本层的 PDF（如那份两栏对照的法规 PDF、国标 PDF）也全量 OCR。对扫描件这是对的，对原生 PDF 是浪费 + 精度损失（RapidOCR 对原生文本层字符的识别率低于直接读文本层）。

6.2 与 6.3 是同一条链路上的两级，建议合并成一个 Phase 5 议题：**「输入分诊」——先判断这份 PDF 有没有可靠的结构化来源，有就直接用，没有再 OCR。**

---

## 7. 建议的修复顺序

按「用户可感知的错误 ÷ 改动成本」排序：

| 顺序 | 事项 | 成本 | 收益 |
|---|---|---|---|
| 1 | **缓存命中补 `apply_schemas` + 测试**（3.1） | ~10 行 | 消除"同一文件两次导出不一致"，且是文档已承诺的行为 |
| 2 | **5 条文档失真**（§5） | 改 5 处文案 | 用户不再按文档操作得到意外结果 |
| 3 | **GUI `export_html` 接 `page_with_image`**（4.1） | ~6 行 | 案例②在 GUI 侧成立；两侧 html 一致 |
| 4 | **产物补 `schema_version`**（4.2） | ~5 行 ×2 处 | 满足硬约束⑤，产物可追溯 |
| 5 | **`--mode` 加 `html_table`；4 个采样参数 flag**（3.3） | ~15 行 | 能力对等补齐 2/3 |
| 6 | **抽 `ensure_engine()` 进门面**（3.3） | ~60 行 | skill 侧拿到与 GUI 同级的失败诊断 |
| 7 | **`--list-schemas` 打印 origin**（4.4） | ~5 行 | 覆盖机制可用 |
| 8 | **缓存键并入档位/跳过标志**（4.5） | ~3 行 | 缓存不再骗人 |
| 9 | **skill 支持目录输入**（3.2） | ~40 行 | 批量场景从 N 次调用降到 1 次 |
| 10 | **`cli pack`**（4.3） | ~40 行 | 满足硬约束⑥ |
| 11 | 输入分诊（PDF 文本层 / 内嵌 XML）（§6.2-6.3） | 大 | 从"能识别"到"不该识别时不用识别" |
| 12 | 国标 schema 生成器（§6.1） | 大 | 342 字段从手写变生成 |

1–8 建议作为一次 `fix(completeness)` 提交，全部有测试；9–10 单独一次；11–12 需要单独立项。

---

## 附录 A：完整核查矩阵

图例：✅ 完整 ／ 🟡 部分 ／ ❌ 未实现 ／ 📄 文档失真

### A. 双工作流与 schema 架构（11）
| # | 需求 | 状态 | 证据 |
|---|---|---|---|
| A1 | A 侧零 schema 可跑 | ✅ | `registry.apply` 在 none 时不写键 |
| A2 | schema 目录 drop-in，不从代码注册表读 | ✅ | `registry._roots` 三级扫描 |
| A3 | 单一显式选择点 `none\|auto\|<id>` | ✅ | `--schema`；GUI 下拉同值 |
| A4 | A 侧只读 schema 包 | ✅ | 无写回路径 |
| A5 | 产物记录 schema id + 版本/hash | 🟡 | 有 id，无 version（4.2）|
| A6 | B 侧独立 CLI `new/lint/test/pack` | 🟡 | 缺 pack（4.3）|
| A7 | 转换 CLI 不认识具体票种 | ✅ | `grep 发票` 仅注释 |
| A8 | A 侧支持目录输入 | ❌ | `:1038` nargs="?"（3.2）|
| A9 | GUI 三态选择 | ✅ | `gui.py:487-491` |
| A10 | skill 显式声明 | ✅ | `--schema` |
| A11 | 程序自动判断 | ✅ | priority + detect |

### B. 三案例（4）
| B1 | ①数据格式+现成版式 → 双文件 | ✅ | `asset_card` 零 Python |
| B2 | ②只有数据格式 → schema 抽取 + 按来源版式 | 🟡 | skill ✅ / GUI ❌（4.1）|
| B3 | ③用户自定义 schema + 版式 | ✅ | `cli new` + 用户目录覆盖 |
| B4 | 从国标自动生成 schema | ❌ | 零实现（6.1）|

### C. 声明式运行时（9）
| C1 | detect 四规则 any/all/none/regex | ✅ |
| C2 | 三级取值 regex→同行→坐标锚点 | ✅ |
| C3 | 表格段强制 start/stop | ✅ |
| C4 | `strict` 整页未命中 | ✅ |
| C5 | `{{ }}` + `data-repeat` | ✅ |
| C6 | 注释剥离 + HTML 转义 | ✅ |
| C7 | 允许扩展字段 | ✅ | yaml 任意 key 均可 |
| C8 | 双向 lint | 🟡 | 表格列未进版式不告警（`lint.py:188,199`）|
| C9 | 样张命中率 | ✅ | `check_samples` |

### D. 零幻觉与路由（6）
| D1 | 判别式优先 | ✅ |
| D2 | VLM 兜底 + 幻觉守卫 | ✅ |
| D3 | 两侧同一路由策略 | ✅ | `RoutingPolicy` 同源 |
| D4 | 手写预筛默认关 + 可强制 | ✅ |
| D5 | 全文重复护栏 | ✅ | 幂等，缓存分支也补 |
| D6 | 逐页 engine 溯源 | ✅ |

### E. 能力对等（7）
| E1 | skill→GUI 手写预筛 | ✅ |
| E2 | skill→GUI 按显存选模型 | ✅ |
| E3 | skill→GUI 密码区掩膜 | ✅ |
| E4 | GUI→skill 引擎诊断 | ❌ | `server_launcher.py:62,83` 未下沉 |
| E5 | GUI→skill `html_table` | ❌ | `:1040` choices 缺 |
| E6 | GUI→skill 采样参数 | ❌ | `:253` 无 model_params |
| E7 | 同文件同质量 | 🟡 | html 版式不同；json `text` 语义不同 |

### F. 导出（4）
| F1 | 5 种格式 | ✅ |
| F2 | schema 命中走 schema 渲染 | ✅ | 5 格式全接 |
| F3 | 忠实版式两侧共用 | ❌ | 仅 skill（4.1）|
| F4 | txt 同 md 分派 | 🟡 | 文档与实现不一致（4.6）|

### G. skill CLI 契约（10）
| G1 | 5 种 `--format` | ✅ |
| G2 | 4 种 `--mode` | ✅ |
| G3 | `--cache` | 🟡 | 命中后 schema 失效（3.1）|
| G4 | `--prov` | ✅ |
| G5 | `--vlm-model` | ✅ |
| G6 | `--gpu-layers` | ✅ |
| G7 | `--list-schemas` 含来源 | 🟡 | 打印 kind 非 origin（4.4）|
| G8 | `--schema` 三态 | ✅ |
| G9 | 日志 10 天 + 完整字段 | ✅ |
| G10 | settings 三层优先级 | ✅ |

### H. 输入（4）
| H1 | 图片 / PDF / TIF | ✅ |
| H2 | 多页 TIF 逐帧 | ✅ | `prepare_tif` ImageSequence |
| H3 | PDF 内嵌图形独立成项 | ❌ | 已移除，README 未同步（§5-1）|
| H4 | PDF 文本层 / 内嵌 XML 利用 | ❌ | 6.2 / 6.3 |

---

## 附录 B：复现缓存问题

```bash
cd <repo>/ocr_app
CODEBUDDY_SAFE_DELETE_ENABLED=0 .venv/bin/python /tmp/verify_cache_schema.py
```

脚本逻辑：证明样张能命中 `asset_card` → 按真实写缓存规则造一份"剔除 schema"的缓存 →
`convert(..., use_cache=True)` → 断言输出是否含 schema 版式。当前结果：不含。

---

## 附录 C：本次审查没有发现的问题（澄清）

- **不重复上一轮安全报告**：CVE 对照、下载链路零校验、HTML 注入、`rowspan` 未 clamp 等已在 `CODE_REVIEW_2026-11-06.md`，此处不赘述。
- **未发现第二处"schema 嵌进转换流程"**：`grep -r 发票 ocr_app/schemas` 之外，转换侧零票种知识。
- **未发现架构级新增耦合**：front → facade → kernel 的单向依赖没有被新代码破坏。
