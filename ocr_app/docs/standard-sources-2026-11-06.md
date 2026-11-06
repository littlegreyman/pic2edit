# 标准原件台账（2026-11-06 官网实测）

> 用途：schema 生成器（`schema_gen`）的 S1/S2 输入来源登记。规格 §6.1 要求
> 「引用标准时给出官网 URL + 标准号 + sha256；来源不明的附件只作线索，不作论据」。
> 原件本身**不提交仓库**；代码里只钉 URL 与 sha256（`schema_gen/sources.py::SOURCES`），
> 下载后缓存在 `~/.pic2edit/standard_sources/`（与注册表用户目录同级）。

## 1. 通知页与标准号

| 项 | 值 |
|---|---|
| 通知页 | `https://www.cicpa.org.cn/xxfb/tzgg/202610/t20261010_66113.html` |
| 发布 | 中国注册会计师协会，2026-09-29 发布 / 2026-10-10 印发 |
| 标准号（固定资产分册） | `T/CICPA 0110—2026` |
| 标准名 | 《注册会计师审计数据规范 固定资产》 |
| 附件直链前缀 | `https://www.cicpa.org.cn/xxfb/tzgg/202610/W020261010587232` |

连通性实测（2026-11-06，本沙箱）：通知页与 8 个附件 HEAD 全部 `200`，
0.05–0.14s；`Server: wswaf`、`Accept-Ranges: bytes`。DNS 解析到 `198.18.0.22`
（环境透明代理接管），**无需 SOCKS 代理**；`127.0.0.1:1080` 在本沙箱未监听。
Python `urllib.request` HEAD 同样 `200`（45ms），生成器因此用标准库下载、零新增依赖。

## 2. 已实下载并钉 hash 的三份原件

| key | 通知页编号 | 文件 | 字节 | sha256 |
|---|---|---|---|---|
| `xsd` | 附件 7「文件输出格式」 | `W020261010587232730153.zip` | 78,266 | `d39b73781b19b172aedfb2b36728ec4039e6e7b4b473cf00940119b64b6689b7` |
| `examples` | 附件 8「实例」 | `W020261010587232734563.zip` | 50,404 | `8f65fa2f14ee82689cd6563968dbab718002e8584b532d69892e6fdc0371db04` |
| `ppe_pdf` | 附件 2「固定资产」 | `W020261010587232603870.pdf` | 667,246 | `e6d868517b6022f83a291be8e4e5fa20c593958311719c55b1a5f9dba62776d4` |

三份的字节数都与响应头 `Content-Length` 逐一相符，魔数正确（`PK\x03\x04` / `%PDF`），
`zipfile.testzip()` 返回 `None`；固定资产 PDF 共 144 页，首页文字为
`T/CICPA 0110—2026 注册会计师审计数据规范 固定资产`。

**校验失败的处理**：`sources.fetch()` 钉死以上 sha256，不匹配时保留
`<dest>.bad-<hash前8>` 副本并抛 `SourceError`，**不重试、不覆盖、不换镜像**。
官网若更新附件，须人工复核后更新本台账与 `SOURCES`；复核前不得使用。

## 3. 两套附件编号（易错，务必区分）

| 通知页 | zip 内目录名（标准正文编号） | 内容 |
|---|---|---|
| 附件 7 | `附件5：文件输出格式/` | 6 个 `.xsd` + `说明.txt` |
| 附件 8 | `附件6：实例/` | 6 个 XML 实例 + `说明.txt` |

两者**不是同一套编号**，文档与 provenance 里同时记录（见 `sources.Source` 的
`notice_attachment` / `inner_attachment`）。

其它编码细节：

- zip 内中文名**无 UTF-8 flag**（`flag_bits & 0x800 == 0`），是 GBK；
  `zipfile` 默认按 cp437 解码会得到乱码，须
  `name.encode("cp437").decode("gbk")` 还原（`sources.decoded_name`）。
- 实例包里固定资产文件名是 `PPE.XML`（**大写扩展名**），其它 5 个是小写
  `.xml`。成员匹配 MUST 大小写不敏感。
- zip 成员只在内存读取、不落盘；仍拒绝 `..` / 绝对路径 / 盘符 / 符号链接。

## 4. 其余五份分册（仅 HEAD 探测，未下载、未钉 hash）

这五份不是本次固定资产生成器的输入，登记在此供后续分册立项时按需下载。
**本次未实下载，因此没有 sha256，不得写进 `SOURCES` 当作已校验原件。**

| 通知页编号 | 标准分册 | 文件（`W020261010587232…`） | Content-Length |
|---|---|---|---|
| 附件 1 | 存货 | `575793.pdf` | 652,564 |
| 附件 3 | 员工薪酬 | `624047.pdf` | 398,838 |
| 附件 4 | 资金及借款 | `642003.pdf` | 1,153,182 |
| 附件 5 | 税项 | `675957.pdf` | 1,373,034 |
| 附件 6 | 租赁 | `701073.pdf` | 1,182,929 |

XSD 包内对应的分册文件：`INVENTORY.xsd`、`PAYROLL.xsd`、`FUNDSBORROWINGS.xsd`、
`TAX.xsd`、`LEASE.xsd`（实例包同名 `.xml`）。

## 5. `PPE.xsd` 的解析口径（2026-11-06 实解析）

`xml.etree.ElementTree` 解析结果：

| 计数 | 值 | 说明 |
|---|---|---|
| `xs:element` **总数** | **361** | 含顶层实体与根容器 `ref`，**不是字段数** |
| 顶层实体 element | 10 | 1 个根容器 `PPE` + 9 个实体定义 |
| 根容器里的 `ref` | 9 | 每个 `maxOccurs="unbounded"`，故 9 个实体都是重复实体 |
| **字段元素** | **342** | 9 个实体的 `complexType/sequence` 下的具名字段 |
| `xs:complexType` | 10 | 全部内联，无命名 complexType |
| `xs:simpleType` | 297 | 枚举与类型约束，**不是字段** |

恒等式：`361 = 10 + 9 + 342`。

> 立项前的连通性报告曾把 361 误判为「与计划里的 342 对不上」，P1 真解析时纠正：
> 两者是不同口径，计划初稿的「9 实体 / 342 字段」是对的。规格 §6.1 已写明口径。
> 字段数仍 MUST 由解析器给出、不得硬编码（其它分册字段数不同）。

9 个实体的字段数：`PPE_Type` 3、`PPE_Master` 67、`PPE_Addition` 41、
`PPE_Removal` 51、`PPE_Change` 40、`PPE_Department_Allocation` 22、
`PPE_Depreciation_Method` 25、`PPE_Depreciation` 46、`PPE_Physical_Count` 47。

字段属性样例（`PPE_Master/PPE_ID`）：`nillable="false"`、
`xs:documentation` =「固定资产的唯一标识符。通常由系统自动生成。」、
`xs:restriction base="xs:string"` + `maxLength=100`。
`nillable` 是数据模型的可空性，**MUST NOT** 直接照搬成 `required`（规格 §6.3）：
标准说「不可为空」不等于票面上一定印着这个字段。

## 6. 复核办法

```bash
cd ocr_app
.venv/bin/python3 -c "
from schema_gen import sources
print(sources.fetch('xsd'))        # 联网下载并校验，或命中已校验缓存
print(sources.list_members('xsd'))
"
sha256sum ~/.pic2edit/standard_sources/xsd-*/file-output-format.zip
```

单项测试全部不联网（`test_schema_gen_input.py` 用内置最小 XSD 夹具 + 假响应）；
联网验证只在人工真跑时执行，避免 CI 因官网波动假红。
