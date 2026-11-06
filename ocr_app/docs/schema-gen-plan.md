# Schema 生成器工作计划（2026-11-06 用户确认版）

> 状态：**已确认，P1–P3 全部收口（2026-11-06）**。前置条件：技术规格 `schema-technical-spec-v1.md`
> （v1.0 已落地，本轮升 v1.1）。本文是执行计划，规范性契约以技术规格为准。
> 用户指令：「先做一个工作计划，等我确认之后再尽快执行」，确认于 2026-11-06。
> 执行约定：有选择时按推荐执行，中途不提问；只在硬阻塞时停下。

---

## 0. 确认前的实测结论（已并入本计划）

在出计划后、执行前，先只读实测了官网连通性（结论详见当日工作日志）：

| 项 | 实测 |
|---|---|
| 官网通知页 / 8 个附件 | 直连全部 `200`，0.05–0.14s，**无需代理** |
| 代理环境 | 本沙箱无 `all_proxy`、`127.0.0.1:1080` 未监听；DNS 解析到 `198.18.0.22`（透明代理接管）。原计划里「被墙则用 `px_socks_http.py` 转发」的兜底条款在本环境用不上，P1 不引入 |
| 附件 7 XSD 包 | 78,266 B，sha256 `d39b73781b19b172aedfb2b36728ec4039e6e7b4b473cf00940119b64b6689b7` |
| 附件 8 XML 实例包 | 50,404 B，sha256 `8f65fa2f14ee82689cd6563968dbab718002e8584b532d69892e6fdc0371db04` |
| 附件 2 固定资产 PDF | 667,246 B，sha256 `e6d868517b6022f83a291be8e4e5fa20c593958311719c55b1a5f9dba62776d4`，144 页，`T/CICPA 0110—2026` |

两条会改变实现的发现：

1. **编号有两套**：通知页叫「附件 7 / 8」，zip 内目录名却是标准正文的
   「附件5：文件输出格式」「附件6：实例」。台账 MUST 写清，别混。
   zip 内中文名无 UTF-8 flag、是 GBK，reader 要
   `name.encode('cp437').decode('gbk')` 还原；`PPE.XML` 扩展名大写，匹配不能大小写敏感。
2. **「361 vs 342」不是矛盾，是统计口径不同（P1 解析时纠正了自己的误判）**：
   `ElementTree` 解析 `PPE.xsd` 得到 `xs:element` 总数 **361** =
   10 个顶层实体 + 9 个根容器 `ref` + **342 个字段元素**；`complexType` 10（全内联）、
   `simpleType` 297（枚举与类型约束，MUST NOT 当字段）。9 个实体的字段数依次为
   3 / 67 / 41 / 51 / 40 / 22 / 25 / 46 / 47，合计 342。
   所以计划初稿的「9 实体 / 342 字段」**是对的**；上一轮连通性报告把它判成
   「对不上实测」是误读。规格 §6.1 已按实测口径写清，字段数仍 MUST 由解析器给出、
   不得硬编码（其它分册字段数不同），但固定资产这一册的验收断言可以精确用 342。

---

## 1. 两个必须先补的规格漏洞（P1 第一件事）

| # | 漏洞 | 后果 | 补法 |
|---|---|---|---|
| A | §6.4 只说「字段标 `locator: pending`、从 `required` 排除」，**没说 `detect` 怎么办** | `detect` 为空 → 运行时退化为「有正文就算命中」；auto 模式下这个数百字段的骨架包会**匹配每一页**，把正常票据渲染成全空的字段化版式，比没有更糟 | 生成包 MUST 写保守 `detect`（默认用标准号作 `--detect-hint`，票面不会出现）+ `priority: 900`；`verify` MUST 断言「auto 用无关样张不得误命中」；规格 §6.4 补这条 MUST |
| B | §6.6 第 2 步 `check_samples` 的 `hit_rate ≥ 0.8`，在 `strict: false` 下 `bool(doc)` **恒真 → 恒 1.0 假通过** | 自检全绿，实际一个字段都没抽到 | 自检拆两层：**结构自检**（lint 0 error / 包装载成功 / ≥1 份 samples / 渲染不崩 / IR 字段数与 XSD 逐一相等 / 两次生成逐字节一致）设门禁；**抽取效果自检**在 `locator_pending > 0` 时只报告、不设 hit_rate 门禁。`verify` MUST 显式打印 `locator_pending: N` |

> **P3 真跑纠正（2026-11-06）**：漏洞 B 里「`strict:false` 下 `bool(doc)` 恒真 →
> 恒 1.0」与 `runtime.extract()` 的实际行为不符——它在零字段时直接返回 `None`，
> 骨架包实测 `hit_rate=0.0`。**结论不变**（自检仍拆两层、效果不设门禁），
> 但理由已按实测改写，见规格 §6.6 与 §9.1。这是「规格也要真跑验证」的又一例。

**最要紧的边界**：`schemas/` 本身是注册表 `BUILTIN_ROOT`（扫子目录找 `manifest.json`），
生成器代码与产物都 MUST NOT 放进它，否则会被当成 schema 包装载。生成器放 `ocr_app/schema_gen/`，
产物默认落 `--out`（cwd 下 `generated/<id>`）。

---

## 2. Phase 划分与收口口径

**每个 Phase 收口 = 全量 pytest 绿 + 独立自解释 commit + 工作区干净**，三者齐备才进下一个。
这是项目既定纪律（见 `.workbuddy/memory/MEMORY.md`）。

### P1 · 契约补漏 + 输入层（commit 1）

- 本文 + 规格升 **v1.1**：§6.4 补 detect MUST、§6.6 拆两层自检、§5.9 加生成器 CLI 行、
  §6.1/§6.5/§6.7 数字参数化、C11/D1/D2/D7 状态更新、§3.13 lint 表补两行。
- 补 lint 两条：`table` 段缺 `start` → `warning`（D1）；manifest 与 yaml 的
  `title/version/priority` 一致性 → `warning`（D2）。**不阻断打包**，否则会破坏现有内置包。
- 新建 `ocr_app/schema_gen/{__init__,sources,readers}.py`：
  - `sources.py`：官网下载（URL + 标准号 + sha256 台账）、本地缓存、`--offline` 支持；
    校验失败 **MUST NOT** 换镜像重试（沿用下载链既有安全规则）。
  - `readers.py`：XSD / PDF 数据元 / XML 实例（XBRL 形态）三 reader，
    只用 `xml.etree.ElementTree` + `PyYAML` + `pypdfium2`，**零新依赖**。
- 台账 `docs/standard-sources-2026-11-06.md`：URL、标准号、sha256、附件编号差异、GBK 说明。
- 测试：内置最小 XSD 夹具单测（**不联网**）；标识符连续性（防贪婪正则吞块）、
  `nillable` 不照搬 `required`、`maxLength` 只作候选 regex、GBK 文件名还原、
  `PPE.XML` 大小写不敏感。
- 真下载附件 7/8/2 并 sha256 对账；PPE.xsd 以实际解析数为准。

### P2 · 生成器本体（commit 2）

- `schema_gen/{ir,emit,verify,cli}.py` + `__main__.py`，唯一入口 `python -m schema_gen`。
- IR **只内存不落盘**；产出 `manifest.json`（含 `x-provenance`：generator / URL / 标准号 /
  sha256 / field_count / coverage）+ `schema.yaml` + `samples/` 骨架，**不生成 layout.html**。
- emit 测试：保守 `detect`、`priority: 900`、`locator: pending`、`required` 全排除、
  `strict: false`、table 段带 `start`、单段 ≤30 字段、IR 不进包、`x-*` 不影响装载、
  **同输入两次生成逐字节一致**。
- verify 六步 + auto 不误命中；CLI 路径守卫（拒绝写入 `schemas/` 与根目录外）。
- 扩展 `test_schema_spec.py` 把 §6 新常量与源码静态对账。

### P3 · 真跑与收口（commit 3）

- 覆盖安装到临时 `PIC2EDIT_SCHEMA_DIR`，按 §6.6 逐步真跑：
  `list` 显示 `origin=user` / 新版本 → `pack` 的 sha256 与 `sha256sum` 对账 →
  `--schema <id> --format json` 页内 `schema_version` 与包一致 →
  与 `--schema none` 对照 `raw_text` 逐字节相同 → auto 无关样张不误命中 →
  缓存两遍 html 不退化 → 全文无绝对路径。
- `schemas/README.md` 加生成器入口；验证矩阵第 12 项 ❌→✅ 并附可复核证据；
  生成流程使用说明写进 `schema_gen/README.md`。
- ~~PDF 数据元 83 个与 XSD 交叉对账~~ → **已实测完成**：「83」查无依据，不沿用；
  改为可复核口径，结论 9/9 实体名、120/120 唯一字段名、归一化空白后 101/101
  中文标签对上，详见台账 `standard-sources-2026-11-06.md` §7 与规格 §9.1。
  PDF 仍不含票面定位策略与版式，故不改变「只产骨架」的边界。

---

## 3. 明确不做（防范围膨胀）

- 不生成定位策略 / 票面别名 / `anchor` / 版式 HTML —— 规格 §6.4 的决定性区分：
  国标只描述数据模型，生成器只产「数据格式齐全、定位策略待补」的骨架。
- **不改消费侧一行代码**：`registry` / `runtime` / `worker` / skill / GUI 全部零改动
  （P1 的两条 lint 属生产侧工具 `schemas/lint.py`，不是消费侧）。
- 不做输入分诊（路线图 11、差距 D6）：内嵌 XML 的财政电子票据不该 OCR，独立立项。
- 不动 D4（采样参数进缓存键）、D8（json 顶层 `schema` 两侧形状统一）—— 与本项无关。
- 不把国标原件或生成包提交进仓库：只提交生成器代码、测试、台账（URL+标准号+sha256）、文档。
- HTML 版式仍非合规 OFD 文件，这条边界不因生成器立项而改变。

## 4. 风险与兜底（已定，不再中途询问）

- 官网下载失败 → 用附件 8 的 XML 实例构造最小 S1 夹具完成链路，台账注明
  「官网原件待本机复核」，**绝不伪造 sha256**。
- 字段数按实体拆段、table 段设 `limit`；跨分册引用的数据元只标来源、不杜撰定义。
- 临时目录一律用 `/home/michael/wb_cleanup.py` 收尾（不用 `rm -rf`、不弹确认）。

## 5. 验收口径

- 测试 264 → 310+ passed，ruff 全绿，工作区干净，无残留引擎进程。
- 「改完要验证是否真的符合需求」：每个对外契约至少一种真跑证据
  （命令 + 产物对账 / 真实覆盖安装场景 / 走缓存与不走缓存的逐字节对照）。
- 单测自证不算完成；日志与产物不符比没有日志更坏。
