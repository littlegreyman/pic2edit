# 增值税发票（纸电票 / 旧版）

## 功能与适用范围

把含**发票代码、密码区、地址电话栏、银行账号栏**的旧版增值税纸电票
（专票 / 普票）识别文本抽成结构化字段，输出票面版式 HTML 与字段化 md/json。
本包是 Python 插件式（`form: plugin`），抽取逻辑在 `extract.py`，
由 `runtime.py` 之外的自定义几何判定处理跨栏明细、密码区底纹与星号粘连。

**不适用于全面数字化电子发票（数电票）**——数电票请用独立的
`invoice_digital` 包。两包 `detect` 互斥：本包排除数电票票名特征，
`invoice_digital` 排除「发票代码 / 密码区」。

## 使用方法

- GUI：凭证类型选「增值税发票」→ 开始转换 → 导出 md / html / json 可见字段化结果；
  勾选「字段化预览」可在编辑区直接查看。
- CLI：`python -m schemas.cli validate invoice` / `test invoice` / `pack invoice`；
  转换侧用 `--schema invoice` 强制指定。
- 样张：`samples/shanghai.txt`（脱敏真机 OCR 文本）+ `shanghai.lines.json`（行框）
  + `shanghai.expected.json`（golden）。

## 主要特点与已知边界

- 判别式 OCR 优先、VLM 仅兜底；本包消费的是判别式全文，不走提示词模板。
- 已知边界：`buyer.address` 曾把「密码区」当地址、「合计」行曾混入明细，
  已修并有脱敏 golden 锁定。跨省版式差异仍需真机样张持续验证。
- **HTML 版式只是人读预览，不是合规归档版式**：无数字签名与监制章套印。
  数电票入账归档需含数字签名的 XML 数据电文、版式为 OFD
  （国家税务总局公告 2024 年第 11 号；电子会计档案另见 DA/T 94-2022）。

## 字段来源与标准编号

字段字典见 `standard.json`（27 项，与 `manifest.json.fields` 对账）。
票种差异参照《国家税务总局关于推广应用全面数字化电子发票的公告》
（2024 年第 11 号，2024-12-01 全国推广）：数电票删除上述四栏、号码改 20 位，
因此本包字段模型**不覆盖**数电票。标准只描述数据模型，不含坐标与版式；
定位策略在本包 `extract.py`。

## 版本与变更记录

- 1.0.0：插件式发票抽取与三套 `render_*`；字段与版式对账门禁。
- 1.0.0（包结构 v2）：新增 README / standard.json / golden 样例并声明
  `contract_version: 2`；修两处错抽；detect 增加数电票互斥。**抽取版本未变**。

## 版权与许可

本包随 pic2edit 以 MIT 许可发布；第三方署名见仓库 `THIRD_PARTY_NOTICES`。
所引税务公告与票样仅用于字段取舍参考，原文版权归发布方所有，本包不含其全文。
