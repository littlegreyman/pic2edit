# pic2edit 凭证 schema 技术规格 v1.1

> 状态：**规范性文档（Normative）** · 2026-11-06 · 适用 `ocr_app/schemas/` 全部实现与「schema 生成流程」
> 本文定义的是**契约**，不是教程。教程见 `schemas/README.md`，skill 对调用方的行为承诺见
> `.workbuddy/skills/pic2edit-ocr/SKILL.md`，转换侧的接口说明见 `references/api_contract.md`。
> 本文若与那三份文档冲突，以本文为准；发现冲突时应改那三份文档，不要改本文去迁就实现。
>
> **v1.1 修订（2026-11-06，生成器立项）**：补齐两处会让生成器「自己骗自己」的契约漏洞——
> §6.4 生成包 MUST 写保守 `detect`（否则 auto 模式误命中每一页）、§6.6 自检拆成
> 结构自检与抽取效果自检两层（`strict:false` 下 `hit_rate` 恒 1.0 是假通过）；
> §5.9 补生成器 CLI 行；§6.1/§6.5/§6.7 把「342 字段」参数化——官网实测
> `PPE.xsd` 为 `xs:element` 361 / `complexType` 10 / `simpleType` 297，不得硬编码；
> §3.13 与 §7 的 D1/D2/D7 状态按实现进度更新。执行计划见 `docs/schema-gen-plan.md`。

---

## 0. 文档定位与读法

### 0.1 为什么要先写这份规格

项目下一步是「**工作流 B 的自动化 / schema 生成流程**」——从国标、XSD、样张或用户描述
机械地产出一个可以直接被转换流程消费的 schema 包。生成器必须先有一个**稳定的输出契约**，
否则生成出来的包会依赖生成器自己的临时假设，换个输入源就要重写消费侧。

因此本文回答三个问题：

1. 一个 schema 包**由哪些文件、哪些字段组成**，每个字段的取值域、默认值、权威来源是什么；
2. 命中 schema 后，**版式怎么显示**、五种交付格式分别怎么分派、哪些东西绝对不能做；
3. 转换时**哪些参数会作用于 schema**、哪些不会、缓存与三态如何交互。

第 6 章额外提前定义生成器的输出契约，使「先做规格、再做生成器」成为可验收的两步。

### 0.2 规范性用语

| 用语 | 含义 |
|---|---|
| **MUST** / 必须 | 硬约束。违反则包非法，或产物不可信。lint / 测试应拦住。 |
| **MUST NOT** / 禁止 | 硬禁止。通常对应已踩过的坑或安全边界。 |
| **SHOULD** / 应当 | 强烈建议；缺失只告警，不阻断（但生成器仍应补齐）。 |
| **MAY** / 可以 | 可选。 |
| **【实现】** | 当前代码已落地，附录 C 给出源码符号，可机器对账。 |
| **【现状差异】** | 实现与本规范有差距，集中登记在 §7，**不假装已做**。 |
| **【规划】** | 尚未实现，是「schema 生成流程」的目标，不作为现状承诺。 |

### 0.3 适用范围与边界

- 适用：内置包 `ocr_app/schemas/<id>/`、用户包 `~/.pic2edit/schemas/<id>/`、
  `$PIC2EDIT_SCHEMA_DIR` 下的一切包、以及未来生成器产出的包。
- 不适用：GUI 的「输出格式 / 提示词模板」配置本身（`config.PROMPT_TEMPLATES`），
  它属于**识别策略**，不在 schema 包内定义；两者的交互在 §5.1。
- **本规格不扩大能力边界**：声明式 schema 仍不具备跨页合并、多遍扫描、几何推理（§3.12）。

---

## 1. 术语与角色

| 术语 | 定义 |
|---|---|
| **工作流 A（消费 / 转换）** | 图片 / PDF / TIFF / 目录 → GUI 或 Skill 转换导出。零 schema 也必须能跑。 |
| **工作流 B（生产 / 制作）** | 设计 schema + 版式，产出**数据包**（不是代码）。独立 CLI 入口，与 A 无调用关系。 |
| **schema 包** | 一个目录，至少 `manifest.json` + `schema.yaml`（或 `entry` 指向的插件）。 |
| **provider** | 注册表把包装载成的运行时对象，实现 `SchemaProvider` 协议。 |
| **page** | 一页的纯数据字典，字段见 §3.9。GUI 的 `InputItem` 与 skill 的 `data["pages"][i]` 都投影成它。 |
| **doc** | `extract()` 的返回字典，即命中后写入 `page["schema"]` 的结构化字段。 |
| **三态** | `none` / `auto` / `<id>`，A 侧唯一的选择点。 |
| **忠实版式** | 无 schema 时「原图 base64 垫底 + 按 OCR 坐标绝对定位的透明文字层」，由 `utils/faithful_html.py` 提供。 |
| **IR** | 生成器内部中间表示（§6.2），不是对外产物，**不得**写进 schema 包。 |

**两条不可动摇的架构约束**（用户定义的六条硬约束之 ②④）：

- A 侧 **MUST NOT** 写回 schema 包；schema 包对 A 侧只读。【实现】
- A 侧 **MUST NOT** 内含任何具体票种的字段知识；新增一种凭证 = 加一个目录，不改一行转换代码。【实现】
  守卫：`test_schemas.py::test_skill_has_no_invoice_field_knowledge` 静态扫描整个 skill scripts 目录。

---

## 2. 调用情形矩阵（本文的锚）

「根据可能被调用的情形定义内容」——下表是全部情形。后续每一章的字段、规则、参数都
必须能回答「它被哪个情形读、读不到会怎样」。

| # | 情形 | 入口 / 触发者 | 读取 | 写入 | 失败时的降级 | 规格章节 |
|---|---|---|---|---|---|---|
| **C1** | 发现与装载 | 进程首次 `get_registry()`；`reset_registry()` | 三级根目录的 `manifest.json` → `entry` 或 `schema.yaml` | 内存注册表 + `errors[]` | 单个坏包**告警跳过**，其余照常 | §3.2 §5.5 |
| **C2** | 列表查询 | `--list-schemas`、`schemas.cli list`、GUI 下拉 | `info_of(provider)` | stdout / 下拉项 | 列表加载失败 → 下拉只剩「自动判定 / 不使用」 | §3.3 §5.2 |
| **C3** | 逐页自动判定 | GUI worker、`--schema auto` | `page.raw_text / lines / img_w / img_h` → `detect` → `extract` | `page` 三键 / `InputItem` 三属性 | 未命中 → 三键写 `None`（**不是不写**） | §3.5 §3.9 §5.6 |
| **C4** | 强制指定 | `--schema <id>`、GUI 下拉具体项 | 同 C3，但候选只有该 provider | 同 C3 | 未知 id：A 侧 Skill **提前报错退出 2**；GUI 降级为 `none` 并告警 | §5.2 §5.7 |
| **C5** | 显式不启用 | `--schema none`、GUI「不使用」 | — | **一个键都不写** | 走通用渲染（这是「没有 schema 的世界」的回归基线） | §5.2 |
| **C6** | 五种格式渲染 | `utils/export_formats.py`、skill `_render()` | `page.schema*` → `render_md/txt/html` | 交付文件 | 渲染抛错 → `""` → 调用方走通用兜底 | §4.4 |
| **C7** | 缓存命中 | `--cache` 二次运行 | 缓存 JSON（**不含** `schema` / `source` / `image`） | 现算 schema；html/json 按需补坐标并回写缓存 | 补算失败 → 本页退化为文本重排 + `[warn]` | §5.4 |
| **C8** | 制作与自检 | `schemas.cli new/validate/test/pack` | 包目录全部文件 | 新包 / `*.zip` / sha256 | lint 有 error → **拒绝 pack**（除非 `--force`） | §3.13 §5.9 |
| **C9** | 插件逃生口 | 内置 `invoice`（`entry: __init__.py`） | `get_provider(pkg, manifest)` | 自定义 doc | detect 恒真，判定在 `extract` 内完成 | §3.12 |
| **C10** | 覆盖安装 | 把内置包拷到用户目录改版本号 | `origin` 字段 | 覆盖注册表同名 id | `origin` 必须显示 `user`（**不是** `kind`） | §3.3 §5.5 |
| **C11** | 生成器 | `python -m schema_gen`（**独立于 A 侧**） | XSD / XML / 国标 PDF / 样张 | 完整 schema 包 + provenance | 无版式信息 → **不生成 layout.html**；定位策略标 `pending` | §6 |
| **C12** | 分发与校验 | `pack` 产出 zip；接收方解压 | zip 顶层目录名 = id | 用户目录 | 半份包（缺文件）必须在 C1 被跳过并告警 | §3.1 §5.9 |

**由矩阵推出的三条硬规则**：

- C3 与 C5 的差别必须**可观测**：`none` 不写键，启用但未命中写 `None`。
  下游据此区分「没启用」与「启用了但没命中」。MUST NOT 合并成同一种空值。【实现】
- C7 的缓存 **MUST NOT** 存 `schema`（派生数据）；`--schema` 因此**不进缓存键**，
  命中时按本次运行的三态现算。【实现】
- C10 的存在意味着 `kind` 不能被当作「来源」使用；列表 MUST 显示 `origin`。【实现】

---

# 第一部分 · 数据格式定义

## 3.1 包目录与文件清单

```
<id>/
├── manifest.json     MUST  元信息（纯数据，元信息的权威来源）
├── schema.yaml       MUST  字段与定位策略（声明式）；插件式可省略
├── layout.html       MAY   HTML 版式片段（仅声明式；见 §3.3 layout 语义）
├── provider.py       MAY   Python 插件（由 manifest.entry 指向；与 schema.yaml 二选一）
├── samples/          SHOULD 样张：*.txt（+ 同名 *.lines.json）
└── README.md         MAY   包内说明（不参与装载）
```

规则：

- 目录名 **SHOULD** 等于 `manifest.id`；`pack` 打出的 zip 顶层目录 **MUST** 等于 id。【实现】
- 以 `_` 或 `.` 开头的子目录被扫描跳过；无 `manifest.json` 的目录不是 schema 包。【实现】
- 包内 **MUST NOT** 出现 `*.pyc` / `__pycache__` / `.git`（`pack` 会剔除）。【实现】
- `entry` 与 `schema.yaml` **二选一**；两者都没有 → 装载失败并告警跳过。【实现】
- 编码统一 UTF-8；YAML 由 `yaml.safe_load` 解析（**MUST NOT** 用 `yaml.load`）。【实现】

## 3.2 权威来源规则（最容易漂移的一处）

| 信息 | 权威来源 | 说明 |
|---|---|---|
| `id` / `title` / `version` / `priority` / `kind` / `layout` | **`manifest.json`** | 注册表在 `_build()` 里用 manifest **覆盖** provider 的默认值。 |
| `detect` 规则、字段集、取值策略、分段顺序 | **`schema.yaml`** | manifest 里同名的 `detect` / `fields` 键**不被消费**，只是文档用途。 |
| `origin` / `package_dir` | **注册表扫描根目录** | 运行时写入，包里 MUST NOT 声明。 |
| `strict` / `sections` | **`schema.yaml`** | 无 manifest 对应项。 |

- `schema.yaml` 的 `id` **MUST** 与 `manifest.id` 一致（lint `error`）。【实现】
- `schema.yaml` 里写 `title` / `version` / `priority` 只影响**未被注册表装载时**的默认值
  （如 `lint.check_samples` 直接 `build_provider`），在真实转换里会被 manifest 覆盖。
  生成器 **SHOULD** 让两处一致，避免单测与实跑看到不同值。【实现，属已知语义】

## 3.3 `manifest.json` 字段字典

| 键 | 类型 | 必需 | 默认 | 取值 / 约束 | 被谁读 | 缺失或错误的后果 |
|---|---|---|---|---|---|---|
| `id` | str | **MUST** | — | 见 §3.14 命名规则；同时是目录名、注册表 key、`--schema <id>` 的值 | `registry._read_manifest` | 缺 → 装载失败（跳过该包）；lint `error` |
| `title` | str | SHOULD | 回退 `id` | GUI 下拉显示文案 | `registry._build` | 缺 → lint `warning`，下拉显示 id |
| `version` | str | SHOULD | `"1.0.0"` | 见 §3.14；产物记录它 | `registry._build` → `page.schema_version` | 缺 → lint `warning`，溯源失准 |
| `kind` | str | MAY | `origin` | 自报类型（`builtin` / `declarative` / `plugin` / `user`） | `info_of` | **MUST NOT** 当作来源判据；见 C10 |
| `priority` | int | MAY | `50` | 小者先试；同值按 id 稳定排序 | `registry.providers` | 缺省 50；负数合法但无必要 |
| `entry` | str | 条件 MUST | — | 指向包内 `.py`，该文件 MUST 暴露 `get_provider(pkg_dir, manifest)` | `registry._build` | 文件不存在 → 装载失败；缺 `get_provider` → `AttributeError` |
| `schema` | str | MAY | `"schema.yaml"` | 声明式 yaml 文件名 | `registry._build` / `runtime.build_provider` | 指向不存在 → `FileNotFoundError` |
| `layout` | str | MAY | 若存在 `layout.html` 则自动 | **仅声明式** HTML 版式文件名 | `registry._build` / `runtime.DeclarativeProvider` | 插件式写了 → lint `warning`；指向不存在 → `error` |
| `description` | str | MAY | — | 纯说明 | 无（文档用途） | 无影响 |
| `detect` / `fields` | any | MAY | — | **当前实现不消费**（内置 `invoice/manifest.json` 里有，仅作人类速查） | 无 | 生成器 MUST NOT 依赖它们生效 |
| `x-*` | any | MAY | — | 扩展键；生成器 provenance 放这里（§6.5） | 无（保留） | MUST NOT 影响装载 |

**`entry` 与 `layout` 互斥语义**【实现】：`layout` 指的是**声明式 HTML 模板文件**；
插件式的 HTML 由 `render_html()` 现算。两者同时声明 → lint `warning`，且仍检查文件存在。
历史上发票曾写 `"layout": "html_layout.py"`，导致把 `.py` 当模板读，已删除。

## 3.4 `schema.yaml` 顶层字段字典

| 键 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | str | **MUST** | — | 与 manifest 一致；`load_spec()` 强制非空 |
| `title` | str | MAY | manifest.title | 见 §3.2 |
| `version` | str | MAY | manifest.version | 见 §3.2 |
| `priority` | int | MAY | 50 | 见 §3.2 |
| `strict` | bool | MAY | **`true`** | `true`：任一 `required` 未取到值 → **整页未命中**（`extract()` 返回 `None`）。推荐保守值。 |
| `detect` | map | SHOULD | `{}` | 四条规则，见 §3.5 |
| `sections` | list | **MUST** | — | 至少一段；顺序即导出顺序 |

`load_spec()` 的三条最小结构校验：顶层必须是映射、`id` 非空、`sections` 是非空 list。【实现】

## 3.5 `detect` 规则（只回答「像不像我」）

| 键 | 类型 | 语义 |
|---|---|---|
| `all` | list[str] | 每个词都 MUST 出现在 `raw_text` 中（二次确认，防止正文里出现标题词就误判） |
| `none` | list[str] | 任一出现即排除 |
| `regex` | list[str] | 每个正则都 MUST `re.search` 命中 |
| `any` | list[str] | 至少一个词出现 |

**求值顺序（短路）**【实现】：`all` → `none` → `regex` → `any`；任一失败立即 `False`。
四条规则全为空时，退化为 `bool(raw_text.strip())`——即「有正文就算候选」，把判断交给
`extract()` 的 `required` 校验。lint 对此给 `warning`（auto 模式下会匹配每一页）。

- `detect` **MAY** 恒真（插件式发票即如此），此时 `priority` **MUST** 取大值让它最后兜底。【实现】
- `detect` 的输入是**表格化之前**的 `raw_text`：GUI worker 明确用原文，因为 KV→表后字段语义
  被压平反而更难抽。【实现】
- `detect` **MUST NOT** 依赖 `lines` 坐标做复杂几何推理；声明式运行时只看文本。
  需要几何证据的票种 MUST 走插件式（§3.12）。

## 3.6 `sections[]` 字段字典

### 3.6.1 所有分段共有

| 键 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `key` | str | **MUST** | — | 段内唯一；**MUST NOT** 与任何字段 key 同名（版式取值会歧义）；`data-repeat` 用它引用 |
| `label` | str | SHOULD | `key` | 导出小标题（`### label` / `[label]` / `<h3>`） |
| `mode` | enum | MAY | `kv` | 白名单 **`kv` / `table` / `text`**；未知值 lint `error` |
| `required` | bool | MAY | `false` | 段级必填：`table` 无行 / `text` 无值 → 计入 `missing`；`strict=true` 时整页未命中 |

### 3.6.2 `mode: kv` 的 `fields[]`

| 键 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `key` | str | **MUST** | — | **全局唯一**（跨段也唯一）；同时是 `flat` 的键与版式占位名 |
| `label` | str | SHOULD | `key` | 票面字面词；行内标签取值靠它 |
| `regex` | str | MAY | — | 显式正则；`group(1)` 优先，无分组则 `group(0)`；`re.M` 多行 |
| `anchor` | enum | MAY | **`right`** | 白名单 **`right` / `below` / `inline` / `none`**；`inline` 与 `none` 等价（不做坐标取值） |
| `required` | bool | MAY | `false` | 缺失计入 `missing`；`strict=true` 时整页未命中 |

lint：`key` 重复 / 与段名同名 / 未知 anchor / 正则编译失败 → `error`；
既无 `label` 也无 `regex` → `warning`（永远抽不到）。【实现】

### 3.6.3 `mode: table` 的字段

| 键 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `columns` | list | **MUST** | — | 每项 `{key, label}`；`key` MUST 唯一；**顺序即票面左→右顺序** |
| `start` | str | **MUST**（运行语义） | `""` | 表格起始标题词；声明了却找不到 → 本段返回空（见下方硬要求） |
| `stop` | str | SHOULD | `""` | 结束标题词；命中即停止收行 |
| `limit` | int | MAY | — | 最多收多少行 |
| `required` | bool | MAY | `false` | 无行时计入 `missing` |

**表格分段三条硬要求**【实现】：

1. `start` **MUST** 写。不写会让票据上半部分的「键：值」行被当成两列数据行，
   产出一张混入表头的假表——比抽不到更危险。声明了但找不到 → 返回空段，
   **MUST NOT** 退化成「整页都当表格」。
2. 列顺序即票面顺序，运行时按左→右填入。
3. 单元格多于声明列数时，多出的**并入最后一列**（不静默丢弃）；少于时缺列留空。
   > 这条在 `runtime._table_rows()` 里曾因 `elif` 条件不可达而没做到；
   > 本次修规格时补上实现 + `test_extract_table_keeps_surplus_cells_in_last_column`。

切列算法：有 `lines` 坐标时按 y 聚视觉行（带宽 `max(6, 0.6×中位框高)`）、行内按 x 排序；
无坐标时按 `\t` 或连续 2+ 空白切列，且一行至少 2 格才收。整行几乎都是列名时跳过（表头行）。
`stop` 词出现在行内即 `break`。【实现】

### 3.6.4 `mode: text` 的字段

| 键 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `regex` | str | SHOULD | — | 整块文字的取值正则 |
| `field_label` | str | MAY | `""` | 行内标签取值用的票面词（**不是** `label`，`label` 是导出标题） |
| `anchor` | enum | MAY | `none` | 同上白名单 |
| `required` | bool | MAY | `false` | 无值时计入 `missing` |

`text` 段的值同时写进 `flat[section.key]`，所以版式里可以直接写 `{{ remarks }}`。【实现】

## 3.7 字段取值三级策略（精确算法）

按顺序尝试，**先到先得**；`label` 是票面字面词，`regex` 是形状约束，`anchor` 是几何位置：

| 级 | 触发 | 算法 |
|---|---|---|
| 1 | 写了 `regex` | `re.search(pat, text, re.M)`；取 `group(1)`（无分组取 `group(0)`）；`.strip()`；值等于 `label` 本身则视为没取到，继续下一级 |
| 2 | 有 `label` | **行内标签**：`re.finditer(re.escape(label), text)` → 去掉标签后紧跟的 `^[ \t\u3000:：]+`（**不含换行**，否则会把下一字段的标签吃成自己的值）→ 取到行尾 → 按 `\s{2,}` 截断 → 长度 ≤ 120 才收 |
| 3 | `anchor` ∉ {`none`,`inline`} | **坐标锚点**：`right` 取同一视觉行右侧最近的框（`x0 ≥ 标签框右−2` 且 `\|Δcy\| ≤ 0.6×标签框高`）；`below` 取下方最近的框（`y0 ≥ 标签框下−1`、`x0 < 标签框右`、`cy` 更大） |

**MUST** 注意：三级不是「三个都跑取最长」，而是**短路**。测试
`test_inline_label_wins_over_anchor` 钉住「同行有值就不再去坐标里找」——坐标很可能取到
隔壁字段的值。生成器 **SHOULD** 同时给字段写 `label` 与（能写时）`regex`。

`extract()` 的收尾【实现】：`flat` 全空且所有表都空 → 返回 `None`（未命中）；
`strict=true` 且 `missing` 非空 → 返回 `None`。宁可不结构化，也不输出半空却看似可信的表。

## 3.8 `samples/` 数据格式

| 文件 | 格式 | 说明 |
|---|---|---|
| `<name>.txt` | UTF-8 纯文本 | 该票种的**识别文本**（不是图片）；`check_samples` 直接喂 `extract()` |
| `<name>.lines.json` | JSON 数组 | 可选；每行框 `{"text","x0","y0","x1","y1"}`，用于测坐标锚点与表格聚行 |

`check_samples()` 返回 `{samples, with_samples, hit_rate, results[]}`，每项
`{sample, hit, fields, tables}`；`schemas.cli test --min-rate`（默认 **0.8**）据此判通过。
**SHOULD** ≥3 份样张，覆盖正常 / 缺字段 / 不同版式。生成器 **MUST** 至少产出一份可命中样张（§6.6）。

## 3.9 运行时数据契约：`page` 与 `doc`

### 3.9.1 `page`（A 侧与 B 侧的唯一交换结构）

```python
{
  "page": int,                 # 页序
  "text": str, "raw_text": str,# 识别文本；schema 取 raw_text
  "lines": [{"text","x0","y0","x1","y1"}, ...],  # 可为空
  "img_w": int, "img_h": int,  # 无坐标时为 0
  "engine": str,               # 溯源（可选）
  "image": str,                # 仅渲染 html 时临时附加；MUST NOT 进缓存
  "schema": dict | None,       # 命中后的 doc
  "schema_id": str | None,
  "schema_version": str | None,
}
```

`registry.apply()` 写入三键的规则【实现】：

| 三态 | `schema` 键 | `schema_id` | `schema_version` |
|---|---|---|---|
| `none` | **不写** | **不写** | **不写** |
| `auto` / `<id>` 命中 | `doc` | `id` | `version` |
| `auto` / `<id>` 未命中 | `None` | `None` | `None` |

GUI 的 `InputItem` 有同名三属性；`export_formats._page_dict()` 把两者投影回同一形状。
**MUST NOT** 在 A 侧另造一份字段结构——这是「同一件事只准有一个实现入口」的具体化。

`apply()` 返回的 statistics：`{mode, hits, pages, applied:{id:n}, versions:{id:ver}, errors:[]}`。

### 3.9.2 声明式 `doc`（`DeclarativeProvider.extract()`）

```python
{
  "type": "<id>",
  "version": "<version>",
  "sections": [                # 顺序 = schema.yaml 的 sections 顺序
    {"key","label","mode":"kv","fields":[{"key","label","value"}]},
    {"key","label","mode":"table","rows":[{col_key: value}]},
    {"key","label","mode":"text","value": str},
  ],
  "flat":   { field_key: value },        # 版式占位的主查找表
  "tables": { section_key: [row_dict] }, # data-repeat 的数据源
  "missing":[ label, ... ],              # 未取到的 required
}
```

### 3.9.3 插件式 `doc`（自由结构）

插件 `extract()` **MAY** 返回任意 dict，由它自己的 `render_md/txt/html` 解释。
内置发票的形状是 `header / buyer / seller / items[] / totals / total_incl_tax /
signatures / meta`（见 `invoice/render.py`），与声明式 doc **不兼容**。

> **MUST**：两类 doc 不得混用渲染器。生成器 **MUST** 只产声明式包；
> 插件作者 **MUST** 自己提供三套 `render_*`，不能复用 `runtime.render_layout()`。

## 3.10 产物中的 schema 元数据（可追溯性 / 硬约束⑤）

| 交付格式 | 落点 | 内容 |
|---|---|---|
| `json` | 顶层 `"schema"` + 每页三字段 | **两侧形状不同，见下方**；页内 `schema_id` / `schema_version` / `schema`(doc) 两侧一致 |
| `md` / `docx` | 文首注释 | `<!-- schema: id@version -->`（多个用 `、` 分隔、按 id 排序） |
| `html` | `<head>` | `<meta name="pic2edit-schema" content="id@version">`，**MUST** HTML 转义 |
| `txt` | **不写** | 纯文本里的 HTML 注释是可见噪声 |

**`json` 顶层 `"schema"` 的现状（真跑核对，2026-11-06）**：

| 入口 | 值 | 源码 |
|---|---|---|
| GUI `export_json` | `{ "<id>": "<version>", ... }`（纯版本映射） | `utils/export_formats.py::export_json` |
| Skill `_render` | `apply_schemas()` 的 statistics：`{mode, hits, pages, applied, versions, errors}`；版本在 **`versions`** 子键里 | `pic2edit_convert.py::_apply_schema_stats` |

两者都能回答「按哪一版抽的」，但**形状不统一**（差距 D8）。调用方 **SHOULD** 以
每页 `schema_version` 为唯一可靠来源；生成器自检 **MUST** 用 `schema.versions[id]`
（skill）或 `schema[id]`（GUI），不要假设两者相同。MUST NOT 在规格里把它们写成一个。

其它硬规则：

- `json.source` **MUST** 只写 `os.path.basename(source)`，MUST NOT 写绝对路径
  （会连用户名与客户目录结构一起外泄）。【实现：GUI `export_json` 与 skill 两处均已改】
- 缓存 **MUST NOT** 存 `source` / `image` / `schema*`——前两者是运行期数据，第三者派生。
- 产物 **MUST** 能回答「按哪一版规则抽的」；`version` 变更时产物 stamp 必须跟着变。

## 3.11 命名与版本规范

| 项 | 规则 |
|---|---|
| `id` | **MUST** 匹配 `[a-z][a-z0-9_\-]{0,31}`（1–32 字符，小写字母开头，其余为小写字母/数字/`_`/`-`）。同时是目录名与 `--schema` 的值。`schemas.cli new` 用 `_SAFE_ID_RE.fullmatch` + `os.path.commonpath` 根目录守卫。 |
| `version` | **SHOULD** 用 `MAJOR.MINOR.PATCH`；字段集或取值规则任何变更 **MUST** 升版（产物 stamp 依赖它）。 |
| `priority` | 建议：`10–30` 具体单据（有强 detect）、`50` 默认、`90–100` 恒真兜底。内置 `asset_card=20`、`invoice=100`。 |
| 字段 `key` | 小写下划线，全局唯一；`x-*` 前缀保留给非标准扩展字段。 |

## 3.12 插件式协议（`SchemaProvider`）

```
detect(text, lines, width, height) -> bool
extract(text, lines, width, height) -> dict | None
render_md(doc) -> str
render_txt(doc) -> str
render_html(doc) -> str          # 片段，不含 <html>/<style>
css() -> str
```

属性：`id / title / version / kind / origin / priority / layout`。
包 MUST 暴露 `get_provider(pkg_dir, manifest)`。注册表会覆盖除 `origin` 外的元信息属性。
lint `_lint_plugin` 检查六个方法可调用 + `entry` 文件存在。【实现】

**适用边界**：仅当版式多变、需要多遍扫描 / 几何推理 / 大写金额校验等能力时才用。
声明式 **MUST NOT** 被扩展成图灵完备 DSL（不加 `when` / `transform` / 表达式求值）；
复杂需求一律退到这个明确的逃生口。

## 3.13 lint 规则全表（生产侧门禁）

| 级别 | 检查 | 影响 |
|---|---|---|
| `error` | manifest 缺失/解析失败/缺 `id`；yaml 解析失败；`id` 两处不一致；分段缺 `key` / key 重复 / 未知 `mode`；表格无 `columns` / 列 key 重复；字段 key 重复 / 与段名同名 / 未知 `anchor` / 正则编译失败 | `validate` 退出 1；`pack` **拒绝打包** |
| `error` | 版式占位在 schema 里不存在；`data-repeat` 指向的段不存在或不是 `table`；版式文件不存在 | 同上 |
| `error` | 插件式：`entry` 文件不存在、注册表加载不到、六个方法缺任一 | 同上 |
| `warning` | 缺 `title` / `version`；`detect` 无任何规则；分段缺 `label`；`text` 段无 `regex`；字段既无 `label` 也无 `regex` | 不阻断 |
| `warning` | 字段在版式里没有对应占位（只取数不用版式是合法用法） | 不阻断 |
| `warning` | `entry` 与 `layout` 同时声明 | 不阻断 |
| `warning` | `table` 段缺 `start`（运营期会混入表头假表；规范 MUST，lint 只告警以兼容存量包）【D1·v1.1 补】 | 不阻断 |
| `warning` | `schema.yaml` 与 `manifest.json` 的 `title` / `version` / `priority` 不一致【D2·v1.1 补】 | 不阻断 |
| `info` | `text` 段无 `regex`（提示） | 不阻断 |

`pack` 流程：先 `lint_package` → 不通过且无 `--force` 则拒绝 → 打包（剔除 `.pyc`/`__pycache__`/`.git`）→
打印文件数与 **sha256** → 打印安装路径。【实现】

> 双向对账存在的原因：项目曾把 `docs/invoice_schema.md` 与抽取代码各写一份字段表，
> 靠人脑同步，文档长期落后。两个文件 + 机器 lint 是唯一可维护的方案。

## 3.14 【现状差异】与本节相关的已知缺口

| # | 差异 | 规范 | 现状 |
|---|---|---|---|
| D1 | `table` 段缺 `start` | 规范 MUST；lint 应告警 | **v1.1 已补**：`lint_package()` 对缺 `start` 的 table 段报 `warning`（不阻断，见 §3.13） |
| D2 | `schema.yaml` 与 manifest 的 `version` 不一致 | SHOULD 一致 | **v1.1 已补**：lint 对 `title` / `version` / `priority` 三处不一致报 `warning`；generator 输出时保证一致 |
| D3 | `origin` 可选值 | `builtin` / `user` | `PIC2EDIT_SCHEMA_DIR` 与 `reset_registry(extra_dirs)` 也标 `user`（合理，但生成器需知道） |

---

# 第二部分 · 版式显示要求

## 4.1 三档渲染降级（GUI 与 Skill 必须一致）

HTML 导出按保真度从高到低，**命中即停**：

| 档 | 条件 | 产物 |
|---|---|---|
| 1 | 命中 schema（`page.schema` 非空且 `render_schema(...,"html")` 非空） | 该 schema 的版式片段（发票票面 / 固定资产卡片）；**不叠加**坐标表、不带原始全文 |
| 2 | 无 schema，但有**原图 + 行框 + 原图尺寸** | 忠实版式：原图 base64 垫底 + 按 OCR 坐标绝对定位的透明文字层（`page_with_image`），文字可选中复制 |
| 3 | 都没有 | Markdown 转 HTML 重排（**最后手段**，版面会被推断错） |

- 三档产物 **MUST** 全部过 `sanitize_html()`（§4.5）。【实现】
- GUI（第 2 档曾缺失，已补）与 Skill **MUST** 对同一文件产出同档结果。【实现】
- 缓存命中时 **MUST** 按需补算 `lines` 再判档，MUST NOT 因为缓存里没有坐标就掉到第 3 档。【实现】
- **MUST NOT** 把「schema 版式」与「坐标碎片表」并列输出（早期这么做过，页面不可读）。

## 4.2 `layout.html` 语法规格

只有两种语法，**MUST NOT** 引入模板引擎（jinja2 等）——多一个打包依赖、多一套注入面，
而需要的能力只有「填值」与「重复行」：

| 语法 | 正则 / 作用 |
|---|---|
| `{{ key }}` | `\{\{\s*!?\s*([A-Za-z_][\w.]*)\s*\}\}`；取标量并 `html.escape` |
| `{{! key }}` | 同上但不转义；**MUST NOT** 用于任何来自 OCR 的值（见 §4.3） |
| `<tr data-repeat="section_key">…</tr>` | `<(\w+)([^>]*\bdata-repeat="([\w.]+)"[^>]*)>(.*?)</\1>`（`re.S\|re.I`）；元素按 `doc.tables[key]` 逐行重复，重复时去掉 `data-repeat` 属性；无行则输出空 |
| `{{ section.field }}` | 带前缀写法；先查当前 scope，再查该 kv 段的字段表，再查 `doc[section][field]` |

其它规则【实现】：

- 渲染前 **MUST** 删除 HTML 注释（`<!--…-->`）：注释里常写 `data-repeat` 用法示例，
  留着会被当成真区块渲染出错位的行。版式注释因此可放心写示例。
- `<style>…</style>` 内容由 `_split_style()` 抽出，交给 `css()` 拼进导出 HTML 的 `<style>`；
  版式文件里 **MUST NOT** 写 `<html>/<head>/<body>/<!DOCTYPE>`，只写「这一段长什么样」。
- 版式渲染抛异常 → **MUST** 退回 `_generic_html(doc)`（通用 `doc-kv` 表），
  不让整次导出失败，并记一条 warning。【实现】
- 无 `layout.html` 时 `render_html()` 直接走 `_generic_html()`，`css()` 返回 `generic_css()`。

## 4.3 占位查找与转义

`_lookup(doc, scope, section, path)` 的顺序【实现】：

1. `path` 在 scope（`flat` 或当前重复行的 row）里 → 直接取；
2. 带 `.` 前缀且前缀是段名 → 取该 kv 段的字段值；
3. 带 `.` 前缀且 `doc[head]` 是 dict → 取 `doc[head][rest]`；
4. 都没有 → 空字符串（**MUST NOT** 把 `{{...}}` 原样留在产物里）。

安全要求：

- 标量一律 `html.escape`；`{{! }}` 只在值由包作者**硬编码**、绝不含 OCR 文本时允许。
- 重复区块内的占位作用域 = 该 kv 段字段表 + 当前行覆盖（行优先）。
- 所有字段值最终还要过 `sanitize_html()`（GUI 导出 / GUI 预览 / skill html）。
- `sanitize_html` 的 `img` 只允许 `data:image/`，因此版式里 **MUST NOT** 引用远程图片
  （会被清洗掉，而且远程图片在 GUI 预览里真的会发起请求）。

## 4.4 五种交付格式的分派表

| `--format` | 命中 schema | 未命中 schema |
|---|---|---|
| `md` | `render_md(doc)`：`### 段名` + `- **字段**：值` + Markdown 表；缺值 `_（未识别到）_` | `build_table_from_lines`（≥50% 视觉行含 ≥2 格才出表）或识别原文 |
| `txt` | `render_txt(doc)`：`[段名]` + `标签\t值`；表格用 TAB 分列 | **不做**坐标表还原（刻意），直接识别原文；纯文本里不写 schema 注释 |
| `docx` | 同 `md` → `md_to_docx` | 同 `md` → `md_to_docx` |
| `html` | `render_html(doc)` → `sanitize_html` | 三档降级（§4.1） |
| `json` | 原样输出 `pages[]`（含 `raw_text` / `lines` / `schema*`），供入库与二次加工 | 同左，但 `schema*` 为 `null` |

**MUST** 注意：`--mode`（识别策略）**不决定**交付分派，分派只看「本页有没有命中 schema」。
早期按 mode 分派，把发票当通用表格输出成一坨坐标碎片。三个轴的正交关系见 §5.1。

## 4.5 HTML 清洗白名单（不可信输入边界）

`utils/html_safety.py::sanitize_html`【实现】：

- 允许标签：`a b blockquote br caption code col colgroup dd del div dl dt em figcaption
  figure h1–h6 hr i img ins li ol p pre q s small span strong sub sup table tbody td
  tfoot th thead tr u ul`；
- 允许属性：`align alt class colspan height href id rowspan src style title valign width`；
- **MUST NOT** 放行 `script / iframe / object / embed / form / link / meta / base / svg`；
- `href` 只允许 `https?:` / `mailto:`；`src` **只允许** `data:image/`；
- `style` 含 `url(` / `expression(` / `@import` → 丢弃该属性；
- 未知标签**保留文字、丢标签**（清洗不该改变用户看到的文字）。

原因：`markdown.markdown()` 默认 HTML 直通，且 `html_table` 模式主动让模型输出 HTML——
提示词注入有一条稳定产出通道；GUI 的 `QTextBrowser` 虽不执行 JS 但会加载 `<img src>`。
GUI 预览 **MUST** 设 `setBaseUrl(QUrl("about:blank"))` 并清洗后再 `setHtml()`。【实现】

## 4.6 CSS、meta 与渲染日志

- 导出 HTML 的 `<style>` 拼接顺序：`_HTML_CSS`（通用）→ `schema_css(pages)`（**只取本次真正命中的
  schema**）→ `faithful_css()`。【实现】
  MUST NOT 永远拼发票 CSS——没命中时那是一套用不上的票面样式。
- `<meta name="pic2edit-schema">` 的 content **MUST** 转义（GUI 与 Skill 两侧）。【实现，本次补齐 skill 侧】
- **渲染日志 MUST 说真话**：逐档如实汇报（如「原图垫底×1 / 纯文本×1」），
  MUST NOT 按「命中几页 schema」猜一句「严格按 schema 输出 / 按原图版面还原」了事。
  日志与产物不符比没有日志更坏。【实现】

## 4.7 版式 ↔ schema 双向一致性（lint）

见 §3.13：占位在 schema 里不存在是 `error`（会永远渲染成空，等于打错字）；
字段没在版式里出现是 `warning`（只取数不用版式合法）；`data-repeat` 必须指向 `table` 段。
生成器 **MUST** 在产出包后跑这条 lint（§6.6）。

## 4.8 合规边界（MUST NOT 越界）

- HTML 版式是**人读预览**，**不是合规版式文件**：无数字签名、监制章套印、防伪。
  电子会计档案归档推荐 OFD（DA/T 94-2022、GB/T 33190-2016）。
- 财政电子票据若带**内嵌 XML**（财会〔2025〕9 号），最准的抽取是「不抽取、直接读」；
  本规格 **MUST NOT** 被理解成「这类文件也该 OCR 后套 schema」。
- 国标数据模型（如 T/CICPA 0110—2026）覆盖不了票面全集（印章 / 二维码 / 手写 / 备注），
  **MUST** 允许 `x-*` 扩展与 extra 兜底区（§6.7）。

---

# 第三部分 · 转换适用参数

## 5.1 三个正交的轴（最容易混淆）

| 轴 | 取值 | 决定什么 | 在哪定义 |
|---|---|---|---|
| **交付格式** `--format` | `md` / `txt` / `docx` / `json` / `html` | 输出文件的容器与分派（§4.4） | A 侧 CLI / GUI 导出对话框 |
| **识别策略** `--mode` | `markdown` / `ocr` / `figure` / `table` / `html_table` | 发给 VLM 的提示词、路由策略、KV→表后处理 | `config.PROMPT_TEMPLATES`（**不在 schema 包内**） |
| **凭证 schema** `--schema` | `none` / `auto` / `<id>` | 命中后按哪套字段规则抽、用什么版式渲染 | schema 包（本文第一、二部分） |

- `--mode` 与 `--format` 是**两个独立参数**，名字相近但互不覆盖。【实现】
- `--mode` 取值 **MUST** 来自门面 `list_modes()`（= `config.PROMPT_LABELS` 的键），
  前端 MUST NOT 各自维护一份白名单（曾漏掉 `html_table`）。【实现】
- schema 命中时交付形态由 schema 决定，**不看 `--mode`**；但 `--mode` 仍影响识别阶段
  （例如 `table` 模式的 KV→表后处理发生在 `raw_text` 之外，schema 取的是原始文本）。【实现】

## 5.2 三态 `none | auto | <id>`（硬约束③：A 侧唯一选择点）

| 值 | 候选集 | 行为 | 未命中 / 出错 |
|---|---|---|---|
| `none` | 空 | 连 `schema` 键都不写 | 一律通用输出 |
| `auto`（默认） | 全部 provider，按 `priority` 升序、同值按 id | 逐页依次 `detect` → `extract`，第一个返回 doc 的胜出 | 三键写 `None`，通用输出 |
| `<id>` | 只有该 provider | 同上 | Skill：未知 id 在**任何昂贵动作之前**报错退出 2；GUI：降级 `none` + 告警 |

- 三态 **MUST** 由 `backend_api.resolve_schema()` 单点解析；GUI 下拉与 `--schema` 同值。【实现】
- `auto` 的调度里，单个 provider 抛异常 **MUST** 只记 warning 并继续下一个，
  MUST NOT 拖垮整篇导出。【实现】
- `none` 的「不写键」由 `test_schemas.py::test_none_mode_touches_nothing` 静态钉住。

## 5.3 A 侧 CLI 参数全表（`pic2edit_convert.py`）

| 参数 | 默认 | 说明 | 是否进缓存键 |
|---|---|---|---|
| `input`（`nargs="*"`） | — | 多个文件 / 目录（递归、稳定排序）；`--list-schemas` 时可不传 | `sha256(输入文件)` |
| `--format` | `md` | 见 §5.1 | 否 |
| `--mode` | `markdown` | 见 §5.1；`choices=list_modes()` | **是**（`mode`） |
| `--url` | `http://localhost:8080` | 引擎地址；**拉起端口 MUST 取自 url**（`port_of(url)`） | 否 |
| `--gpu-layers` | `None` | 0 = CPU | 否 |
| `--out` | `""` | 单输入=文件路径；多输入=**已存在的目录** | 否 |
| `--stop-server` | `False` | 本次拉起的引擎完成后回收 | 否 |
| `--cache` | `False` | 按内容哈希复用识别结果 | — |
| `--prov` | `False` | md/txt/docx 加 `<!-- engine:… -->` | 否 |
| `--vlm-model` | `None`（→ settings 的 `vlm.model`） | `auto/q8/q4/bf16/cpu` | **是**（`vlm:` 档位） |
| `--schema` | `auto` | 三态 | **否**（派生数据，命中现算） |
| `--list-schemas` | `False` | 打印 id / 版本 / **origin** / 优先级 / 标题后退出 | — |
| `--temperature` | `None → 0.0` | 采样温度 | **否**（见下） |
| `--repeat-penalty` | `None → 1.10` | 重复惩罚 | 否 |
| `--repeat-last-n` | `None → 4096` | 重复检测窗口 | 否 |
| `--max-tokens` | `None → 16384` | 单页上限 | 否 |

**采样参数不进缓存键**【现状差异 D4】：四个 flag 只影响 VLM 生成，理论上是识别层参数。
当前约定是「要重跑就换 `--schema` / 换目录 / 删缓存目录」，SKILL.md 已写明。
生成器与调用方 **MUST NOT** 假设「改了 temperature 一定拿到新结果」。若后续把它们并入键，
MUST 同步 §5.4 与 SKILL.md。

## 5.4 缓存键与派生数据

```
sha256(输入文件) | mode | CACHE_VERSION | vlm:<档位> | skip:<0|1> [| mask:<x,y,z,w>]
```

- `CACHE_VERSION = "v3"`；根目录 `<tmp>/pic2edit_cache_<uid>/`，权限 0700（多用户机器上
  MUST NOT 用共享目录，否则他人可预置伪造识别结果）。【实现】
- **识别层参数 MUST 进键**：`mode` / VLM 档位 / 是否跳过判别式 / 掩膜矩形——它们决定
  `raw_text` 与 `lines`，读取时无法重算。
- **派生数据 MUST NOT 进键、也 MUST NOT 写进缓存**：`schema` / `schema_id` /
  `schema_version`（每次读取按当前三态现算）、`source`、`image`（运行期临时路径）。【实现】
- 命中分支 **MUST** 调用与首识别相同的 `_apply_schema_stats()`，
  MUST NOT 另写一份「补 schema」逻辑（两条路径各写一遍正是此前静默失效的根因）。【实现】
- 命中分支若 `pages[].lines` 为空而本次需要版面（html / json / table），
  **MUST** 调 `_ensure_page_lines()` 补算坐标并回写缓存；否则 html 会从第 1/2 档静默退化到
  第 3 档。【实现，真跑复现过】
- `--cache` 两遍输出 **MUST** 逐字节相同；这是可复核的硬验收项。

## 5.5 环境变量与 GUI 设置

| 名称 | 作用 | 与 schema 的关系 |
|---|---|---|
| `PIC2EDIT_SCHEMA_DIR` | `os.pathsep` 分隔的额外扫描根目录，**优先级高于内置、低于 `~/.pic2edit/schemas`** | 直接决定 C1/C10；origin 记为 `user` |
| `~/.pic2edit/schemas/` | 用户级根目录（最后扫描，同名覆盖内置） | 同上 |
| `PIC2EDIT_SKIP_DISCRIMINATIVE` | 跳过 RapidOCR，只走 VLM 兜底 | 进缓存键 `skip:` |
| `PIC2EDIT_MASK_RECT` / `PIC2EDIT_MASK_PASSWORD` | 密码区掩膜 | 进缓存键 `mask:` |
| `PIC2EDIT_ROOT` / `PIC2EDIT_OCR_APP` / `PIC2EDIT_LIVE_URL` / `PIC2EDIT_HW_FORCE` / `PIC2EDIT_CLINIC` / `PIC2EDIT_NO_WELCOME` | 路径 / 服务 / 预筛 / 欢迎页 | **与 schema 无关** |

GUI 设置：`schema_mode`（默认 `auto`）、`prompt_template`、`ModelParams` 四项、
`resolution`（**仅 transformers 后端消费，且是模型加载期 `--mode` 参数**；
llama.cpp / Ollama 不接，状态栏也只在 transformers 后端显示它）。

生成/调试时 **MUST** 用 `PIC2EDIT_SCHEMA_DIR` 或 `reset_registry(extra_dirs)` 隔离，
MUST NOT 直接覆盖内置包做试验。

## 5.6 调度、失败降级与只读约束

| 环节 | 规则 |
|---|---|
| 输入校验 | **MUST** 在缓存分支之后、引擎拉起**之前**做；不可解码 → `InputPrepError` → `[error] 无法解码图片：<文件名>` + 退出码 3（批量则跳过该文件） |
| 未知 schema id | MUST 在任何昂贵动作（解码、拉引擎、OCR）之前报错 |
| `detect` / `extract` 异常 | 只记 warning，继续下一个候选 / 本页当未命中 |
| 渲染异常 | 返回 `""`，调用方走通用兜底，不中断导出 |
| 坏包 | 只告警跳过（`registry.errors[]`），MUST NOT 让整个注册表崩掉 |
| A 侧写回 | **禁止**（硬约束④） |

## 5.7 退出码与输出通道

**A 侧**（skill CLI）：

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 批量里有文件失败（其余仍已产出） |
| 2 | 参数/输入整体不可用：未知 schema、没有可转换文件、多输入却给了文件型 `--out` |
| 3 | 输入文件无法解码 |

**B 侧**（`schemas.cli`）：`0` 全部通过；`1` 有 error / 命中率未达门槛；`2` 用法错误。

stdout **MUST** 只有产物绝对路径（单输入一行、批量每行一个）或查询结果；
诊断信息（`[cache]` / `[schema]` / `[engine]` / `[warn]` / `[render]`）一律 stderr。【实现】

## 5.8 目录批量与 `--out` 语义

- 目录递归收集扩展名：`.png/.jpg/.jpeg/.webp/.bmp/.tif/.tiff/.pdf`，
  目录名与文件名均排序（结果稳定、可 diff）。【实现】
- 多输入（含目录）时 `--out` **MUST** 是已存在的目录，否则退出 2
  （N 个文件争抢同一输出名会静默只剩最后一个）。【实现】
- 不存在的输入路径要逐个报出，不静默忽略。

## 5.9 B 侧 CLI（与 A 侧不同入口，硬约束⑥）

| 子命令 | 作用 | 关键参数 |
|---|---|---|
| `list` | 列已发现 schema（打印 **origin**） | — |
| `validate` | 结构与版式对账 | `target`（id 或目录）、`--samples` |
| `test` | 样张命中率 | `target`、`--min-rate`（默认 0.8） |
| `new` | 生成声明式空壳 | `id`、`--title`、`--dir`（默认 `USER_ROOT`）、`--force` |
| `pack` | 先 lint 再打 zip + sha256 | `target`、`--out`、`--force` |

`new` 的 id 走 `_SAFE_ID_RE.fullmatch` + `commonpath` 根目录守卫；
`pack` 的 zip 顶层目录 MUST 是 id，安装路径 `~/.pic2edit/schemas/`。【实现】

**生成器是第三个入口（v1.1 补）**：`python -m schema_gen`（在 `ocr_app/` 下运行），
与上表同属工作流 B，但**不是** `schemas.cli` 的子命令——它读的是外部标准原件，
产出的是包，不参与包的校验/分发。

| 入口 | 作用 | 关键参数 |
|---|---|---|
| `python -m schema_gen` | 从 S1/S2/S3 标准源生成声明式包骨架 | `--source`、`--out`、`--id`、`--standard`、`--detect-hint`、`--offline` |

生成器 MUST NOT 写入 `schemas/`（那是注册表 `BUILTIN_ROOT`，放进去会被当成包扫描），
MUST NOT 写入 `--out` 根目录之外；产物不自动安装，由调用方按 C10/C12 自行覆盖安装。【实现】

---

# 第四部分 · 生成流程输出契约

> 本章的**契约条款**于 v1.0 确定、v1.1 补齐两处漏洞，是 `schema_gen` 必须遵守的输出规范；
> 实现进度按 P1 → P2 → P3 分阶段落地，当前状态见 §7 D7（**不假装已做完**）。
> 写在这里是为了让生成器的输出从第一天就受本文约束，而不是等写完再补规格。

## 6.1 输入源分级（可靠性从高到低）

| 级 | 来源 | 能拿到什么 | 例子 |
|---|---|---|---|
| S1 | XSD / XML 实例 | 字段名、中文说明、类型、长度、精度、可空性、实体分组 | T/CICPA 0110—2026 通知页附件7 `PPE.xsd`：**9 实体 / 342 字段**；`xs:element` 总数 361（10 顶层实体 + 9 根容器 `ref` + 342 字段元素），`simpleType` 297（枚举/约束，非字段）。2026-11-06 实下载解析 |
| S2 | 国标 PDF 的数据元定义表 | 标识符、名称、说明、表4 卡片表的行 | 正文第 6 章 83 个数据元、表 4 的 48 行（**待与 S1 交叉对账，对不上就登记差距，不杜撰**） |
| S3 | 样张 OCR 文本 / 行框 | 票面真实标签词、位置关系、版式片段 | `samples/*.txt` + `.lines.json` |
| S4 | 用户口述 / 手工描述 | 标题词、字段清单 | `schemas.cli new` 模板 |

**MUST**：引用标准时给出官网 URL + 标准号 + sha256；来源不明的附件只作线索，不作论据。
**MUST**：字段数由解析器给出，**不得硬编码**；统计口径 MUST 区分「`xs:element` 总数」
与「字段元素数」——`PPE.xsd` 的 361 含 10 个顶层实体与 9 个根容器 `ref`，
真正的字段是 342 个，把总数当字段会凭空多出 19 个空字段。
`xs:simpleType` 多为枚举与类型约束，MUST NOT 当成字段。
（立项前真跑解析时曾把 361 误判为「与 342 对不上」，已按实际结构纠正并写回本节——
规格本身也要接受真跑验证。）

> **S1 附件的两套编号（v1.1 补）**：通知页把 XSD 包叫「附件 7」、XML 实例包叫「附件 8」，
> 而 zip 内目录名是标准正文的「附件5：文件输出格式」「附件6：实例」。台账 MUST 同时记两套，
> 不要混用。zip 内中文名无 UTF-8 flag、是 **GBK**，reader MUST 用
> `name.encode('cp437').decode('gbk')` 还原；实例包里固定资产文件名为 `PPE.XML`（大写扩展名），
> 匹配 MUST 大小写不敏感。

## 6.2 IR（中间表示，不对外）

```python
{
  "entity": str,          # 实体 / 分段
  "key": str,             # 目标 schema.yaml 的字段 key
  "label": str,           # 中文名（票面标签候选）
  "doc": str,             # xs:documentation / 数据元说明
  "dtype": str,           # string / decimal / date / int …
  "max_length": int|None,
  "digits": (int,int)|None,   # totalDigits, fractionDigits
  "nillable": bool,
  "source": str,          # S1/S2/S3/S4 + 定位信息
  "confidence": float,
}
```

IR **MUST NOT** 写进 schema 包；包里只落 §3 的四个文件 + samples + provenance。

## 6.3 S1（XSD）→ `schema.yaml` 映射规则

| XSD | 目标 | 规则 |
|---|---|---|
| `xs:element/@name` | `fields[].key` | 转小写下划线；截断到标识符规则；冲突加实体前缀 |
| `xs:documentation` | `fields[].label` | 取中文说明的首个短语；同时留作 detect 关键词候选 |
| 实体 / complexType | `sections[]` | 一个实体一段 `mode: kv`；行式明细实体用 `mode: table` |
| `maxLength` / `totalDigits`+`fractionDigits` | `fields[].regex`（**候选**） | 生成形状约束（如 `^\d{1,10}(\.\d{1,2})?$`），**MUST** 经样张复核后才启用 |
| `nillable=false` | `required: true`（**候选**） | 标准的可空性 ≠ 票面一定印着该字段；**MUST NOT** 直接照搬，否则 `strict` 会让每页都未命中 |
| 重复元素 | `data-repeat` / table 段 | 有边界标题时写 `start` / `stop` |

## 6.4 能生成什么、不能生成什么（决定性区分）

| 能机械生成 | **不能**自动生成 |
|---|---|
| 字段集、实体分段、类型/长度约束、中文名、版本与 provenance、样张骨架、**保守 `detect` 与低优先级** | **定位策略**（票面标签别名、`anchor` 方向、票面专用 regex）、**能真正识别票面的** `detect` 关键词、版式 HTML |

原因：CICPA 这类标准描述的是**数据模型 / 输出格式**（第 7 章数据结构表 + 附录 A XML 格式），
**通篇不含版式与坐标**；财政部电子凭证类另有全国统一式样，但那是另一类输入。
因此生成器的正确产物是「**数据格式齐全、定位策略待人工/样张补齐**」的包，
MUST NOT 假装生成了可直接上线的抽取规则。

由此推出三条 MUST（**v1.1 补齐漏洞 A**，前两条 v1.0 已有，第三条是新增的）：

1. 生成器 **MUST** 在产出里明确标注哪些字段 `locator: pending`，并把它们从 `required`
   里排除；生成包 **MUST** 写 `strict: false`。否则 `required` 缺失会让每页都未命中。
2. 生成器 **MUST** 写一条**保守 `detect`**：默认用标准号 / 标准名作关键词
   （`--detect-hint` 可覆盖），这些词**不会**出现在真实票面上；同时 **MUST** 设
   `priority: 900`（最低优先级）。否则 `detect` 为空 → 运行时退化为「有正文就算命中」，
   一个数百字段的骨架包会在 **auto 模式下匹配每一页**，把正常票据渲染成全空的字段化版式
   ——比没有这个包更糟，且 `strict:false` 让 `bool(doc)` 恒真，日志会显示「命中」。
3. `verify` **MUST** 用一份与标准无关的样张断言「auto 模式不得误命中该生成包」
   （§6.6 第 4 步）。这条不能用单测里 `detect()` 的直接调用代替——必须走注册表
   `apply_schemas(..., "auto")` 的真实路径，才覆盖得到 priority 与短路顺序。

## 6.5 provenance（MUST 落盘）

生成包 **MUST** 携带来源信息，建议放 `manifest.json` 的扩展键：

```json
"x-provenance": {
  "generator": "schema_gen/<version>",
  "sources": [{"kind": "S1|XSD", "url": "...", "standard": "T/CICPA 0110—2026",
               "sha256": "...", "retrieved_at": "..."}],
  "generated_at": "...",
  "field_count": "<N：由解析器给出，不得硬编码>",
  "coverage": {"model": "<N>", "locator_resolved": 0, "locator_pending": "<N>"}
}
```

`x-*` 键 **MUST NOT** 影响装载；注册表忽略未知键。
`coverage.locator_resolved` 为 0 是生成包的正常状态，也是 §6.6 是否启用效果门禁的开关。

## 6.6 生成后自检流水线（v1.1 拆两层，补齐漏洞 B）

**为什么必须拆**：`check_samples()` 的 `hit` 判据是 `bool(doc)`。生成包
`strict: false` + `detect` 保守时，`extract()` 会返回一个「字段全空但结构合法」的 doc，
于是 **hit_rate 恒为 1.0** ——自检全绿，实际一个字段都没抽到。把这一条当门禁，
等于让生成器自己给自己发合格证。因此门禁只挂在**结构**上，抽取效果在定位策略补齐前**只报告**。

### 第一层 · 结构自检（门禁，MUST 全过才交付）

```
1. lint_package(pkg)                → 0 error（占位/repeat/正则/重复 key/table.start）
2. 注册表真实装载                    → get_registry().get(id) 非空，origin 正确
3. ≥1 份 samples、三套 render 不崩   → render_md/txt/html 均无残留占位/注释
4. IR 字段数 == XSD 解析出的字段数   → 逐一相等，不以「数量级相近」放过
5. 同输入两次生成逐字节一致          → sha256 对账（时间戳类字段 MUST 可冻结）
6. cli pack                          → 打印 sha256 并与 sha256sum 对账，zip 顶层目录 = id
7. 解压到 PIC2EDIT_SCHEMA_DIR        → cli list 显示 origin=user 与新 version
8. 真跑转换（--schema <id> --format json）
                                     → skill 的 schema.versions[id] 或页内
                                       schema_version 与包一致（形状见 D8）
9. 与 --schema none 对照             → 两者 pages[].raw_text 逐字节相同
10. auto 不误命中                    → 无关样张走 apply_schemas(..., "auto")，
                                       该生成包 MUST NOT 命中（§6.4 第 3 条）
```

第 8、9、10 步是「改完要验证是否真的符合需求」的具体化：**只看单测绿不算完成**。

### 第二层 · 抽取效果自检（此阶段**不设门禁**）

| 条件 | 行为 |
|---|---|
| `coverage.locator_resolved == 0`（生成包的正常状态） | `check_samples()` 的 `hit_rate` **只打印、不判通过**；`verify` MUST 显式打印 `locator_pending: N` 与「定位策略待人工/样张补齐」 |
| `coverage.locator_resolved > 0` **且** 样张是真实票面文本 | 才启用 `hit_rate ≥ 0.8` 门禁；未达标即拒绝交付 |
| 样张是生成的骨架文本 | 即使有 resolved 也只报告——骨架文本必然命中，不构成证据 |

`verify` 的输出 MUST 区分「结构自检通过」与「效果未验证」，**MUST NOT** 用一句
「全部通过」把两者混在一起（日志与产物不符比没有日志更坏）。

## 6.7 规模与扩展

- 数百字段级别（官网实测 `PPE.xsd` 有 **342 个字段元素**、分属 9 个实体）
  MUST 按实体拆 `sections`，单段字段数 SHOULD ≤ 30（超过则按语义再分组）；
  table 段设 `limit` 防止整页正文被收成明细行。
- 国标覆盖不到的票面要素（印章 / 二维码 / 手写 / 备注）用 `x-*` 前缀字段 +
  通用 `extra` 兜底区；跨分册引用数据元 MUST 标注来源分册，不要杜撰定义。
- 生成器 **MUST NOT** 修改消费侧任何代码；产出只能是数据包（硬约束②④）。

---

## 7. 规格与实现的差距清单（诚实登记，不假装做过）

| # | 条目 | 规范 | 现状 | 建议 |
|---|---|---|---|---|
| D1 | `table` 段 `start` 必填 | MUST + lint 告警 | **v1.1 已补** `lint_package()` warning（不阻断，兼容存量包） | 生成器输出的 table 段 MUST 带 `start` |
| D2 | yaml/manifest 的 version/title/priority 一致 | SHOULD | **v1.1 已补** lint warning；生成器输出时保证一致 | 保持 |
| D3 | `origin` 取值 | `builtin`/`user` | env 与 extra_dirs 也记 `user` | 可接受，文档已写明 |
| D4 | 四个采样参数进缓存键 | 理论上 SHOULD | 未进；SKILL.md 已写明重跑办法 | 若并入，须同步 §5.4、SKILL.md、缓存测试 |
| D5 | 声明式 html_table / 合并单元格 | 不在本规格范围 | 由 `utils/table_html.py` 在无 schema 路径提供 | 不混进 schema 契约 |
| D6 | 输入分诊（PDF 文本层 / 内嵌 XML） | §4.8 提到 | 未实现（审查报告 §6.2/6.3） | 单独立项，本规格不承诺 |
| D7 | §6 生成器 | 契约已定（v1.1） | **实现中**：按 `docs/schema-gen-plan.md` 的 P1 输入层 → P2 本体 → P3 真跑分阶段落地 | 三个 Phase 各自收口（全量测试 + 独立 commit + 工作区干净）；完成后本行改「已实现」并附 §9 证据 |
| D8 | `json` 顶层 `schema` 形状 | 两侧 SHOULD 一致 | GUI = `{id:version}`；Skill = statistics（版本在 `versions` 子键）。真跑中文样张核对过 | 不改现有契约以免破坏消费者；调用方以每页 `schema_version` 为准；统一需单独发版 |

---

## 8. 验收清单（每条都可复核）

```bash
cd ocr_app
V=.venv/bin/python3

# 1. 规格里声明的常量/默认值与源码对账（可执行契约）
CODEBUDDY_SAFE_DELETE_ENABLED=0 $V -m pytest test_schema_spec.py -q

# 2. 现有两个内置包都合法
$V -m schemas.cli validate asset_card && $V -m schemas.cli validate invoice
$V -m schemas.cli list                      # 看 origin 列
$V -m schemas.cli test asset_card           # 命中率 100%

# 3. 最小合法包往返（附录 A 的示例真的能被装、被校验、被打成 zip）
T=$(mktemp -d); $V -m schemas.cli new spec_demo --title 规格示例 --dir "$T"
$V -m schemas.cli validate "$T/spec_demo"
$V -m schemas.cli pack "$T/spec_demo" --out "$T/spec_demo.zip"   # 打印 sha256
sha256sum "$T/spec_demo.zip"                # 与上一行对账

# 4. 覆盖安装真的生效（拷内置包改版本号后 origin=user / 版本变化）
cp -r schemas/asset_card "$T/" && sed -i 's/"version": *"1.0.0"/"version": "9.9.9"/' "$T/asset_card/manifest.json"
PIC2EDIT_SCHEMA_DIR="$T" $V -m schemas.cli list

# 5. 真跑一次转换并核对产物元数据（不靠单测自证）
$V ../.workbuddy/skills/pic2edit-ocr/scripts/pic2edit_convert.py <一张图> \
   --schema auto --format json --out "$T/out.json" --cache
# 断言：json.source 只有文件名、schema.versions 含 <id>:<version>、
#       每页 schema_version 与包一致、全文不含绝对路径（形状差异见 §7 D8）

# 6. 缓存两遍逐字节一致 + html 不退化
$V ../.workbuddy/skills/pic2edit-ocr/scripts/pic2edit_convert.py <同一张图> \
   --schema auto --format html --out "$T/out2.html" --cache
grep -c 'data:image/png;base64' "$T/out2.html"   # 必须与首跑一致
```

---

## 9. 2026-11-06 真跑记录（本版规格的验收证据）

规格不是写完就算数。以下都是**实际执行命令**观察到的现象，不是测试自证；
临时样本放在 `<tmp>/` 下，用 `PIC2EDIT_SCHEMA_DIR` 隔离，未改动内置包。

| 验收动作 | 观察结果 |
|---|---|
| `schemas.cli list` | `asset_card v1.0.0 builtin 20` / `invoice v1.0.0 builtin 100`，来源列是 origin |
| `validate asset_card` / `validate invoice` | 均「无问题」 |
| `test asset_card` | 2 份样张，命中率 100%，`depreciation` 表 2 行 |
| `new spec_demo` → `validate` → `pack` | 4 个文件、`sha256` 与 `sha256sum` 逐字符一致、zip 顶层目录 = `spec_demo` |
| 附录 A 完整示例包 `spec_full` | `validate` 无问题；1 份样张命中率 100%（2 字段 / 2 表格行）；`pack` 成功 |
| 覆盖安装（拷 `asset_card` 改 1.0.0→9.9.9） | 列表显示 `asset_card v9.9.9 user`，证明 origin 与覆盖机制都生效 |
| 中文合成样张 `--schema spec_full --format json --cache` | `[schema] 1/1 页命中`；`doc_no=SP-001`、`remarks=规格验证样张`；`schema.versions={'spec_full':'1.2.3'}`；页内 `schema_version=1.2.3`；`source=spec_sample.png`（只文件名）；全文无 `/tmp/` |
| 同一注册表直接调 `apply_schemas` + 三套 `render_schema` | md `### 一、基本信息` 且含 `\| --- \|`；txt `[一、基本信息]`；html 版式 `class="spec"` 生效、无残留 `{{`、无注释、`data-repeat` 生成 2 行；CSS 抽出 `.spec` |
| `--schema none --format html` 缓存命中再跑 | 两次日志都是 `[render] html 共 1 页：原图垫底×1`，产物 `data:image/png;base64` 均为 1（**未退化成纯文本**） |
| `--schema spec_full` vs `none` 的 json 对照 | `pages[0].raw_text` 逐字节相同；`none` 侧页内**没有** `schema` 键；命中侧 md 字段化且有 `<!-- schema: … -->`，none 侧是识别原文 |

真跑还纠正了规格初稿的一处错误：`json` 顶层 `schema` 在 skill 侧是 statistics、
在 GUI 侧是 `{id:version}`，已按实测改成两张表并登记为 D8。

---

## 附录 A · 最小合法声明式包

```jsonc
// manifest.json
{
  "id": "spec_demo",
  "title": "规格示例单据",
  "version": "1.0.0",
  "kind": "user",
  "priority": 30,
  "schema": "schema.yaml",
  "layout": "layout.html"
}
```

```yaml
# schema.yaml
id: spec_demo
title: 规格示例单据
version: 1.0.0
priority: 30
strict: true
detect:
  any: [规格示例单据]
  none: [增值税]
sections:
  - key: header
    label: 一、基本信息
    mode: kv
    fields:
      - {key: doc_no, label: 单据编号,
         regex: '单据编号[：:\s]*([A-Za-z0-9\-_/]+)',
         anchor: right, required: true}
  - key: items
    label: 二、明细
    mode: table
    start: 明细
    stop: 合计
    columns:
      - {key: name, label: 名称}
      - {key: amount, label: 金额}
  - key: remarks
    label: 三、备注
    mode: text
    regex: '备注[：:]\s*(.+)'
```

```html
<!-- layout.html：HTML 注释会被丢弃，可放心写示例 -->
<style>.spec { border: 1px solid #ccc; padding: 12px; }</style>
<div class="spec">
  <h2>规格示例单据</h2>
  <p>单据编号：{{ doc_no }}</p>
  <table>
    <tr data-repeat="items"><td>{{ name }}</td><td>{{ amount }}</td></tr>
  </table>
  <p>{{ remarks }}</p>
</div>
```

`samples/demo.txt`（含 `规格示例单据` / `单据编号：SP-001` / `明细` 段 / `合计` / `备注：…`）
加一份 `.lines.json` 即可跑 `cli test`。

---

## 附录 B · 字段速查

| 想改什么 | 改哪里 | 章节 |
|---|---|---|
| 下拉里显示的名字 / 版本号 / 谁先试 | `manifest.json` 的 `title`/`version`/`priority` | §3.3 |
| 判断「这一页像不像我」 | `schema.yaml` 的 `detect` | §3.5 |
| 抽什么字段、怎么定位 | `schema.yaml` 的 `sections` | §3.6 |
| 缺字段算不算命中 | `schema.yaml` 的 `strict` / `required` | §3.4 §3.7 |
| 输出长什么样 | `layout.html` | §4.2 |
| 三态怎么选 | `--schema` / GUI 下拉 | §5.2 |
| 缓存里能不能存 | §5.4 的派生数据清单 | §5.4 |
| 加一种新凭证 | 加一个目录，**不改转换代码** | §3.1 |

## 附录 C · 规格 ↔ 源码对账表

| 规格条目 | 源码符号 |
|---|---|
| 三级发现 / 覆盖 / 坏包降级 | `schemas/registry.py::SchemaRegistry._roots` / `_scan` / `_fail` |
| 三态解析 | `schemas/registry.py::SchemaRegistry.resolve` / `candidates` |
| 页面三键写入 / 统计 | `schemas/registry.py::SchemaRegistry.apply` / `_apply_one` |
| 渲染分派 / CSS 汇总 | `schemas/registry.py::SchemaRegistry.render` / `css_for` |
| 元信息清单 | `schemas/base.py::info_of` / `SchemaProvider` |
| 常量白名单 | `schemas/runtime.py::SECTION_MODES` / `ANCHORS` |
| 载入与三态结构校验 | `schemas/runtime.py::load_spec` / `build_provider` |
| detect 求值顺序 | `schemas/runtime.py::DeclarativeProvider.detect` |
| 字段三级取值 | `schemas/runtime.py::_field_value` / `_inline_value` / `_anchored_value` |
| 表格分段 | `schemas/runtime.py::_table_rows` / `_cut_segment` / `_rows_from_lines` / `_rows_from_text` |
| 版式两种语法 | `schemas/runtime.py::render_layout` / `_PLACEHOLDER_RE` / `_REPEAT_RE` / `_lookup` |
| doc 形状 | `schemas/runtime.py::DeclarativeProvider.extract` |
| lint 规则 | `schemas/lint.py::lint_package` / `_lint_plugin` / `check_samples` |
| B 侧 CLI | `schemas/cli.py::main` / `cmd_new` / `cmd_pack` / `_SAFE_ID_RE` |
| HTML 清洗白名单 | `utils/html_safety.py::sanitize_html` / `ALLOWED_TAGS` / `ALLOWED_ATTRS` |
| 五格式导出 / 三档降级 | `utils/export_formats.py::export_html` / `export_json` / `export_by_ext` |
| 忠实版式 | `utils/faithful_html.py::page_with_image` / `faithful_css` |
| 缓存键 / 命中补算 | `pic2edit_convert.py::_cache_key_for` / `_ensure_page_lines` / `_apply_schema_stats` |
| 可执行契约 | `test_schema_spec.py`（把本表里的常量与默认值逐条钉住） |
```
