# 发票 Schema（增值税专用/普通/电子/数电票通用数据模型 · 对照国标）

> 依据三张纸质专票票样 + 国家现行标准提炼修订。
> **对齐基准**：GB/T 42965.1-2023《电子发票业务数据规范 第1部分：基本要素》（税务总局归口，现行）。
> **参考**：GB/T 36609-2018《电子发票基础信息规范》、GB/T 44554.2-2025 / 44554.5-2025《电子凭证入账要求》。
> 用途：RAG 采编中「发票类」文档的字段抽取目标结构、入库校验规则与一致性门禁基准。

## 0. 适用国家标准
| 标准号 | 名称 | 状态 | 与本 schema 关系 |
|---|---|---|---|
| **GB/T 42965.1-2023** | 电子发票业务数据规范 第1部分：基本要素 | 现行(2023-08-06) | **主对齐**：字段直接映射其数据元 EI1xx/EI2xx |
| **GB/T 42965.2-2023** | 电子发票业务数据规范 第2部分：特定要素 | 现行 | 建筑服务/不动产/差额征税等特定业务扩展（见 §5） |
| GB/T 36609-2018 | 电子发票基础信息规范 | 现行(2019-01-01) | 早期信息模型；提供安全/附加/红字等扩展字段 |
| GB/T 44554.2-2025 | 电子凭证入账要求 第2部分：增值税电子发票 | 现行 | 入账/归档要求，非字段 schema |
| GB/T 44554.5-2025 | 电子凭证入账要求 第5部分：全面数字化的电子发票 | 现行 | 数电票入账/归档要求 |

## 1. 层级数据模型（JSON 视图，含 std_id 对齐国标）

```
Invoice
├── header                票头 / 基础信息
│   ├── invoice_kind_code  发票种类代码      string?     GB/T 36609 发票种类代码（如 144031500...）
│   ├── invoice_type       发票种类          enum        增值税专用发票/普通发票/电子专票/电子普票/数电票…
│   ├── invoice_code       发票代码          string(10|12)?  数电票无此字段
│   ├── invoice_no         发票号码          string(8|20)   数电票为 20 位
│   ├── issue_date         开票日期          date
│   ├── check_code         校验码            string(20)?   电子/普票必备，专票部分版式也有
│   ├── machine_no         机器编号          string?
│   ├── agent_flag         代开标志          bool?       GB/T 36609 代开标志（税务代开）
│   ├── agent_org          代开单位          string?     代开机关名称
│   ├── list_flag          清单标志          bool?      明细超行数时附清单
│   ├── copy_type          联次              enum?      发票联/抵扣联/记账联/存根联（数电票无）
│   └── red_info           红字发票信息      {original_code, original_no}?  红冲时携带原蓝票
├── buyer                  购买方
│   ├── name                名称                       (EI112)
│   ├── taxpayer_id         纳税人识别号   string(15|18) (EI111)
│   ├── buyer_type          购买方类型     enum?       企业/个人/机关 (GB/T 36609)
│   ├── address             地址                       (EI113)
│   ├── phone               电话                       (EI114)
│   ├── bank_name           开户银行名称               (EI115)
│   └── bank_account        开户银行账号               (EI116)
├── items[]                开票项目（货物或应税劳务、服务名称）
│   ├── name                项目名称                   (EI201)
│   ├── spec                规格型号                   (EI202)
│   ├── unit                单位                       (EI203)
│   ├── quantity            数量                       (EI204)
│   ├── unit_price          单价                       (EI205)
│   ├── amount              金额（不含税）             (EI206)
│   ├── tax_rate            税率           number(%)    一般纳税人 (EI207)
│   ├── levy_rate           征收率         number(%)    小规模纳税人 3%/5% (EI208) ← 与税率区分
│   ├── tax_amount          税额                       (EI209)
│   ├── tax_incl_amount     含税金额       number?      (EI210) ← 国标单列
│   └── goods_tax_class     商品和服务税收分类编码 string(19)? (EI211) ← 电子票必填
├── totals                 合计
│   ├── total_excl_tax      合计金额                   (EI123)
│   └── total_tax           合计税额                   (EI124)
├── total_incl_tax         价税合计
│   ├── capital             价税合计（大写）            (EI126)
│   └── figures             价税合计（小写）            (EI125)
├── seller                 销售方
│   ├── name                名称                       (EI102)
│   ├── taxpayer_id         纳税人识别号   string(15|18) (EI101)
│   ├── address             地址                       (EI103)
│   ├── phone               电话                       (EI104)
│   ├── bank_name           开户银行名称               (EI105)
│   └── bank_account        开户银行账号               (EI106)
├── remarks                备注            string?     代开信息/销方章文/特定业务备注
├── signatures             签署
│   ├── payee               收款人                     (GB/T 36609)
│   ├── reviewer            复核人
│   ├── issuer              开票人                     (EI127)
│   └── seller_seal         销售方（章）   bool
└── security               安全/附加（电子票可选）
    ├── qr_code             动态二维码/二维码 string?   (数电票为动态二维码)
    ├── verify_url          查验URL        string?     全国增值税发票查验平台
    ├── electronic_signature 电子签名      string?      OFD 签章
    └── password_area       密码区         masked(掩膜后不入库，非数据字段)
```

## 2. 字段明细表（对齐 GB/T 42965.1-2023）

| 字段路径 | 中文名 | 类型 | 必填 | 示例 | 校验规则 | 国标 |
|---|---|---|---|---|---|---|
| `header.invoice_type` | 发票种类 | enum | 是 | 增值税专用发票 | 命中已知票种枚举 | — |
| `header.invoice_code` | 发票代码 | string | 条件 | 3400000000 | `^\d{10}$`或`^\d{12}$`；数电票无 | — |
| `header.invoice_no` | 发票号码 | string | 是 | 10000001 / 20位数电票 | 专票`^\d{8}$`；数电票`^\d{20}$` | EI121 |
| `header.issue_date` | 开票日期 | date | 是 | 2017年12月01日 | ISO 解析；≤入库日 | EI122 |
| `header.check_code` | 校验码 | string | 条件 | 9348…44080 | 电子/普票`^\d{20}$`；专票可选 | — |
| `header.copy_type` | 联次 | enum | 否 | 发票联 | 发票联/抵扣联/记账联/存根联；数电票无 | — |
| `header.agent_flag` | 代开标志 | bool | 否 | true | 税务代开时为真 | GB/T36609 |
| `header.agent_org` | 代开单位 | string | 否 | 靖江市国家税务局… | 代开机关 | GB/T36609 |
| `buyer.name` | 购买方名称 | string | 是 | 示例电机有限公司 | 非空 | EI112 |
| `buyer.taxpayer_id` | 购买方税号 | string | 是 | 91000000000000001X | `^\d{15}$`或`^[0-9A-Z]{18}$` | EI111 |
| `buyer.address` | 购买方地址 | string | 否 | 某某省某某市某某路 | **与电话分离** | EI113 |
| `buyer.phone` | 购买方电话 | string | 否 | 0000-0000001 | — | EI114 |
| `buyer.bank_name` | 购买方开户银行 | string | 否 | 示例银行城北支行 | **与账号分离** | EI115 |
| `buyer.bank_account` | 购买方账号 | string | 否 | 12345678901234567890 | — | EI116 |
| `items[].name` | 货物或应税劳务、服务名称 | string | 是 | 碳结圈 | 每行必填 | EI201 |
| `items[].spec` | 规格型号 | string | 否 | Φ90 | — | EI202 |
| `items[].unit` | 单位 | string | 否 | 吨 | — | EI203 |
| `items[].quantity` | 数量 | number | 否 | 4.736 | ≥0 | EI204 |
| `items[].unit_price` | 单价 | number | 否 | 3957.26 | ≥0 | EI205 |
| `items[].amount` | 金额（不含税） | number | 是 | 18741.61 | 2位小数 | EI206 |
| `items[].tax_rate` | 税率 | number(%) | 条件 | 17 | 一般纳税人 ∈{13,9,6,0} | EI207 |
| `items[].levy_rate` | 征收率 | number(%) | 条件 | 3 | 小规模 ∈{3,5}（与税率二选一） | EI208 |
| `items[].tax_amount` | 税额 | number | 是 | 3186.07 | ≈ amount×rate | EI209 |
| `items[].tax_incl_amount` | 含税金额 | number | 否 | 21927.68 | = amount+tax_amount | EI210 |
| `items[].goods_tax_class` | 商品和服务税收分类编码 | string(19) | 条件 | 1010101010000000000 | 电子票必填 19 位 | EI211 |
| `totals.total_excl_tax` | 合计金额 | number | 是 | 94578.64 | ≈ Σ amount | EI123 |
| `totals.total_tax` | 合计税额 | number | 是 | 16078.36 | ≈ Σ tax | EI124 |
| `total_incl_tax.capital` | 价税合计大写 | string | 是 | 壹拾壹万零陆佰伍拾柒圆整 | 中文大写 | EI126 |
| `total_incl_tax.figures` | 价税合计小写 | number | 是 | 110657.00 | = 合计金额+合计税额 | EI125 |
| `seller.name` | 销售方名称 | string | 是 | 示例贸易有限公司 | 非空 | EI102 |
| `seller.taxpayer_id` | 销售方税号 | string | 是 | 91000000000000002H | 同 buyer | EI101 |
| `seller.address` | 销售方地址 | string | 否 | 合肥市金寨路162号… | **与电话分离** | EI103 |
| `seller.phone` | 销售方电话 | string | 否 | 0000-0000002 | — | EI104 |
| `seller.bank_name` | 销售方开户银行 | string | 否 | 示例银行湖滨支行 | **与账号分离** | EI105 |
| `seller.bank_account` | 销售方账号 | string | 否 | 1234567890123456 | — | EI106 |
| `remarks` | 备注 | string | 否 | 代开企业名称… | 代开/特定业务 | — |
| `signatures.payee` | 收款人 | string | 否 | 张三 | — | — |
| `signatures.reviewer` | 复核人 | string | 否 | 李四 | — | — |
| `signatures.issuer` | 开票人 | string | 否 | 张三 | — | EI127 |
| `signatures.seller_seal` | 销售方（章） | bool | 否 | true | 红章存在性 | — |

## 3. 与国标的对照结论（是否需要修改 → 已修改）

| 维度 | 原 schema（票样版） | 国标要求 | 处理 |
|---|---|---|---|
| 地址/电话、开户行/账号 | 合并为 2 个字段 | 国标**分离**为 地址/电话/开户银行名称/开户银行账号 | **已拆分** |
| 税率 vs 征收率 | 仅 `税率` | 国标**区分** 税率(EI207) 与 征收率(EI208) | **已加 `levy_rate`** |
| 含税金额 | 无 | 国标单列 含税金额(EI210) | **已加 `tax_incl_amount`** |
| 商品和服务税收分类编码 | 无 | 国标 EI211（电子票 19 位必填） | **已加 `goods_tax_class`** |
| 校验码 | 仅备注"条件" | 电子/普票明确必备 20 位 | **提升为条件必填并加正则** |
| 代开 / 清单 / 红字 | 仅 remarks 提及 | 国标有 代开标志/代开单位/清单标志/红字发票信息 | **已加结构化字段** |
| 联次 | 作为枚举值 | 数电票**无联次** | **标注纸质专属、数电票变体去除** |
| 发票代码 | 必填 | 数电票**无发票代码**（单一票种统一赋码） | **改为条件、数电票变体去除** |
| 发票号码长度 | 8 位 | 数电票 20 位 | **改为 8\|20** |
| 字段标识 | 自命名 | 国标 EI 数据元 | **表内加 `国标` 列对齐 EIxxx** |

→ **结论：原 schema 与国标存在 7+ 处结构性不符，已全部修订对齐。** 修订后 schema 既能覆盖三张纸质专票票样，也符合 GB/T 42965.1-2023 基本要素，可直接作为字段抽取与校验基准。

## 4. 入库一致性校验规则（建议，用于无幻觉门禁）

1. **金额勾稽**：`total_incl_tax.figures == round(total_excl_tax + total_tax, 2)`（≤0.02 容差）。
2. **行项求和**：`total_excl_tax ≈ Σ items.amount`、`total_tax ≈ Σ items.tax_amount`（分尾差 ≤0.05）。
3. **行内勾稽**：`items.tax_amount ≈ round(amount × (tax_rate‖levy_rate)/100, 2)`。
4. **含税勾稽**：`items.tax_incl_amount ≈ round(amount + tax_amount, 2)`。
5. **税号合法**：buyer/seller 税号匹配 15 位老税号或 18 位统一社会信用代码（末位可为 X）。
6. **中文大写 ↔ 小写**：`total_incl_tax.capital` 解析后与 `figures` 一致。
7. **日期**：`issue_date` 可解析且 ≤ 当前日期；数电票号码末 15 位为顺序码。
8. **不变量**：`password_area` 已掩膜，不得作为字段入库；严禁由模型臆造任何上述字段。

## 5. 变体子结构（独立于主 schema 的专用票种 / 业务扩展）

> 以下子结构在「主 schema」基础上增减字段，用于覆盖全部发票票种。抽取器应按 `header.invoice_type` 选择对应子结构。

### 5.1 数电票（全面数字化的电子发票，GB/T 44554.5-2025）
- **删除**：`invoice_code`、`copy_type`、`password_area`（数电票无密码区密文、无联次、单一票种统一赋码）。
- **变更**：`invoice_no` 固定 **20 位**（1-2 公历年度后两位、3-4 省级行政区划、5 开具渠道、6-20 顺序码）。
- **合并**：`items[].tax_rate` 与 `levy_rate` 合并为 **`tax_rate_or_levy`**（票面印作「税率/征收率」）。
- **新增**：`dynamic_qr`（动态二维码，票面左上/动态）。
- **基本项（17 项）**：动态二维码、发票号码、开票日期、购买方信息、销售方信息、项目名称、规格型号、单位、数量、单价、金额、税率/征收率、税额、合计、价税合计（大写、小写）、备注、开票人。
- **特定内容（按业务标签）**：建筑服务 / 不动产 / 旅客运输 / 货物运输 / 差额征税 / 农产品收购 / 机动车 / 二手车 / 代收车船税等，见 §5.5。

### 5.2 机动车销售统一发票（国标版式）
```
MotorVehicleInvoice
├── header: invoice_code(12位) | invoice_no(8位) | issue_date | machine_no(税控码/机器编号)
├── buyer: name | id_no(身份证号/组织机构代码) | taxpayer_id
├── vehicle: vehicle_type(车辆类型) | brand_model(厂牌型号) | origin(产地)
│           | cert_no(合格证号) | import_cert_no(进口证明书号)
│           | engine_no(发动机号) | vin(车辆识别代号/车架号)
├── price: total_incl_tax_capital(价税合计大写) | total_incl_tax_figures(价税合计小写)
├── seller: name | taxpayer_id | address_phone | bank_account
├── tax: vat_rate(增值税税率/征收率) | vat_amount(增值税税额)
├── authority: tax_authority(主管税务机关) | tax_authority_code(主管税务机关代码)
├── remarks | verify_url(查验URL)
└── signatures: payee | reviewer | issuer
```

### 5.3 二手车销售统一发票（国标版式）
```
UsedCarInvoice
├── header: invoice_code(12位) | invoice_no(8位) | issue_date | machine_no
├── buyer: name | id_no | taxpayer_id        # 买方单位/个人
├── seller: name | id_no | taxpayer_id       # 卖方单位/个人
├── vehicle: vehicle_type | brand_model | vin(车架号)
│           | plate_no(该车车牌照) | transfer_authority(转入地车辆管理所名称)
├── price: total_capital(车价合计大写) | total_figures(车价合计小写)
├── agent: name(经营/拍卖单位) | taxpayer_id | address_phone | bank_account
├── tax: vat_rate | vat_amount | late_fee(滞纳金)
├── remarks | verify_url
└── signatures: payee | reviewer | issuer
```

### 5.4 红字发票（红冲，主 schema + 红字扩展）
- **新增**：`red_info { original_code(原发票代码) | original_no(原发票号码) | red_reason(红冲原因) }`。
- **金额/税额全部为负**（或备注「红字」）；常关联《开具红字增值税专用发票信息表》。
- 其余字段同主 schema（专票/普票），仅符号相反。

### 5.5 特定要素（GB/T 42965.2-2023，附加到 `remarks` 或 `specific` 扩展节点）
| 特定业务 | 关键附加字段 |
|---|---|
| 建筑服务 | 建筑服务发生地（省/市/区+详细）、建筑项目名称、土地增值税项目编号、跨地（市）标志 |
| 不动产销售/租赁 | 不动产地址、不动产名称、面积单位 |
| 旅客运输 | 车次/航班号、出发地、到达地、出行日期、等级 |
| 货物运输 | 起运地、到达地、运输工具种类、运输货物名称、车种车号、金额 |
| 差额征税 | 扣除额（价款与计税差额） |
| 农产品收购 / 自产农产品销售 | 收购/销售标识、免税标识 |
| 代收车船税 | 车牌号、税款所属期、代收车船税金额 |

## 6. 扩展提示
- 主 schema 已对齐 **GB/T 42965.1-2023 基本要素**；数电票入账归档见 **GB/T 44554.5-2025**，增值税电子发票见 **GB/T 44554.2-2025**。
- 机动车/二手车为独立子结构（§5.2/§5.3），与专票 8 列明细不同，需单独渲染版式。
- 红字发票见 §5.4；特定行业字段见 §5.5，建议放入 `specific` 节点而非污染主字段。
- 抽取器实现时应按 `header.invoice_type` 分派子结构与对应 HTML 版式（见配套 `scripts/invoice_render.py`）。
