# 固定资产卡片

## 功能与适用范围

把固定资产卡片的识别文本抽成结构化字段，并用 `layout.html` 输出人读票面版式。
本包是**声明式参考实现**（`form: declarative`）：零自定义 Python，
只有 `schema.yaml` + `layout.html`，由通用 `runtime.py` 驱动。
适用于含「固定资产卡片 / 资产卡片 / 卡片编号」且同时出现「资产编号」的单据；
`detect.none` 排除「增值税 / 发票代码」，不会抢发票。

## 使用方法

- GUI：凭证类型选「固定资产卡片」（或 `auto`，其 priority=20 先于发票）→ 转换 → 导出。
- CLI：`python -m schemas.cli validate asset_card --samples` /
  `test asset_card` / `pack asset_card`。
- 样张：`samples/basic.txt`（多栏）、`single_column.txt`（单栏），
  各带同名 `*.expected.json` golden。

## 主要特点与已知边界

- 新增一种结构规整的凭证 = 写一个目录，**不改一行转换代码**。
- 已知边界：声明式无跨页合并、多遍扫描、几何推理；
  表格列按声明顺序左→右对应，版式变化需调整 `columns` 顺序。
- **HTML 版式只是人读预览，不是合规归档版式**；
  电子会计档案的归档版式推荐 OFD（DA/T 94-2022），HTML 无数字签名与监制章套印。

## 字段来源与标准编号

字段取舍参考《注册会计师审计数据规范 固定资产》（T/CICPA 0110—2026）
固定资产卡片表的常用项；字段字典见 `standard.json`，
标准编号 / 来源 / 引用许可写在 `standard.standard`。
标准只描述数据模型，不含坐标与版式；定位策略见 `schema.yaml`。
**不整份内嵌受版权保护的标准全文**。

## 版本与变更记录

- 1.0.0：声明式 schema + layout.html + 两份样张。
- 1.0.0（包结构 v2）：新增 README / standard.json / 两份 golden 并声明
  `contract_version: 2`；抽取行为未变。

## 版权与许可

本包随 pic2edit 以 MIT 许可发布；第三方署名见仓库 `THIRD_PARTY_NOTICES`。
所引标准仅用于字段取舍参考，原文版权归发布方所有，本包不含其全文。
