"""pic2edit-ocr skill 层回归自测（不依赖真实图片，纯内存数据）。

覆盖本轮重构的关键不变式：
  1. 重复护栏：只截尾部循环，正常文本零误伤；
  2. schema 渲染：缺字段必须是「（未识别）」，不能是破折号或空白；
  3. 明细列对齐：某行缺列时**绝不左移**（错位数据比缺失更危险）；
  4. 设置读取：默认值可读，日志目录落在 skill 内。

运行：<venv>/python _selftest.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pic2edit_convert as P
import settings_store
from invoice_render import _empty, invoice_title_text

FAIL = 0


def check(name, cond, detail=""):
    """带统计的断言：全部跑完再汇总，避免第一个失败就看不到后面的结果。"""
    global FAIL
    if cond:
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def box(text, x0, y0, x1, y1):
    """构造一个 RapidOCR 行框（与 pic2edit 的 _raw 输出同构）。"""
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


# ---------------------------------------------------------------------------
print("=== 1. 重复护栏 _trim_repetition ===")
t1 = "购买方 名称 某某公司 纳税人识别号 123456"
o1, r1 = P._trim_repetition(t1)
check("正常文本零误伤", r1 == 0 and o1 == t1)

unit = "货物 100.00 13% 13.00 "
o2, r2 = P._trim_repetition(unit * 8)
check("干净尾部循环被截断", r2 >= 3 and "已自动截断" in o2, f"reps={r2}")

o3, r3 = P._trim_repetition("真实前缀文字 ABC123。\n" + (unit * 200))
check("截断保留真实前缀", r3 >= 3 and "真实前缀文字" in o3 and o3.count("货物") <= 2)

pair = "重庆增值税专用发票  \n重庆增值税普通发票  \n"
o4, r4 = P._trim_repetition("真实前缀。\n" + pair * 200)
check("交替抖动循环被截断", r4 >= 3 and o4.count("重庆增值税专用发票") <= 2, f"reps={r4}")

cf = os.path.join(tempfile.gettempdir(), "pic2edit_cache",
                  "f3af5c6c120014e1150cf98ec20c3e1a8c839bf72dbf653f3c9c64cec0893bc5.json")
if os.path.isfile(cf):
    rt = json.load(open(cf, encoding="utf-8"))["pages"][0]["raw_text"]
    o5, r5 = P._trim_repetition(rt)
    check("真实重庆样本（行级抖动+半行截断）", r5 >= 3 and o5.count("重庆增值税专用发票") <= 3,
          f"reps={r5}")
else:
    print("  [SKIP] 真实重庆缓存样本不存在")

# ---------------------------------------------------------------------------
print("=== 2. 「（未识别）」占位 ===")
check("HTML 空值占位", "（未识别）" in _empty(None) and "—" not in _empty(None))
check("HTML 有值不加占位", "（未识别）" not in _empty("示例"))
check("标题：代码推断省份", invoice_title_text({"invoice_type": "增值税专用发票",
                                              "invoice_code": "3400000000"}) == "安徽增值税专用发票")
check("标题：已含省份不重复前缀", invoice_title_text({"invoice_type": "重庆增值税专用发票",
                                                  "invoice_code": "4200000000"}) == "重庆增值税专用发票")

doc_empty = {"header": {}, "buyer": {}, "seller": {}, "items": [],
             "totals": {}, "total_incl_tax": {}, "remarks": "", "signatures": {}}
md = P._render_md_schema(doc_empty)
check("schema md 全缺字段仍可渲染", md.count("（未识别）") >= 12, f"count={md.count('（未识别）')}")
check("schema md 不含破折号占位", "| — |" not in md)

doc_full = {
    "header": {"invoice_type": "增值税专用发票", "invoice_code": "3400000000"},
    "buyer": {"name": "示例电机有限公司", "taxpayer_id": "91000000000000001X"},
    "seller": {"name": "示例贸易有限公司"}, "items": [],
    "totals": {"total_excl_tax": "94578.64", "total_tax": "16078.36"},
    "total_incl_tax": {"capital": "壹拾壹万", "figures": "110657.00"},
    "remarks": "", "signatures": {},
}
md2 = P._render_md_schema(doc_full)
check("schema md 命中省份标题", md2.startswith("# 安徽增值税专用发票"))
check("schema md 保留已识别值", "94578.64" in md2 and "示例电机有限公司" in md2)

# ---------------------------------------------------------------------------
print("=== 3. 明细列对齐（缺列不得左移）===")
# 构造一张迷你发票：表头有「单位」列，但两行明细都没有单位值 —— 这正是历史上
# 让"单价填进数量、税额填进税率"的坑。断言数量仍落在 quantity、单位保持空缺。
L = [
    box("货物或应税劳务、服务名称", 100, 100, 400, 120),
    box("规格型号", 500, 100, 600, 120),
    box("单位", 700, 100, 760, 120),
    # 第 1 行（无单位）
    box("品名A", 100, 140, 200, 160),
    box("S1", 500, 140, 560, 160),
    box("10", 800, 140, 860, 160),
    box("5.5", 900, 140, 960, 160),
    box("55.00", 1000, 140, 1060, 160),
    box("12%", 1100, 140, 1140, 160),
    box("6.60", 1200, 140, 1260, 160),
    # 第 2 行（无单位）
    box("品名B", 100, 175, 200, 195),
    box("S2", 500, 175, 560, 195),
    box("20", 800, 175, 860, 195),
    box("5.5", 900, 175, 960, 195),
    box("110.00", 1000, 175, 1060, 195),
    box("12%", 1100, 175, 1140, 195),
    box("13.20", 1200, 175, 1260, 195),
    # 汇总行（必须被排除出明细）
    box("￥165.00", 1000, 215, 1060, 235),
    box("￥19.80", 1200, 215, 1260, 235),
    box("价税合计（大写）", 100, 260, 300, 280),
    box("壹佰捌拾肆圆整", 400, 260, 600, 280),
    box("（小写）184.80", 1000, 260, 1100, 280),
]
items = P._extract_items_from_lines(L, 1400, 400)
check("明细行数=2（汇总行被排除）", len(items) == 2, f"len={len(items)}")
if len(items) == 2:
    a = items[0]
    check("品名落在 name 列", a.get("name") == "品名A", a)
    check("规格落在 spec 列", a.get("spec") == "S1", a)
    check("数量没有被单价挤占", a.get("quantity") == "10", a)
    check("单价列正确", a.get("unit_price") == "5.5", a)
    check("金额列正确", a.get("amount") == "55.00", a)
    check("税率列正确", a.get("tax_rate") == "12%", a)
    check("税额列正确", a.get("tax_amount") == "6.60", a)
    check("单位列缺值即空缺（不左移）", "unit" not in a, a)

tot = P._extract_totals_geom(L, 400)
check("合计金额/税额从坐标取到",
      tot.get("total_excl_tax") == "165.00" and tot.get("total_tax") == "19.80", tot)
check("价税合计大写取到", (tot.get("capital") or "").startswith("壹佰捌拾肆"), tot)
check("价税合计小写取到", tot.get("figures") == "184.80", tot)

# ---------------------------------------------------------------------------
print("=== 4. 字段可信度守卫（宁可留空，不可错填）===")
# 5.1 合并表头：`单位数量` 必须被识别为表头，否则会吞掉首行明细的税额
check("合并表头 单位数量 被识别", P._is_item_header_cell("单位数量"))
check("合并表头 金额税额 被识别", P._is_item_header_cell("金额税额"))
check("表头关键字本身被识别", P._is_item_header_cell("规格型号") and P._is_item_header_cell("税额"))
check("真实品名不误判为表头",
      not P._is_item_header_cell("碳结圆") and not P._is_item_header_cell("中90"))
check("整行表头被识别", P._is_item_header_cell("货物或应税劳务、服务名称"))

# 5.2 占位符不是值：`称：#` 不能把 `#` 当名称
check("占位符 # 判为 junk", P._is_junk_value("#") and P._is_junk_value("＊"))
check("空串判为 junk", P._is_junk_value("") and P._is_junk_value("   "))
check("标签串判为 junk（以冒号结尾）", P._is_junk_value("纳税人识别号："))
check("真实值不判 junk", not P._is_junk_value("示例电机有限公司"))

# 5.3 签章栏是人名：纯数字/含「章」的印刷注记都要拒
check("签章纯数字被拒", not P._seal_ok("998668868900"))
check("签章含「章」被拒", not P._seal_ok("发票专用单（章）开票员"))
check("签章真实姓名通过", P._seal_ok("张三") and P._seal_ok("李四"))

# 5.4 税号形态守卫：抽取层抽到公司名时不得写进税号栏
L_tax = [
    box("纳税人识别号：", 100, 100, 300, 120),
    box("91000000000000002H", 400, 100, 700, 120),
]
g_ok = P._extract_party_geom(L_tax, 0, 300, 1000)
check("合法税号被保留", g_ok.get("taxpayer_id") == "91000000000000002H", g_ok)

L_bad = [
    box("纳税人识别号：", 100, 100, 300, 120),
    box("靖江市国家税务局第四税务分局", 400, 100, 900, 120),
]
g_bad = P._extract_party_geom(L_bad, 0, 300, 1000)
check("非税号形态被丢弃（不拿公司名当税号）", "taxpayer_id" not in g_bad, g_bad)

# 5.5 地址/账号拆分
check("地址电话按尾部号码拆分",
      P._split_addr_phone("某某省某某市某某路 0000-0000001") == ("某某省某某市某某路", "0000-0000001"))
check("无电话时整段归地址且不臆造",
      P._split_addr_phone("某某省某某市某某路162号") == ("某某省某某市某某路162号", ""))
check("开户行与账号按尾部长数字拆分",
      P._split_bank("示例银行城北支行12345678901234567890")
      == ("示例银行城北支行", "12345678901234567890"))

# 5.6 大写金额是封闭字符集：形近误识要归一，认不出的汉字原样保留
check("大写金额形近归一（柴→柒）",
      P._norm_upper_amount("壹拾壹万零陆佰伍拾柴圆整") == "壹拾壹万零陆佰伍拾柒圆整")
check("大写金额去非汉字噪声", P._norm_upper_amount("?叁仟贰佰柒拾陆圈整") == "叁仟贰佰柒拾陆圆整")
check("大写金额不改写认不出的汉字",
      P._norm_upper_amount("伍仟成佰圆整") == "伍仟成佰圆整")

# 5.7 合计行不能被「（小写）￥xxx」抢位（带货币符号的小写行也常在合计行下方）
L2 = list(L[:16]) + [
    box("￥165.00", 1000, 215, 1060, 235),
    box("￥19.80", 1200, 215, 1260, 235),
    box("价税合计（大写）", 100, 260, 300, 280),
    box("壹佰捌拾肆圆整", 400, 260, 600, 280),
    box("（小写）￥184.80", 1000, 260, 1100, 280),
]
tot2 = P._extract_totals_geom(L2, 400)
check("带￥的小写行不抢合计行位置",
      tot2.get("total_excl_tax") == "165.00" and tot2.get("total_tax") == "19.80", tot2)

# ---------------------------------------------------------------------------
print("=== 5b. 无区号电话 / 形近标签 / 票面空白三态 ===")

# 6.1 OCR 丢掉区号后只剩本地号：这是样本里 6/16 张丢电话的真正原因
check("无区号尾部 8 位号码被拆为电话",
      P._split_addr_phone("某某区某某路500号1幢101室50000001")
      == ("某某区某某路500号1幢101室", "50000001"))
check("地址本身以字母数字结尾时仍能拆电话",
      P._split_addr_phone("某某市某某区某某路8号某某园区17号楼二居A2010-50000002")
      == ("某某市某某区某某路8号某某园区17号楼二居A2010", "50000002"))
check("连写长号不被腰斩（12 位不切成 07 + 8 位）",
      P._split_addr_phone("某某省某某市某某区某某村某某路073100000000")[1] == "073100000000")
check("纯中文地址不凭空造电话",
      P._split_addr_phone("东莞市大朗镇大角岭工业区") == ("东莞市大朗镇大角岭工业区", ""))

# 6.2 价税合计小写：标签被截断 + ￥ 被识成 羊/手/?
for _raw in ("（小写）羊1250.00", "（小写）手75200.00", "(小写)?67.00", "(小)Y4780.00"):
    check(f"小写金额可从形近噪声中取出：{_raw}", P._clean_amount(_raw) != "")
check("「(小)」被认作小写标签", P._is_figures_label("(小)Y4780.00"))
check("「小型」不被误认为小写标签", not P._is_figures_label("小型微利企业"))
check("两值粘连仍拒绝", P._clean_amount("X4.640.77") == "")

# 6.3 校验码：标签形近误识（校绘码）+ 无冒号连写
check("校绘码被认作校验码", P._extract_check_code([], "校绘码：60000000000000000001")
      == "60000000000000000001")
check("校验码无冒号连写可取", P._extract_check_code([], "校验码60000000000000000002")
      == "60000000000000000002")
check("无数字时不臆造校验码", P._extract_check_code([], "校验码") == "")

# 6.4 明细表头形近：`单住` 要归一回 `单位`，否则单位列整体丢失
check("单住归一回单位（表头单元格）", P._is_item_header_cell("单住"))
check("数置归一回数量", P._nhk("数置") == "数量")
check("真实品名不被表头归一污染", P._nhk("碳结圆") == "碳结圆")

# 6.5 三态渲染：未识别 / 票面空白 必须可区分
_md_sig = P._render_md_schema({
    "header": {"invoice_type": "增值税专用发票"},
    "signatures": {}, "remarks": "",
    "meta": {"blank": ["remarks", "signatures.payee"]},
})
check("票面空白标记出现", P._BLANK in _md_sig)
check("未识别标记仍在（复核栏未标空白）", P._MISS in _md_sig)
check("两种标记不是同一个串", P._BLANK != P._MISS)

# 6.6 电子发票不该凭空多出「联次/机器编号/校验码」三栏
_rows_e = P._pick_header_rows({"invoice_no": "24320000000000000001"}, "电子发票", "e_invoice")
check("电子发票不含联次栏", all(r["label"] != "联次" for r in _rows_e), _rows_e)
check("电子发票不含校验码栏", all(r["label"] != "校验码" for r in _rows_e), _rows_e)
_rows_p = P._pick_header_rows({"invoice_no": "10000002"}, "校验码 机器编号 第三联", "vat_general")
check("纸质普票保留校验码栏", any(r["label"] == "校验码" for r in _rows_p), _rows_p)

# ---------------------------------------------------------------------------
print("=== 5. 设置读取 ===")
s = settings_store.load(force=True)
check("默认保留 10 天", s["log"]["retention_days"] == 10, s["log"])
check("日志目录在 skill 内",
      os.path.abspath(s["log"]["dir"] if os.path.isabs(s["log"]["dir"])
                      else os.path.join(settings_store.SKILL_ROOT, s["log"]["dir"])
                      ).startswith(settings_store.SKILL_ROOT))
check("模型档位取值合法", s["vlm"]["model"] in ("auto", "q8", "q4", "bf16", "cpu"), s["vlm"])

print()
if FAIL:
    print(f"FAILED: {FAIL} 项")
    sys.exit(1)
print("ALL TESTS PASSED")
