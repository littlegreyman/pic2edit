# 全面数字化电子发票（数电票）

## 功能与适用范围

把数电票识别文本抽成结构化字段。本包是**独立 schema id**（用户决策 1），
不与 `invoice` 共用字段模型——数电票按《国家税务总局关于推广应用
全面数字化电子发票的公告》（2024 年第 11 号，2024-12-01 全国推广）
删除了发票代码、密码区、地址电话栏、银行账号栏，号码改为 20 位，
套用旧版纸电票模型必然抽错（P1 已修过「密码区当买方地址」）。

适用范围：票名含「电子发票（普通发票）/ 电子发票（增值税专用发票）/
全面数字化电子发票/数电票」，且有 20 位发票号码、不含「发票代码/密码区」。

## 使用方法

- GUI：凭证类型选「全面数字化电子发票（数电票）」，或用 `auto`
  （本包 priority=50，排在 `invoice`=100 之前）。
- CLI：`python -m schemas.cli validate invoice_digital` /
  `test invoice_digital` / `pack invoice_digital`；转换侧 `--schema invoice_digital`。
- 样张：`samples/digital_sample.txt` + `digital_sample.expected.json`（合成骨架）。

## 主要特点与已知边界

- 声明式零 Python；`detect` 与 `invoice` 互斥，`auto` 模式不会互相抢占。
- **本包是基础字段版**：覆盖票面基本信息与金额，**不含明细表**：
  明细区边界、跨栏与星号粘连需要真实数电票样张才能可靠定位。
  `strict: false`、所有字段 `required` 仅号码一项，避免骨架阶段整页不命中。
- **抽样效果尚未经真机验证**：当前样张是合成骨架，不是真实票面。
  拿到真机数电票后应补齐 `samples/` 与 `expected.json`，再评估命中率门禁。
- `layout_source: none`：标准只有数据模型、没有版式，故不编造 `layout.html`。
  **HTML 导出只是人读预览，不是合规归档版式**；数电票入账归档需保存
  含数字签名的 XML 数据电文，版式文件为 OFD（另见 DA/T 94-2022）。

## 字段来源与标准编号

字段字典见 `standard.json`，含 `removed_fields` 明示数电票不再存在的旧字段。
标准编号 / 来源 / 引用许可写在 `standard.standard`。
标准只描述数据模型，不含坐标与版式；定位策略见 `schema.yaml`。

## 版本与变更记录

- 1.0.0：独立包初版（决策 1），与 `invoice` 互斥；基础字段 + 合成骨架样张。

## 版权与许可

本包随 pic2edit 以 MIT 许可发布；第三方署名见仓库 `THIRD_PARTY_NOTICES`。
所引税务公告与票样仅用于字段取舍参考，原文版权归发布方所有，本包不含其全文。
