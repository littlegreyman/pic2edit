# schema_gen：从标准原件生成 schema 包骨架

> 规范性契约见 [`../docs/schema-technical-spec-v1.md`](../docs/schema-technical-spec-v1.md) §6，
> 执行计划见 [`../docs/schema-gen-plan.md`](../docs/schema-gen-plan.md)，
> 原件台账见 [`../docs/standard-sources-2026-11-06.md`](../docs/standard-sources-2026-11-06.md)。
> 本文是**使用说明**；与规格冲突时以规格为准。

## 它做什么（以及不做什么）

从官方标准原件（当前支持中注协 `T/CICPA 0110—2026` 的 `PPE.xsd`）读出数据模型，
产出一个**声明式 schema 包骨架**：`manifest.json` + `schema.yaml` + `samples/`。

**它只描述数据格式，不描述票面。** 国标 / XSD 里没有字段在票面上的位置、别名、
坐标或版式，所以生成器 MUST NOT 假装能生成这些：

- 所有字段 `locator: pending`，不写 `regex` / `anchor` / `required`；
- `strict: false`（否则必填缺失会让每页都不命中）；
- 保守 `detect`：默认用标准号作关键词（真实票面不会出现），`priority: 900` 最低优先级；
- 重复实体落 `table` 段，`start` 用同一个保守关键词，边界找不到时返回空段；
- 不生成 `layout.html`（版式无从推断）。

因此生成包**不是可直接上线的抽取规则**。真实票面在本阶段必然不命中，这是设计意图，
不要为了「让它命中」改松 `detect`。补齐票面别名、锚点、`start/stop` 边界，
并换成真实票面样张之后，才谈得上抽取效果。

## 为什么和 `schemas/` 分开

`ocr_app/schemas/` 是注册表 `BUILTIN_ROOT`（扫子目录找 `manifest.json`）。
生成器代码和产物都 MUST NOT 放进去，否则会被当成 schema 包装载。
产物默认落 `ocr_app/generated/<id>/`，**不自动安装**。

## 用法

```bash
cd ocr_app

# 联网取官网原件（首次），校验 sha256 后缓存到 ~/.pic2edit/standard_sources/
python -m schema_gen --id ppe_card --version 1.0.0

# 断网 / 复现：只读已校验缓存，并冻结时间使两次生成逐字节一致
python -m schema_gen --offline --frozen-time 2026-11-06T00:00:00+00:00 \
    --out generated/ppe_card --force

# 校验与分发
python -m schemas.cli validate generated/ppe_card
python -m schemas.cli pack generated/ppe_card --out /tmp/ppe_card.zip
```

stdout **只有产物绝对路径**（便于脚本消费），诊断与自检报告一律走 stderr。
退出码：`0` 成功且结构自检通过 / `1` 自检未通过（产物保留供排查）/ `2` 用法、输入或输出路径错误。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--source` | `xsd` | 输入源，当前只支持官网 XSD 包 |
| `--member` | `PPE.xsd` | zip 内成员名，大小写不敏感（原件里是 `PPE.XML` 这类大写扩展名） |
| `--id` | `ppe_card` | 包 id，1–32 位小写 `[a-z0-9_-]` |
| `--title` / `--version` / `--standard` | 见 `--help` | 元信息；`--standard` 同时是保守 detect 关键词 |
| `--detect-hint` | 标准号 | 覆盖保守 detect 关键词 |
| `--out` | `generated/<id>` | 必须在 `ocr_app/` 内，且不能是 `schemas/` / `.git` / 项目根本身 |
| `--cache-dir` | `~/.pic2edit/standard_sources` | 原件缓存目录 |
| `--offline` | 否 | 只读已通过 sha256 校验的缓存，不联网 |
| `--frozen-time` | 当前 UTC | 冻结 `generated_at`，用于逐字节一致性自检 |
| `--force` | 否 | 覆盖已存在的非空输出目录 |
| `--no-verify` | 否 | 跳过生成后自检（不推荐） |

## 生成后自检：两层，别混成一句「全部通过」

- **第一层 · 结构自检（门禁）**：lint 0 error、注册表真实装载、≥1 份样张、
  md/txt/html 三套渲染不崩、IR 字段数与 XSD 逐一相等、同输入两次生成逐字节一致；
  外加一条独立门禁：无关样张在 `auto` 与强制指定两条路径都 MUST NOT 命中生成包
  （走真实 `apply_schemas`，不直接调 `detect()`）。
- **第二层 · 抽取效果自检（只报告）**：打印 `locator_pending: N` 与 `hit_rate`，
  **不设门禁**。`check_samples()` 的 `hit` 是 `bool(doc)`，而 `runtime.extract()`
  在零字段时返回 `None`（骨架包实测 `hit_rate=0.0`）；补齐部分策略后又可能是
  「字段几乎全空」的 1.0。两端都不是效果证据。

结论固定写成「结构自检通过；抽取效果未验证；整体PASS」。

> 三态 `<id>` **仍走 `detect`**（规格 C4），只是把候选缩小到该 provider，
> 不是无条件套用。verify 报告里「强制指定…应为 None」验证的是 detect 保守。

## 覆盖安装（产物不自动安装）

```bash
mkdir -p "$SCHEMA_DIR" && cp -r generated/ppe_card "$SCHEMA_DIR/"
PIC2EDIT_SCHEMA_DIR="$SCHEMA_DIR" python -m schemas.cli list
# → ppe_card   v1.0.0   user   ...
```

也可以把 `pack` 出的 zip 解压到 `~/.pic2edit/schemas/`（zip 顶层目录须为 `<id>/`）。
`origin=user` 表示生效的是用户目录这一份；同名 id 会覆盖内置包。
转换侧三态与生成包无关：`--schema none|auto|<id>` 由 `backend_api` 统一解析。

## 原件与供应链

- 只从 `https://www.cicpa.org.cn` 下载；URL 与 sha256 钉死在 `sources.py`。
- 校验失败**不重试、不覆盖、保留 `.bad-<hash>` 证据**；`--offline` 只认 hash 一致的缓存。
- zip 成员读取防越界 / 符号链接，中文名按 GBK 还原，成员名大小写不敏感。
- 两套附件编号不要混：通知页叫「附件 7 / 8」，zip 内是标准正文的「附件5 / 附件6」。

## 已知边界（诚实清单）

- 定位策略全部 `pending`；`hit_rate` 不构成效果证据，真实票面不会命中。
- 当前 XSD 的 9 个实体都被判为重复，因此 16 段**全为 table**；这是解析结果，
  尚未按业务语义区分主从实体。342 个字段元素去重后只有 120 个唯一字段名
  （如 `Segment_Reference_Level_1` 在 8 个实体里复用）。
- 字段集已与标准正文 PDF 交叉对账（9/9 实体名、120/120 唯一字段名、
  归一化空白后 101/101 中文标签），数据模型一致，见台账 §7；
  但 PDF 仍**不含票面定位策略与版式**，这正是只产骨架的原因。
- HTML 版式是「人读预览」，不是合规 OFD（DA/T 94-2022），无数字签名与监制章套印。
- 真跑验收记录见规格 §9.1。沙箱 `ulimit -v 4GB` + seccomp 拦 CUDA，
  真跑用「预置识别结果缓存 + 合成样张」验证产物契约，**OCR 识别质量未验证**，
  须在用户原生桌面复核。
