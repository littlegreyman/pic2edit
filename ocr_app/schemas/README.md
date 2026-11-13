# 凭证 schema：格式说明与制作流程

> **规范性文档**：字段字典、版式显示要求、转换适用参数、以及「schema 生成流程」的
> 输出契约，见 [`../docs/schema-technical-spec-v1.md`](../docs/schema-technical-spec-v1.md)。
> 本文是快速上手教程；两者冲突时以规格为准（规格里另有「与实现的差距清单」）。

本目录是**消费侧**（转换流程怎么用 schema）。制作一种新凭证所需的全部知识也在这里，
因为"怎么造"和"造出来长什么样"是同一件事的两面。

一句话：**新增一种凭证 = 写一个目录，不改一行转换代码。**

> **从标准原件起骨架（v1.1 新增第三个入口）**：`python -m schema_gen`，用法见
> [`../schema_gen/README.md`](../schema_gen/README.md)。它从官方 XSD 产出声明式包骨架，
> 但**只产数据格式、不产定位策略与版式**：字段全部 `locator: pending`、
> `strict: false`、保守 `detect`（标准号）、`priority: 900`、不生成 `layout.html`。
> 产物默认落 `ocr_app/generated/<id>/`，**不自动安装**，也 MUST NOT 放进本目录
> （`schemas/` 是注册表扫描根）。补齐别名 / 锚点 / 边界并换成真实票面样张之前，
> 不要把它当可直接上线的抽取规则；`schemas.cli test` 的命中率在此阶段不构成
> 抽取效果证据（骨架包实测 `hit_rate=0.0`）。

```
schemas/<id>/
  manifest.json     # 元信息（纯数据）：id / title / version / priority / layout
  schema.yaml       # 字段与定位策略（声明式）
  layout.html       # 版式（可选；没有就用通用表格）
  samples/          # 样张（推荐 ≥3 份，lint/test 会跑）
  provider.py       # 仅复杂版式才需要：Python 插件逃生口
```

## 一、两条扩展档位

| 档位 | 需要写什么 | 适用 |
|---|---|---|
| **声明式**（默认） | `manifest.json` + `schema.yaml` +（可选）`layout.html` | 资产卡片、报销单、证照、合同封面——字段位置规则可描述的单据 |
| **插件式** | 上面的文件 + `provider.py`（暴露 `get_provider()`） | 发票这类版式多变、需要多遍扫描与几何推理的场景 |

发票走的是插件式（`invoice/`），它的 1300 行抽取逻辑**原样搬进**了 `invoice/extract.py`，
行为未改。它是 `provider.py` 这种逃生口的参考实现，不是声明式要追赶的目标。

## 二、schema.yaml 语法

```yaml
id: asset_card
title: 固定资产卡片
version: 1.0.0
priority: 20          # 小者先试；发票是 100，因为它的 detect 恒真，必须最后兜底
strict: true          # true：任何 required 取不到值即整页未命中（保守，推荐）

detect:               # 只回答"像不像我"
  any: [固定资产卡片, 资产卡片]   # 命中任一即可
  all: [资产编号]                 # 必须全部出现（做二次确认）
  none: [增值税, 发票代码]         # 出现任一即排除
  regex: ['卡\s*片\s*编\s*号']     # 可选：正则也必须命中

sections:             # 按导出顺序排列，label 就是导出的小标题
  - key: header
    label: 一、基本信息
    mode: kv
    fields:
      - key: card_no
        label: 卡片编号
        regex: '卡片编号[：:\s]*([A-Za-z0-9\-_/]+)'
        anchor: right
        required: true

  - key: depreciation
    label: 二、折旧明细
    mode: table
    start: 折旧明细        # 必须写：否则上半部分的键值行会被当成两列数据行
    stop: 合计
    columns:              # 顺序必须与票面从左到右一致
      - {key: period, label: 期间}
      - {key: amount, label: 本期折旧}

  - key: remarks
    label: 三、备注
    mode: text
    regex: '备注[：:]\s*(.+)'
```

### 字段取值三级策略

按顺序尝试，先到先得。**写得越具体越稳**，但都不是必须的：

1. `regex` —— 显式正则，`group(1)` 为值。号码、金额、日期这类有固定形态的字段首选。
2. **标签同行** —— `卡片编号：AC-001` 这种"标签与值在同一行"的形态，自动切出值。
   只要写了 `label` 就生效，不需要额外配置。
3. **坐标锚点** —— `anchor: right` 取同一视觉行右侧最近的框；`anchor: below` 取下方
   最近的框。用于"标签一格、值一格"的表格式票面。`anchor: none`（或等价的 `inline`）
   表示只靠前两条；**默认就是 `right`**，写字段时别以为不写 anchor 就不做坐标取值。

### 表格分段的三条硬要求

- **必须写 `start`**：表格从哪一行开始。声明了却找不到该标题 → 本段返回空。
  这是刻意的：退化成"整页都当表格"会产出一张混入表头、看似完整实则错误的数据表。
- 列的**顺序即票面顺序**，运行时按左→右依次填入。
- 无坐标时按"连续 2 个以上空白或制表符"切列；有坐标时按 y 聚行、行内按 x 排序，
  此时列数多于声明列数会把多余的并入最后一列。

## 三、layout.html 语法

只有两种语法，刻意不做成模板语言（能编程的 yaml/json 会比 Python 更难调）：

| 语法 | 作用 |
|---|---|
| `{{ card_no }}` | 取字段值，自动 HTML 转义；`{{! x }}` 不转义 |
| `{{ header.card_no }}` | 带分段前缀的写法，等价 |
| `<tr data-repeat="depreciation">` | 该元素按表格行重复，内部占位按行取值 |

`<style>` 里的内容会被抽出来拼进导出 HTML 的 `<style>`；HTML 注释会被丢弃，
所以注释里可以放心写示例。

## 四、制作与校验

```bash
cd ocr_app

# 1. 生成空壳（默认写到 ~/.pic2edit/schemas/）
python -m schemas.cli new my_doc --title 我的单据

# 2. 改 schema.yaml 的 detect 与字段，往 samples/ 放样张文本与期望输出

# 3. 结构与版式对账
python -m schemas.cli validate my_doc --samples

# 4. 命中率 + 期望字段逐项对账报告
python -m schemas.cli test my_doc

# 5. 看看注册表认不认
python -m schemas.cli list

# 6. 校验通过后打成可分发 zip（打印 sha256）
python -m schemas.cli pack my_doc
```

`validate` 检查的是**两个文件会不会漂移**，这也是本项目已经踩过的坑
（`docs/invoice_schema.md` 与抽取代码双写，文档长期落后）：

- 字段在版式里没有占位 → 警告（只取数不用版式是合法用法）；
- 版式里的占位在 schema 里不存在 → **错误**（会永远渲染成空，等于打错字）；
- `data-repeat` 指向的分段不存在或不是 table → **错误**；
- 正则编译失败、key 重复、mode/anchor 不认识 → 错误。

### 包结构 v2 五件套（`new` 生成的就是 v2）

`cli new` 与 `python -m schema_gen` 产出的包都带 `contract_version: 2`，
目录里除 `manifest.json` / `schema.yaml` / `layout.html` 外还有：

| 文件 | 作用 |
|---|---|
| `README.md` | 功能与适用范围 / 使用方法 / 主要特点与已知边界 / 字段来源与标准编号 / 版本与变更记录 / 版权与许可（六章固定，lint 会查） |
| `standard.json` | **数据规范**：字段字典 + 标准编号/来源/许可。只描述「有哪些数据元」，不写坐标与正则 |
| `samples/<name>.txt` | 输入：票面识别文本 |
| `samples/<name>.expected.json` | 输出：期望字段值（golden），`cli test` 逐项对账并打印差异 |

**数据规范与定位策略是两件事**：国家标准只写数据模型，通篇没有版式和坐标，
所以 `standard.json` 管字段定义、`schema.yaml` 管「怎么在票面上找到它」。
`schema.yaml` 里的字段 `key` 必须能在 `standard.json.fields` 里找到，
但反过来标准里定义了、暂时没定位策略的字段是允许的（生成骨架就全是
`locator: pending`）。

声明式包**不需要写 Python**——零自定义代码由 `runtime.py` 通用引擎驱动，
这是设计优点不是缺陷。是否自带 HTML 版式用 `layout_source` 声明三态：
`package`（本包 `layout.html`）/ `generic`（通用兜底）/ `none`（仅结构化数据）。
标准没有版式时不要编造，写 `none` 即可。

`expected.json` 的差异**只报告、不设门禁**：不同机器 OCR 质量会波动，
把它做成 CI 硬门槛会无故变红。它的用途是人工复核时一眼看出
`buyer.address="密码区"` 这类错值。

> 存量内置包尚未声明 v2，`validate` 只给一条 `info` 建议，不会判红。
> 完整规范与渐进纳管口径见 `docs/schema-technical-spec-v1.md` §3.1.1。

## 五、用户级目录

除内置的 `ocr_app/schemas/`，还会扫描：

1. `$PIC2EDIT_SCHEMA_DIR`（`os.pathsep` 分隔，测试与私有部署用）
2. `~/.pic2edit/schemas/`

同名 `id` **后者覆盖前者**，因此可以把内置 schema 拷出来改，不影响原包。
坏包只告警跳过，绝不让整个注册表崩掉——转换流程的底线是"没有 schema 也能跑"。

## 六、已知边界

- 声明式**没有**跨页合并、多遍扫描、几何推理能力。遇到这些请写 `provider.py`。
- 纯正则 + 标签邻近抽不到发票那种鲁棒性（专票/普票/数电/各省差异、密码区、跨栏、
  星号粘连、大写金额校验）。这也是发票留在插件式的真实原因，不是懒得迁移。
- 版式是"人读预览"，不是合规版式文件。电子会计档案的版式推荐是 OFD
  （DA/T 94-2022），HTML 复现没有数字签名与监制章套印。
