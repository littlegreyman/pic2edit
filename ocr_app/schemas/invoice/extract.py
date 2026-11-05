# -*- coding: utf-8 -*-
"""发票抽取层（发票专用，纯 Python + 坐标，无 Qt 依赖）。

本模块由 skill 转换脚本机械搬移而来（Phase 3.2.1，逐字保留、行为零变化）：
原先发票 schema 的抽取/渲染**内嵌在转换流程**里，导致
  ① 转换任意文档都要先跑一遍「这是不是发票」的判定；
  ② 新增一种凭证必须改消费侧代码并重新发版。

搬出后，它可以被 GUI / Skill / 未来的声明式运行时**按需调用**，
转换流程本身不再知道发票的存在（见 schemas/registry.py 与 schemas/base.py）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .html_layout import PROVINCE_NAMES


# --------------------------------------------------------------------------
# 键名归一化：不同时期发票版本/底纹会导致近义键名（如“开票日期”vs“开票时间”、
# “货物或应税劳务、服务名称”vs“货物或服务名称”），语义无歧义应视为相同并提取。
# --------------------------------------------------------------------------
def _norm_key(s):
    """去空白与常见标点/间隔，便于近义键名匹配。"""
    s = (s or "").replace(" ", "").replace("\u00a0", "")
    for p in "、，,。.:：·—_~（）()[]【】%":
        s = s.replace(p, "")
    return s


def _is_item_header(text):
    """判断一行是否为货物明细表头。

    三种措辞都要认（同一套 schema 要通吃各省版式）：
      1. 老版专用发票的整句：`货物或应税劳务、服务名称`
      2. 新版/电子发票的简写：`项目名称`、`名称`
      3. 表头被拆成一排多框时，任一框命中 ≥3 个列名关键字

    前两条之外必须有兜底：电子发票的表头措辞改成"项目名称/规格型号/单位/…"后，
    只认"货物"会把整张票判成"无明细表"，明细直接为空——样本里 7/16 张就是这么丢的。
    """
    n = _norm_key(text)
    if not n or len(n) > 24 or "：" in n or ":" in n:
        return False
    # 「应税劳务」是发票明细表头独有的措辞（业务数据里不会出现），
    # 而它的前半截极容易被读歪——北京票读成「简物戒应税劳务名你」。
    # 只要出现这个词就足以判定是明细表头，不必强求「货物」二字也认对。
    if "应税劳务" in n:
        return True
    if ("货物" in n) and ("服务" in n or "名称" in n):
        return True
    if n in ("项目名称", "名称", "服务名称", "货物名称"):
        return True
    hits = sum(1 for k in ("名称", "规格", "型号", "单位", "数量", "单价",
                           "金额", "税率", "征收率", "税额", "项目") if k in n)
    return hits >= 3


# 明细表头可能出现的单元格词。合并表头（"单位数量"/"金额税额"）是这些词的任意拼接，
# 故判据用"全部由表头词拼成"，而不是逐个枚举。
_HDR_WORD = r"(?:规格型号|规格|型号|单位|数量|单价|金额|税率|征收率|税额|名称|项目)"

# 表头形近误识归一：`单住`（位→住）、`数置`（量→置）在小字号票面上很常见。
# 只在**明细表头**语境里用（_nhk），不污染通用的 _norm_key。
_HDR_ALIAS = str.maketrans({"住": "位", "伩": "位", "値": "位", "置": "量", "宣": "单"})


def _nhk(s):
    """明细表头专用归一：把形近误识拉回规范列名（``单住`` → ``单位``）。"""
    return _norm_key(s).translate(_HDR_ALIAS)


def _is_item_header_cell(text):
    """判断一个框是否为明细表头的单元格（含合并表头）。

    为什么必须识别合并表头：`单位数量` 这类合并框在 y 上比表头行心更低，会抢到行聚类的
    主导位置，把首行明细的税额吞进"表头 band"而被 `_itemish` 过滤掉——本样本首行税额
    ``3186.07`` 就是这样丢的。判据取"全部由表头关键字拼接而成"，任何拼接组合都能命中，
    而 `碳结圆`/`中90` 这类真实值不会被误判。
    """
    n = _nhk(text)
    return _is_item_header(text) or re.fullmatch(_HDR_WORD + r"+", n) is not None


_ITEM_COL_WORDS = (
    ("spec", ("规格型号", "规格", "型号")),
    ("unit", ("单位",)),
    ("quantity", ("数量",)),
    ("unit_price", ("单价",)),
    ("amount", ("金额",)),
    ("tax_rate", ("税率", "征收率")),
    ("tax_amount", ("税额",)),
)


def _detect_item_columns(lines, H):
    """探测明细表**票面实际印了哪几列**（用于渲染时裁掉"结构上不存在"的列）。

    代开版式/部分老版普票根本没有「单位」列，全量输出 8 列会给每行都塞一个
    「（未识别）」，把"票面没这栏"和"没识别出来"混成一团。明细表头所在的
    一条横带上逐框识别列名，命中即为该列存在。

    Args:
        lines: 行框列表。
        H: 页高。

    Returns:
        列键列表（按票面从左到右排序）；找不到表头时返回 ``[]``（调用方回退到全 8 列）。
    """
    hdr = next((ln for ln in lines or [] if _is_item_header(ln["text"])), None)
    if hdr is None:
        return []
    tol = max(8.0, 0.02 * H)
    cols = {}
    for ln in lines:
        if abs(_cy(ln) - _cy(hdr)) > tol:
            continue
        n = _nhk(ln["text"])
        if not n or len(n) > 16:
            continue
        for key, words in _ITEM_COL_WORDS:
            if key in cols:
                continue
            if any(w in n for w in words):
                cols[key] = ln["x0"]
                break
    if not cols:
        return []
    order = sorted(cols.items(), key=lambda kv: kv[1])
    return ["name"] + [k for k, _x in order if k != "name"]


# ----------------------------- 发票结构解析 -----------------------------
# 计划单列市：票面标题直接印市名（"宁波增值税电子普通发票"），而 PROVINCE_NAMES
# 只有省名，导致这些票的标题前缀补不出来。只用于标题前缀匹配，不进行政区划码表。
PLAN_CITIES = ("宁波", "厦门", "大连", "青岛", "深圳")


def _split_addr_phone(ap):
    """把「地址电话」串拆成 (地址, 电话)。拆不开时整段归地址，绝不凭空造电话号。

    两种印刷形态都要覆盖：带「电话」标签的（地址、电话：xx 电话：yy），
    以及不带标签、电话直接跟在地址后面的（某某省某某市某某路 0000-0000001）。
    """
    ap = (ap or "").strip()
    if not ap:
        return "", ""
    m = re.search(r"(.*?)(?:电\s*话)\D{0,4}(\S+)$", ap)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # 无标签形态：尾部 区号+号码（区号 0 开头 3~4 位，号码 7~8 位，允许连字符/空格）
    m = re.search(r"^(.*?[\u4e00-\u9fff）)])\s*(\(?0\d{2,3}\)?[-\s]?\d{7,8}(?:[-\s]?\d{1,4})?)\s*$", ap)
    if m and len(re.findall(r"[\u4e00-\u9fff]", m.group(1))) >= 2:
        return m.group(1).strip(), m.group(2).strip()
    # 无区号形态：OCR 经常把 `021-50000001` 的区号段整块丢掉（低分辨率票面上「021-」
    # 只有三四个极窄字符），只剩 `50000001` 挂在地址末尾。样本里 6/16 张都是这样丢电话的。
    # 判据取「尾部 7~8 位纯数字，且其前**紧邻一个非数字字符**」：这样
    # `...1161室50000001`、`...A2010-50000002` 都能拆出电话，而连写的
    # `073100000000`（12 位，前面是汉字、中间没有非数字）不会被切掉前 4 位。
    m = re.search(r"^(.*?\D)(\d{7,8})$", ap)
    if m:
        _head = m.group(1).rstrip(" \t-—，,、")
        if len(re.findall(r"[\u4e00-\u9fff]", _head)) >= 2 and len(_head) >= 4:
            return _head, m.group(2)
    return ap, ""


def _split_bank(bank):
    """把「开户行及账号」串拆成 (开户行, 账号)。拆不开时整段归开户行。

    账号是纯数字长串（≥10 位），开户行是中文行名，两者拼接时没有分隔符很常见，
    故用"尾部连续数字"作为切点；切出的账号必须够长，避免把行名里的门牌号当账号。
    """
    bank = (bank or "").strip()
    if not bank:
        return "", ""
    m = re.search(r"(.*?)(?:账\s*号|账号|帐号)\D{0,4}(\S+)$", bank)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.search(r"^(.*?[\u4e00-\u9fff）)])\s*(\d{10,32})\s*$", bank)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return bank, ""


_JUNK_VALUE_RE = re.compile(r"^[#＃*＊※·•\-—_~\.。,，、:：|\s]+$")


def _is_junk_value(v):
    """判断抽到的「值」是不是占位符号而不是真实内容。

    发票上会出现「称：#」这种印刷占位/花字残留。把 `#` 当字段值输出，比留空更具误导性——
    下游会认为这是一个有效字段。真实值至少含 1 个字母/数字/汉字。
    """
    v = (v or "").strip()
    if not v:
        return True
    if _JUNK_VALUE_RE.match(v):
        return True
    # 以冒号结尾/含冒号的串是「标签」而不是「值」——坐标法偶尔会把右侧的另一个标签抓过来
    if v.endswith(("：", ":")) or re.search(r"[:：]", v):
        return True
    return re.search(r"[0-9A-Za-z\u4e00-\u9fff]", v) is None


def _label_value(ln, band, tol):
    """取一个「标签行」对应的值：优先取标签自带的冒号后内容，否则取同排右侧最近的文本块。

    OCR 的中文发票里，标签与值有时在同一框（"称：示例电机有限公司"），有时分属两框
    且 x 相邻。两种形态都要覆盖，否则会像旧实现那样整片丢字段。
    """
    t = ln["text"]
    m = re.search(r"[:：]\s*(.+)$", t)
    # 冒号后是 `#`/`*` 这类占位符时不能直接当值返回，要继续走坐标找右侧真实值
    if m and not _is_junk_value(m.group(1)):
        return m.group(1).strip()
    lx1, ly = ln["x1"], _cy(ln)
    cands = [b for b in band
             if b is not ln and b["x0"] >= lx1 - 8 and abs(_cy(b) - ly) <= tol]
    if not cands:
        return ""
    cands.sort(key=lambda b: b["x0"] - lx1)
    for b in cands:
        if not _is_junk_value(b["text"]):
            return b["text"].strip()
    return ""


def _name_candidate(band, ln_label, tol):
    """名称值被印成占位符时，在标签附近另找「像公司名」的框。

    本样本的销售方把公司名单独印在「称：#」这一行的**上一行**（x 还有轻微重叠），
    因此不能要求 x0 必须严格在标签右侧，改为按 (纵向距离, 横向偏移) 取最近的候选。
    只接受含 ≥4 个汉字、且不含其它字段标签词的框，避免把「纳税人识别号」的值抓来当名称。
    """
    skip = ("名称", "称", "识别号", "识号", "税号", "地址", "开户行",
            "购买方", "销售方", "密码区", "代开")
    best, best_key = "", None
    for b in band:
        if b is ln_label:
            continue
        t = (b["text"] or "").strip()
        if not t:
            continue
        n = _norm_key(t)
        if any(k in n for k in skip):
            continue
        if re.search(r"[:：]", t):  # 含冒号的是标签框，不是值
            continue
        if len(re.findall(r"[\u4e00-\u9fff]", t)) < 4:
            continue
        dy = abs(_cy(b) - _cy(ln_label))
        if dy > tol * 1.5:
            continue
        key = (dy, abs(b["x0"] - ln_label["x0"]))
        if best_key is None or key < best_key:
            best, best_key = t, key
    return best


def _clean_name(v):
    """把名称值清理成"只有公司名"：截断粘在后面的字段标签，并拒绝乱码。

    两件事都必须做：
      1. 截断——OCR 常把"南京某某公司统一社会信用代码/"并成一个框，名称里不能带尾巴；
      2. 拒乱码——密码区字符（``57<2*-1/227…``）没有汉字，被抽成公司名会直接污染
         知识库，这是"无幻觉入库"红线。公司名至少要有 2 个汉字且汉字占比 ≥ 30%。
    """
    v = (v or "").strip()
    if not v:
        return ""
    v = re.split(r"统一社会信用代码|纳税人识别号|纳税人识号|地址|开户行|电\s*话", v)[0].strip()
    v = v.rstrip("／/|｜").strip()
    if not v or _is_junk_value(v):
        return ""
    cjk = sum(1 for c in v if "\u4e00" <= c <= "\u9fff")
    if cjk < 2 or cjk / max(1, len(v)) < 0.3:
        return ""
    return v


def _has_cjk(v, at_least=2):
    """判断字符串是否含足够汉字（用于地址/开户行这类必须有中文的字段）。"""
    return sum(1 for c in (v or "") if "\u4e00" <= c <= "\u9fff") >= at_least


_ORG_SUFFIX_RE = re.compile(r"(公司|厂|商行|商店|中心|事务所|银行|集团|合作社|分店)$")
_PARTY_LABEL_WORDS = ("识别", "税号", "地址", "开户", "密码", "发票", "电话", "帐号", "账号")


def _guess_party_name(band):
    """「名称」标签被读歪时的兜底：按**公司名的形态**在当事人区块里找。

    北京票把「称：」读成「苏：」，四种标签写法全落空，销方名称整块丢失。
    但公司名本身有很强的形态特征——以 公司/厂/商行/中心 等结尾、至少 4 个汉字、
    不含任何其他字段标签词。满足这三点基本不可能是别的东西。

    Args:
        band: 当事人区块内的行框列表。

    Returns:
        公司名；找不到返回空串（宁缺勿错）。
    """
    best = ""
    for ln in sorted(band, key=_cy):
        t = (ln["text"] or "").strip()
        if not t:
            continue
        n = _norm_key(t)
        if any(w in n for w in _PARTY_LABEL_WORDS):
            continue
        # 允许 1~2 个字的标签前缀（`苏：xxx`），也允许整框就是公司名
        m = re.match(r"^.{1,2}[:：]\s*(.+)$", t)
        cand = _clean_name(m.group(1) if m else t)
        if cand and _ORG_SUFFIX_RE.search(cand) and cand not in best:
            best = cand
            break
    return best


def _extract_party_geom(lines, y_lo, y_hi, H):
    """用 OCR 坐标抽取购销方四要素（名称 / 纳税人识别号 / 地址电话 / 开户行账号）。

    比纯正则在粘连文本里瞎猜可靠得多：发票是固定版式，"标签在左、值在右同排"是硬约束。

    Args:
        lines: 行框列表。
        y_lo, y_hi: 纵向区间（购买方在明细表头之上，销售方在价税合计之下）。
        H: 页高，用于自适应容差。

    Returns:
        字段字典；区间内找不到任何标签时返回 ``{}``。
    """
    band = [ln for ln in lines if y_lo <= _cy(ln) <= y_hi]
    if not band:
        return {}
    # 纵向容差必须小于行距（约 30~36px），否则上一行的地址会被当成下一行税号的"右邻值"
    tol = max(14.0, 0.014 * H)

    def find(*keys):
        for ln in sorted(band, key=_cy):
            t = _norm_key(ln["text"])
            if any(k in t for k in keys):
                return ln
        return None

    def find_re(pat):
        """按正则找标签框——用于标签被读歪的情形（``纳税人识别骨``、``的税人认测号``）。"""
        for ln in sorted(band, key=_cy):
            if re.search(pat, _norm_key(ln["text"])):
                return ln
        return None

    out = {}
    ln_name = find("名称", "称")
    ln_tax = find("纳税人识别号", "内税人识别号", "纳税人税号") \
        or find_re(r"税人")
    ln_addr = find("地址") or find_re(r"地\s*址|地址")
    ln_bank = find("开户行") or find_re(r"开[户尸][行厅]")

    if ln_name is not None:
        v = _clean_name(_label_value(ln_name, band, tol))
        if not v:
            v = _clean_name(_name_candidate(band, ln_name, tol))
        if v:
            out["name"] = v
    if not out.get("name"):
        _g = _guess_party_name(band)
        if _g:
            out["name"] = _g
    if ln_tax is not None:
        v = _label_value(ln_tax, band, tol)
        if v:
            # 税号在定义上就是 15~20 位字母数字（15 位老税号 / 18 位统一社会信用代码）。
            # 抽到的串不满足这个形态时**宁可留空**：像江苏"代开"版式会把代开机关名称
            # 抽进税号栏，输出一个公司名当税号比留「（未识别）」危害大得多。
            digits = re.sub(r"[^0-9A-Z]", "", v.upper())
            if 15 <= len(digits) <= 20:
                out["taxpayer_id"] = digits
    if ln_addr is not None:
        v = _label_value(ln_addr, band, tol)
        if v:
            _a, _ph = _split_addr_phone(v)
            # 地址必须有中文（防密码区/税号串位），电话必须是号码形态
            if _has_cjk(_a):
                out["address"] = _a
            if _ph and re.search(r"\d", _ph):
                out["phone"] = _ph
    if ln_bank is not None:
        v = _label_value(ln_bank, band, tol)
        if v:
            _bn, _ba = _split_bank(v)
            if _has_cjk(_bn):
                out["bank_name"] = _bn
            if _ba and re.search(r"\d", _ba):
                out["bank_account"] = _ba
    # 地址被抽成了税号（"代开"版式常见）时清掉，避免同一串数字同时当两个字段
    if out.get("address") and out.get("taxpayer_id") \
            and re.sub(r"\s", "", out["address"]) == out["taxpayer_id"]:
        out["address"] = ""
    return out


def _extract_party(text, label_re):
    # 注意：label_re 是“购买方|购货单位|购货方”形式的多选，必须用括号整体分组，
    # 否则 (.*?) 只绑定到最后一个分支；匹配到“购买方”时 group(1) 会是 None。
    m = re.search("(" + label_re + r")(.*?)(?=销售方|销货|货物|合计|价税合计|$)",
                  text, re.S)
    block = m.group(2) if m else ""
    out = {}
    # 中文 OCR 无空格，字段值用「前瞻到下一字段标签」截断，避免整段粘连。
    # 键名容忍近义写法（如 纳税人税号、开户行及帐号）；分隔符冒号/空格均可。
    # 名称的「名」字常被 OCR 丢掉（只剩"称：xxx"），故"名"设为可选。
    specs = [
        ("name", r"(?:名\s*)?称[:：]?\s*(.*?)(?=(?:纳|内)税人识别号|(?:纳|内)税人税号|地址[、,，]?电话|开户行及账号|开户行及帐号|$)"),
        ("taxpayer_id", r"(?:纳|内)税人识别号[:：]?\s*(.*?)(?=地址[、,，]?电话|开户行及账号|开户行及帐号|$)"),
        ("addr_phone", r"地址[、,，]?电话[:：]?\s*(.*?)(?=开户行及账号|开户行及帐号|$)"),
        ("bank", r"开户行及[账帐]号[:：]?\s*(.*?)(?=收款人|复核|开票人|备注|$)"),
    ]
    for key, pat in specs:
        mm = re.search(pat, block, re.S)
        if mm:
            out[key] = mm.group(1).strip()
    # 拆分 地址、电话 / 开户行及账号（尽力而为；拆不开则整体归首字段，不臆造）
    ap = out.pop("addr_phone", "")
    if ap:
        out["address"], out["phone"] = _split_addr_phone(ap)
    bank = out.pop("bank", "")
    if bank:
        out["bank_name"], out["bank_account"] = _split_bank(bank)
    return out


def _cy(ln):
    """文本块的行中心 y（像素）。"""
    return (ln["y0"] + ln["y1"]) / 2.0


def _cluster_1d(vals, tol):
    """一维聚类：相邻差值 ≤ tol 归为一组，返回各组均值（用于推断列中心）。"""
    vals = sorted(vals)
    groups, cur = [], []
    for v in vals:
        if cur and v - cur[-1] > tol:
            groups.append(cur)
            cur = []
        cur.append(v)
    if cur:
        groups.append(cur)
    return [sum(g) / len(g) for g in groups]


_ITEM_KEYS = ["name", "spec", "unit", "quantity", "unit_price", "amount", "tax_rate", "tax_amount"]


def _extract_items_from_lines(lines, W, H):
    """从带坐标的 OCR 行框重建货物明细表（按表头锚点对齐 8 个 schema 列）。

    为什么不能像以前那样"行内按 x 排序后依次填 8 列"：一旦某行缺列（发票很常见——
    本样本的"单位"列就是空的），后续所有值会整体左移一格，把单价填进数量、把税额
    填进税率，产出"错得很有说服力"的数据。宁可留「（未识别）」，也不能错位。

    做法：
      1) 用表头行拿到 name / spec / unit 三个**锚点列的 x 中心**；
      2) 数据区所有单元格的 x 中心做一维聚类，得到实际存在的列中心；
      3) 锚点左侧的簇按 x 顺序填 name/spec；锚点右侧的簇按 x 顺序填
         quantity/unit_price/amount/tax_rate/tax_amount；
      4) 每行按"最近列中心 + 容差"落格，落不进的留空。

    Args:
        lines: RapidOCR 行框列表，每项含 text / x0 / y0 / x1 / y1。
        W, H: 页面宽高（像素），用于自适应容差。

    Returns:
        schema 列字典组成的行项列表；无法可靠对齐时返回 ``[]``（宁缺勿错）。
    """
    if not lines:
        return []
    hdr = next((ln for ln in lines if _is_item_header(ln["text"])), None)
    if hdr is None:
        return []

    # 明细行行距通常只有 20~30px，容差必须小于行距，否则相邻两行会被并成一行
    row_tol = max(6.0, 0.015 * H)
    hdr_cy = _cy(hdr)

    # 表头带的锚点列：name / spec / unit
    anchors = {}
    for ln in lines:
        if abs(_cy(ln) - hdr_cy) > row_tol:
            continue
        t = _nhk(ln["text"])
        cx = (ln["x0"] + ln["x1"]) / 2.0
        if "规格" in t:
            anchors.setdefault("spec", cx)
        elif "单位" in t:
            anchors.setdefault("unit", cx)
        elif _is_item_header(ln["text"]):
            anchors.setdefault("name", cx)

    # 数据区下界：价税合计行（其上的"合计金额/税额"行属于汇总，不是明细）
    end_cy = H
    for ln in lines:
        if _cy(ln) > hdr_cy and "价税合计" in _norm_key(ln["text"]):
            end_cy = min(end_cy, _cy(ln))
    if end_cy >= H:
        # 兜底：「价税合计」读歪时用「（大写）」定位。否则下界一路落到页底，
        # 销售方四要素（名称/税号/地址/开户行）会被当成明细行吞进表里。
        for ln in lines:
            if _cy(ln) > hdr_cy and "大写" in _norm_key(ln["text"]):
                end_cy = min(end_cy, _cy(ln))

    # 下界取表头框"上沿"：表头框往往比首行明细更高，用 hdr_cy+row_tol 会切掉首行。
    # 表头单元格按"文本形态"排除（含数字与否不可靠：品名列"碳结圆"就没有数字），
    # 合并表头（"单位数量"）也要排除，否则会吞掉首行明细，详见 _is_item_header_cell。
    data_top = hdr["y0"]
    # 表头常占**两行**（老版发票的「税率/税额」比「名称/规格」低半行，北京票就是这样），
    # 第二行表头会被当成首行明细，把「税山（税额）」读成品名。故把紧随表头
    # 1.6 个行距内的「2 字表头碎片」一并排除。判据同时要求首字属于列名用字，
    # 3 字以上的真实品名（如「印花税」）不受影响。
    _hdr_frag_top = hdr_cy + row_tol * 1.6
    _HDR_FRAG_HEAD = set("税金额价量位格名号项规单数")
    cands = [ln for ln in lines
             if data_top < _cy(ln) < end_cy
             and not _is_item_header_cell(ln["text"])
             and not (hdr_cy <= _cy(ln) <= _hdr_frag_top
                      and len(_norm_key(ln["text"])) == 2
                      and _norm_key(ln["text"])[0] in _HDR_FRAG_HEAD)]
    if not cands:
        return []

    # 行聚类
    bands, cur = [], []
    for ln in sorted(cands, key=_cy):
        # 用「与行首」比较而非「与上一个」：逐项比较会让误差累积，把多行串成一行
        if cur and abs(_cy(ln) - _cy(cur[0])) <= row_tol:
            cur.append(ln)
        else:
            if cur:
                bands.append(cur)
            cur = [ln]
    if cur:
        bands.append(cur)

    # 剔除"合计/税额小计"这类纯数字汇总行（否则会把列中心带偏）
    def _is_summary(band):
        texts = [b["text"].strip() for b in band if b["text"].strip()]
        if not texts:
            return True
        if any(re.search(r"合计|小写|大写", t) for t in texts):
            return True  # "（小写）110657.00" 这类汇总行不能当成明细
        # 合计行的金额常把小数点读成冒号/短横（`¥181：62` = 181.62），货币符号
        # 也常被读成 `Y`/`￥`/空白。判据：**整行没有任何汉字**且全部单元格都是"数字串"。
        # 明细行一定带汉字（品名/规格），所以不会被这条误杀。
        return all(not re.search(r"[\u4e00-\u9fff]", t)
                   and re.fullmatch(r"[^\u4e00-\u9fff\d]{0,2}[\d.,\s:：\-]+", t)
                   for t in texts)

    # 只保留"像明细行"的 band：至少 2 个数字单元格。必须**在算列中心之前**过滤，
    # 否则"税总函…""备注碎片"这类单格噪声会造出假列，把品名/规格挤到错误列上。
    def _itemish(band):
        return sum(1 for b in band if re.search(r"\d", b["text"])) >= 2

    data_bands = [b for b in bands if not _is_summary(b) and _itemish(b)]
    if not data_bands:
        return []

    # 列中心聚类
    centers = _cluster_1d([(b["x0"] + b["x1"]) / 2.0 for band in data_bands for b in band],
                          tol=max(20.0, 0.02 * W))
    if not centers:
        return []

    unit_x = anchors.get("unit", anchors.get("spec"))
    if unit_x is None:
        return []  # 定位不到锚点列，宁可不出明细也不出错位数据

    left = [c for c in centers if c < unit_x - 1]
    right = [c for c in centers if c >= unit_x - 1]

    # 左侧 → name / spec（可能存在也可能不存在，按 x 顺序）
    col_map = {}
    left_keys = ["name", "spec"][-len(left):] if left else []
    for k, c in zip(left_keys, left):
        col_map[k] = c
    # 右侧 → quantity / unit_price / amount / tax_rate / tax_amount
    right_keys = ["quantity", "unit_price", "amount", "tax_rate", "tax_amount"][-len(right):] if right else []
    for k, c in zip(right_keys, right):
        col_map[k] = c
    if len(right) > 5:
        col_map = {k: v for k, v in col_map.items() if k in _ITEM_KEYS}

    # 校验：税率列必须真的像税率，否则说明列映射不可信 → 放弃明细则不臆造。
    # 「像税率」= 带 %，或本身就是个 1~3 位小整数（低质扫描常把 `17%` 读成 `179`、
    # 把 `6%` 读成 `6`）。金额一律带两位小数，不会被误认成税率。
    rate_c = col_map.get("tax_rate")
    if rate_c is not None:
        rate_tol = max(12.0, 0.02 * W)
        rate_cells = [b["text"] for band in data_bands for b in band
                      if abs((b["x0"] + b["x1"]) / 2.0 - rate_c) <= rate_tol]
        if rate_cells and not any(
                ("%" in t) or re.fullmatch(r"\d{1,3}", t.strip()) for t in rate_cells):
            return []

    tol_cell = max(12.0, 0.02 * W)
    items = []
    for band in data_bands:
        row = {}
        for b in band:
            cx = (b["x0"] + b["x1"]) / 2.0
            if not col_map:
                break
            key = min(col_map, key=lambda k: abs(col_map[k] - cx))
            if abs(col_map[key] - cx) <= tol_cell and key not in row:
                row[key] = b["text"].strip()
        if len(row) >= 2:
            items.append(row)
    _fix_star_name(items)
    return items


def _fix_star_name(items):
    """把电商票「*类别*品名」从规格列挪回品名列（原地修改）。

    国家税务总局要求普通发票的品名写成 ``*类别*具体名称``（如
    ``*文具*古德牌凭证装订机``）。这类文本很长、又常是整行唯一的宽框，
    列心聚类容易把它归到相邻的「规格型号」列，于是品名列空、规格列一堆长文本。
    判据用 ``*类别*`` 前缀——它是品名专用标记，规格型号里不会出现。
    """
    for it in items:
        spec = (it.get("spec") or "").strip()
        name = (it.get("name") or "").strip()
        # 规格型号里永远不会出现 `*`，所以只要规格列以 `*` 开头且品名列空，
        # 这个值就一定是品名。不必强求两个 `*` 都读出来——低质扫描常丢掉后一个
        # （`*运输服务*客运服务费` 被读成 `*运输服务客运服务费`）。
        if spec and spec.startswith("*") and not name:
            it["name"], it["spec"] = spec, ""
        elif spec and re.match(r"^\*[^*\s]{1,20}\*", spec) and not name:
            it["name"], it["spec"] = spec, ""
        # 品名列空、规格列却是一串**纯中文短语**：几乎一定是品名错位到了规格列。
        # 规格型号总含数字/字母/Φ/×/- 等符号（W310、中90、DN80、Φ65、A4），
        # 而「信息服务费」这类 4 字以上的纯中文短语不可能是规格。
        elif (spec and not name and len(spec) >= 4
              and not re.search(r"[0-9A-Za-zΦφ×*\-]", spec)):
            it["name"], it["spec"] = spec, ""


_CHECK_CODE_RE = re.compile(r"校\s*[^\s\d码]{0,2}\s*码\s*[:：]?\s*([\d\s]{15,32})")


def _extract_check_code(lines, text=""):
    """抽取纸质发票的校验码（税总函规定的 20 位数字串）。

    「校验码」三个字在低分辨率票面上极易读歪（样本里出现过 ``校绘码``），
    而且常与 20 位数字直接连写、没有冒号（``校验码60000000000000000002``）。
    故标签按"校 + 0~2 个非数字非码字 + 码"模糊匹配，先框内、后全文明文。

    Args:
        lines: 行框列表（可为空）。
        text: 明文兜底。

    Returns:
        纯数字校验码；形态不符（位数不够、混入乱码）时返回空串，绝不臆造。
    """
    for src in ([ln["text"] or "" for ln in sorted(lines or [], key=_cy)], [text or ""]):
        for t in src:
            m = _CHECK_CODE_RE.search(t)
            if not m:
                continue
            d = _only_digits(m.group(1))
            if 15 <= len(d) <= 32:
                return d
    return ""


def _extract_header_geom(lines, H):
    """用坐标抽取票头三要素（发票代码 / 发票号码 / 开票日期）与联次。

    为什么必须用坐标：老式发票把「代码」和「No 号码」印在同一行，OCR 会连写成
    ``1000000134000000003400000000...``，任何正则在粘连数字里都只能切出一段错值
    （本样本曾得到 ``100000013400``）。但分行框里 ``No 10000001`` 与 ``3400000000``
    是两个独立框，按"框内纯数字位数"即可精确还原。

    Args:
        lines: 行框列表。
        H: 页高，用于把搜索范围限制在票头区域。

    Returns:
        ``{"invoice_code", "invoice_no", "issue_date", "copy_type"}`` 的子集。
    """
    out = {}
    if not lines:
        return out
    # 票头区域：把搜索限定在页面上 32%，避开正文里的长数字（税号/银行账号）
    head = [ln for ln in lines if _cy(ln) <= 0.32 * H]

    # 开票日期：整框就是一个规范日期（标签常与值分处两框、甚至顺序颠倒）
    for ln in sorted(head, key=_cy):
        m = re.fullmatch(r"(\d{4})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?",
                         (ln["text"] or "").strip())
        if m:
            out["issue_date"] = f"{m.group(1)}年{int(m.group(2)):02d}月{int(m.group(3)):02d}日"
            break

    # 发票号码：`No 10000001` 形态最可靠（发票号码为 8 位）
    for ln in sorted(head, key=_cy):
        m = re.fullmatch(r"NO[.:：]?\s*(\d{8})", (ln["text"] or "").strip(), re.I)
        if m:
            out.setdefault("invoice_no", m.group(1))

    # 发票代码：独立的 10 / 12 位纯数字框（老式票代码 10 位，新版 12 位）
    for ln in sorted(head, key=_cy):
        m = re.fullmatch(r"(\d{10}|\d{12})", (ln["text"] or "").strip())
        if m:
            out.setdefault("invoice_code", m.group(1))

    # 无 No 前缀时，8 位独立框作为发票号码兜底
    for ln in sorted(head, key=_cy):
        m = re.fullmatch(r"(\d{8})", (ln["text"] or "").strip())
        if m:
            out.setdefault("invoice_no", m.group(1))

    # 联次：竖排的「第三联发票联购买方记账凭证」
    for ln in sorted(lines, key=_cy):
        t = (ln["text"] or "").strip()
        if re.match(r"第[一二三四五六七八]联", t) and len(t) <= 30:
            out.setdefault("copy_type", t)
            break
    return out


def _seal_ok(v):
    """签章栏（收款人/复核/开票人）的值是否可信。

    这三栏都是人名。三种东西都不是人名，宁可留「（未识别）」：
      - 纯数字（≥10 位，通常是相邻的税号/账号被串过来）；
      - 印刷注记（"发票专用章（章）开票员"）；
      - **同排的另一个字段标签**——北京票的「收款人：」右侧只印了「销货单位」，
        右邻取值会把单位名当成人名填进收款人栏，这是"错填"，比留空危险得多。
    """
    v = (v or "").strip()
    if _is_junk_value(v):
        return False
    if any(k in v for k in ("单位", "购买方", "销售方", "章", "开户", "识别号")):
        return False
    return not (re.fullmatch(r"\d+", v) or len(v) > 12)


_SEAL_LABELS = (("收款人", "payee"), ("复核", "reviewer"),
                ("开票人", "issuer"), ("开票员", "issuer"))


def _extract_seal_geom(lines, H):
    """抽取票尾签章三项（收款人 / 复核 / 开票人）。

    这三个标签在票尾**同一行相邻**印刷，纯正则会串成一长串
    （本样本曾得到复核=``李四开票人张三销售方（``）。改为"只在本框文本内取值"，
    并把搜索范围限定在页面下 30%，避免命中正文里的同名词。

    值分两种印刷形态，都要覆盖：和标签同框（``收款人：张三``），或印在标签
    右侧的独立框里（``收款人：`` + 隔壁 ``张三``）——后者在样本里占多数，
    旧实现只认前者，7 张票的收款人因此全丢。

    Args:
        lines: 行框列表。
        H: 页高。

    Returns:
        ``(values, blanks)``：values 是 ``{"payee", "reviewer", "issuer"}`` 的子集；
        blanks 是"标签印了、但票面确实没填内容"的键集合——它与"没识别出来"是两回事，
        渲染时要分开标注（专票的收款人/复核常由机器打印，本就留空）。
    """
    values, blanks = {}, set()
    if not lines:
        return values, blanks
    band = [ln for ln in lines if _cy(ln) >= 0.70 * H]
    if not band:
        return values, blanks
    tol = max(12.0, 0.018 * H)

    for label, key in _SEAL_LABELS:
        if key in values:
            continue
        hit_label = False
        for ln in sorted(band, key=lambda x: (abs(_cy(x) - H * 0.85), x["x0"])):
            t = (ln["text"] or "").strip()
            if label not in _norm_key(t):
                continue
            hit_label = True
            m = re.search(label + r"[:：，,、]?\s*([^\s、，,。：:]{1,12})", t)
            v = m.group(1).strip() if m else ""
            if not _seal_ok(v):
                # 标签自己是干净的（冒号后没粘东西）→ 值可能在右侧独立框
                v = ""
                if re.fullmatch(label + r"\s*[:：]?\s*", t):
                    cands = [b for b in band
                             if b is not ln and b["x0"] >= ln["x1"] - 6
                             and abs(_cy(b) - _cy(ln)) <= tol]
                    cands.sort(key=lambda b: b["x0"])
                    for b in cands:
                        if _seal_ok((b["text"] or "").strip()):
                            v = b["text"].strip()
                            break
            if _seal_ok(v):
                values[key] = v
                break
        # 标签印在票面上、但左右都取不到可信的人名 → 票面空白，不是识别失败
        if hit_label and key not in values:
            blanks.add(key)
    return values, blanks


# 大写金额是**封闭字符集**，出现集合外字符必然是 OCR 形近误识。
# 这里只做形近/繁体归一，并把原始串另存 capital_raw，绝不丢原值。
_UPPER_ALIAS = str.maketrans({
    "貳": "贰", "參": "叁", "叄": "叁", "陸": "陆", "圓": "圆", "圈": "圆",
    "萬": "万", "億": "亿", "柴": "柒", "染": "柒", "玫": "玖", "玟": "玖", "案": "柒",
    # 低质票面上「壹→意」「圆→画」这类整字读歪很常见（北京票得到「意仟贰佰伍拾画整」）。
    # 大写金额是封闭字符集，集合外的字必然是误识，归一到最可能的那个不算臆造。
    "意": "壹", "画": "圆", "弍": "贰", "弎": "叁", "垒": "叁", "扒": "捌", "什": "拾",
    "装": "柒", "董": "壹",
})


def _norm_upper_amount(v):
    """归一大写金额里的形近误识字符（如「伍拾柴圆整」→「伍拾柒圆整」）并去掉非汉字噪声。

    只做两件事：① 形近/繁体 → 规范大写汉字；② 去掉 ``?``、空格等非汉字字符。
    **不**改写任何认不出的汉字——认不出就原样保留，让它在交付物里显眼，由人来判断。
    """
    v = (v or "").translate(_UPPER_ALIAS)
    return "".join(c for c in v if "\u4e00" <= c <= "\u9fff")


def _clean_amount(v):
    """规范化金额单元格：去货币符号/千分位，**拒绝粘连或多值**（宁可留空）。

    低质量扫描里 ￥ 常被识成 X / Y，两个相邻金额也会粘进同一个框
    （如 ``X4.640.77`` 实际是 4.64 与 0.77 两个值）。这种串既不能整体当金额，
    也不能猜其中哪一段是哪个字段——一律返回空串，让它在交付物里显示「（未识别）」。
    """
    v = (v or "").strip().replace(",", "").replace("，", "")
    # 小数点被读成冒号是低质票面上的高频错误（`¥181：62` 实为 181.62）。
    # 只认「数字 + 冒号 + 恰好两位数字」这个精确形态，避免把 `金额：100.00` 这类
    # 键值串也一起改写。
    v = re.sub(r"(\d)[：:](\d{2})(?!\d)", r"\1.\2", v)
    hits = re.findall(r"\d+\.\d{2}", v)
    if len(hits) != 1:
        return ""
    # 除了这一个金额之外还残留别的数字 → 说明是两值粘连，不可信
    if len(re.sub(r"[^\d.]", "", v)) > len(hits[0]) + 2:
        return ""
    return hits[0]


_FIG_LABEL_RE = re.compile(r"小\s*写|[（(]\s*小\s*[）)]")


def _is_figures_label(t):
    """判断该框是否为「价税合计（小写）」标签。

    标签被截断是常态：样本里出现过 `（小写）`、`(小写)`、`（小）`、`(小)` 四种写法，
    右侧的 ￥ 又被识成 羊 / 手 / ? / Y。只认完整的「小写」会漏掉一半。
    """
    return _FIG_LABEL_RE.search(t or "") is not None


def _figures_candidate(texts):
    """在一组文本里挑出唯一可信的小写金额（多值粘连时返回空，宁缺勿错）。"""
    for t in texts:
        v = _clean_amount(t)
        if v:
            return v
    return ""


def _extract_totals_geom(lines, H):
    """用坐标抽取「合计金额/税额」与「价税合计大小写」。

    纯正则在粘连文本里会把"价税合计（大写）"后面的整段（含销售方信息）都吞进来；
    坐标法直接把"标签同排右侧最近的那个框"当值，边界天然正确。

    Returns:
        ``{"total_excl_tax", "total_tax", "capital", "figures"}`` 的子集。
    """
    out = {}
    if not lines:
        return out
    tol = max(20.0, 0.02 * H)
    inc = next((ln for ln in lines if "价税合计" in _norm_key(ln["text"])), None)
    if inc is None:
        # 标签被读歪时（北京票「价税合计」→「佛税合许」），用「（大写）」定位：
        # 整张票上只有价税合计这一栏带「大写」字样，这个锚点比四个汉字更耐误识。
        inc = next((ln for ln in lines if "大写" in _norm_key(ln["text"])), None)
    if inc is None:
        return out
    inc_cy = _cy(inc)
    band = [ln for ln in lines if abs(_cy(ln) - inc_cy) <= tol * 2]

    cap = _label_value(inc, band, tol)
    if cap:
        cap = re.sub(r"^[（(]?\s*大写\s*[）)]?\s*", "", cap).strip()
        if cap:
            out["capital"] = cap

    # 小写金额：三个来源依次兜底。这一栏是旧实现丢得最多的地方——
    #   ① 标签与本值同框（`（小写）￥5200.00`、`(小)Y4780.00`）→ 取标签后残余串；
    #   ② 标签与值分框（`（小写）` 在左、`¥5200.00` 在右甚至下一行）→ 取右邻/近邻框；
    #   ③ 标签整块没检出（重庆票只有 `￥ 3276.00`）→ 取价税合计行右侧、不含成串汉字的框。
    # 大写金额串是**纯汉字**，天然被 ③ 的"汉字 <3 个"条件排除，不会大小写串台。
    fig = ""
    lbl = next((ln for ln in band if _is_figures_label(ln["text"])), None)
    if lbl is not None:
        m = re.search(r"[）)]?\s*([^）)]*)$", lbl["text"])
        if m:
            fig = _clean_amount(re.sub(r"^[^\d]{0,4}", "", m.group(1)))
        if not fig:
            near = [ln for ln in band if ln is not lbl and ln["x0"] >= lbl["x1"] - 8
                    and len(re.findall(r"[\u4e00-\u9fff]", ln["text"])) < 3]
            near.sort(key=lambda x: (abs(_cy(x) - _cy(lbl)), x["x0"]))
            fig = _figures_candidate([n["text"] for n in near])
    if not fig:
        right = [ln for ln in band if ln["x0"] > inc["x1"] - 8
                 and len(re.findall(r"[\u4e00-\u9fff]", ln["text"])) < 3]
        right.sort(key=lambda x: (abs(_cy(x) - inc_cy), -x["x0"]))
        fig = _figures_candidate([n["text"] for n in right])
    if fig:
        out["figures"] = fig

    # 合计金额/税额：价税合计行**上方**、最靠近的一行里若有两个金额，即视为合计行。
    # 金额允许不带 ￥（电子发票常见），但必须排除含"大写/小写"的价税合计行——它也在
    # 上方且更靠下，用"最近一行"会把合计行抢掉。故按行分组后，**从下往上找第一个
    # 含 ≥2 个金额的组**；明细行虽然在同样形态，但一定比合计行更靠上，不会被先选中。
    # 判据直接复用 _clean_amount 而不是抄一份正则：它已经处理了"小数点被读成冒号"
    # （`¥181：62`），抄正则会漏掉这类格子，导致合计行只剩 1 个金额而被整行丢弃。
    cur = [ln for ln in lines
           if _cy(ln) < inc_cy and _clean_amount(ln["text"])
           and not re.search(r"大写|小写", ln["text"])]
    if len(cur) >= 2:
        cur.sort(key=_cy)
        row_tol = max(10.0, 0.01 * H)
        rows, grp = [], []
        for ln in cur:
            if grp and abs(_cy(ln) - _cy(grp[0])) <= row_tol:
                grp.append(ln)
            else:
                if grp:
                    rows.append(grp)
                grp = [ln]
        if grp:
            rows.append(grp)
        for cand in reversed(rows):
            if len(cand) < 2:
                continue
            row = sorted(cand, key=lambda x: x["x0"])
            _ex = _clean_amount(row[0]["text"])
            _tx = _clean_amount(row[-1]["text"])
            if _ex:
                out["total_excl_tax"] = _ex
            if _tx:
                out["total_tax"] = _tx
            break
    return out


_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _only_digits(s):
    """提取纯数字并归一全角数字（票面上 `１２１０００` 这类全角印刷很常见）。"""
    return re.sub(r"\D", "", (s or "").translate(_FW_DIGITS))


def _extract_invoice(text, lines, W, H):
    """把发票 OCR 文本 + 坐标重建为符合 docs/invoice_schema.md 的结构化 schema 字典。
    非发票返回 None。键名匹配容忍不同时期版本的近义写法（见 _is_item_header 与各处正则）。"""
    # 票种判定用「明文 + 按阅读顺序拼回的坐标行」：raw_text 有可能缺标题行
    # （VLM 兜底或旧缓存的拼接顺序不同），只看明文会把整张发票判成非发票而丢 schema。
    _ordered = "".join(
        (ln.get("text") or "") for ln in sorted(lines or [], key=lambda x: (_cy(x), x["x0"]))
    )
    full = text + _ordered
    if not re.search(r"发票", full):
        return None
    doc = {
        "header": {}, "buyer": {}, "seller": {}, "items": [],
        "totals": {}, "total_incl_tax": {}, "remarks": "", "signatures": {},
    }
    # 标题：识别是否含省份（如票样“重庆增值税专用发票”）；优先保留 OCR 标题省份，
    # 避免与发票代码推断的省份（样本代码常与印刷省份不一致）冲突。
    mt = re.search(r"(" + "|".join(PROVINCE_NAMES) + r")?\s*"
                   r"(增值税电子普通发票|增值税电子专用发票|增值税专用发票|增值税普通发票"
                   r"|电子发票|普通发票)", full)
    if mt:
        doc["header"]["invoice_type"] = (mt.group(1) or "") + mt.group(2)
    # 发票代码/号码都有**固定位数**（税总函）：代码 10 位（老版）或 12 位（新版），
    # 号码 8 位（纸质/税控票）或 20 位（数电票）。不按位数守，就会把 18 位税号
    # 当成「发票代码」填进去——那是最典型的"错得很有说服力"。
    m = re.search(r"(?:发票代码|代码|发票(?!号码))\D{0,10}(\d{6,12})", text)
    if m and len(_only_digits(m.group(1))) in (10, 12):
        doc["header"]["invoice_code"] = _only_digits(m.group(1))
    m = re.search(r"(?:发票号码|发票号|号码)\D{0,10}(?:No)?\s*(\d{6,20})", text)
    if m and len(_only_digits(m.group(1))) in (8, 20):
        doc["header"]["invoice_no"] = _only_digits(m.group(1))
    m = re.search(r"(?:开票日期|开票时间|开具日期)[:：]?\s*([\d]{4}年[\d]{1,2}月[\d]{1,2}日|[\d]{4}[-/][\d]{1,2}[-/][\d]{1,2})", text)
    if m:
        doc["header"]["issue_date"] = m.group(1)
    m = re.search(r"机器编号\D{0,6}(\d{6,12})", text)
    if m:
        doc["header"]["machine_no"] = _only_digits(m.group(1))
    _cc = _extract_check_code(lines, text)
    if _cc:
        doc["header"]["check_code"] = _cc
    m = re.search(r"(第[一二三四五六]联[：:].{0,24})", text)
    if m:
        doc["header"]["copy_type"] = m.group(1).strip()

    # 票头三要素：坐标结果覆盖明文正则结果。
    # 明文里的「代码+号码」是连写的（100000013400000000...），正则只能切出错值；
    # 分行框里两者是独立框，按位数即可精确还原，故坐标优先。
    for _k, _v in _extract_header_geom(lines or [], H).items():
        if _v:
            doc["header"][_k] = _v

    # 购销方：先用明文块（无坐标时也有效），再用坐标结果覆盖（更可靠）
    buyer_txt = _extract_party(text, r"购买方|购货单位|购货方")
    seller_txt = _extract_party(text, r"销售方|销货单位|销货方")
    buyer_geo, seller_geo = {}, {}
    if lines:
        _hdr = next((ln for ln in lines if _is_item_header(ln["text"])), None)
        _row_tol = max(6.0, 0.02 * H)
        _hdr_cy = _cy(_hdr) if _hdr else H * 0.42
        _inc_cy = next((_cy(ln) for ln in lines if "价税合计" in _norm_key(ln["text"])), None)
        if _inc_cy is None:
            _inc_cy = next((_cy(ln) for ln in lines if "大写" in _norm_key(ln["text"])), None)
        if _inc_cy is None:
            # 两个锚点都没读出时**必须**给一个几何下界，否则销售方四要素整块丢失
            # （北京票就是这样把销方全丢的）。销售方块一定在明细表之后、票尾签章之前，
            # 取页高 62% 作为保守分界，宁可多收一点噪声也不整块放弃。
            _inc_cy = H * 0.62
        buyer_geo = _extract_party_geom(lines, 0, _hdr_cy - _row_tol, H)
        if _inc_cy is not None:
            seller_geo = _extract_party_geom(lines, _inc_cy, H, H)
    # 名称统一过一遍清理（截断粘连的字段标签 + 拒乱码），明文与坐标两条路都要走
    for _d in (buyer_txt, seller_txt, buyer_geo, seller_geo):
        if _d.get("name"):
            _n = _clean_name(_d["name"])
            if _n:
                _d["name"] = _n
            else:
                _d.pop("name", None)
    doc["buyer"] = {**buyer_txt, **{k: v for k, v in buyer_geo.items() if v}}
    doc["seller"] = {**seller_txt, **{k: v for k, v in seller_geo.items() if v}}
    doc["items"] = _extract_items_from_lines(lines, W, H)

    # 合计/价税合计：坐标优先，明文兜底
    _tot = _extract_totals_geom(lines or [], H)
    m = re.search(r"小写[）)]?\s*[¥￥]?\s*([\d]+\.\d{2})", text)
    if m and not _tot.get("figures"):
        _tot["figures"] = m.group(1)
    m = re.search(r"合计[^\d]{0,8}([¥￥]?\d[\d.]*)\s*(\d+%)\s*([¥￥]?\d[\d.]*)", text)
    if m:
        _tot.setdefault("total_excl_tax", m.group(1))
        _tot.setdefault("total_tax", m.group(3))
    doc["totals"]["total_excl_tax"] = _tot.get("total_excl_tax")
    doc["totals"]["total_tax"] = _tot.get("total_tax")
    _cap = _tot.get("capital")
    doc["total_incl_tax"]["capital"] = _norm_upper_amount(_cap)
    if _cap and doc["total_incl_tax"]["capital"] != _cap:
        doc["total_incl_tax"]["capital_raw"] = _cap  # 归一前的原始串，保证可追溯
    doc["total_incl_tax"]["figures"] = _tot.get("figures")

    m = re.search(r"备注[:：]?\s*(.*?)(?=收款人|复核|开票人|$)", text, re.S)
    if m:
        doc["remarks"] = m.group(1).strip()
    for key, label in (("payee", "收款人"), ("reviewer", "复核"), ("issuer", "开票人")):
        mm = re.search(label + r"[:：]?\s*([^\s、，,。]{1,12})", text)
        if mm and _seal_ok(mm.group(1)):
            doc["signatures"][key] = mm.group(1).strip()
    # 签章：坐标结果覆盖明文结果（明文会把三个标签串成一段）
    _seal, _seal_blank = _extract_seal_geom(lines or [], H)
    for _k, _v in _seal.items():
        if _v:
            doc["signatures"][_k] = _v

    # 「票面空白」与「没识别出来」必须分开记：专票的收款人/复核由机器打印，
    # 票面上本就留空，把它标成「（未识别）」会让复核人去找一个不存在的失败。
    _blank = set()
    for _k in _seal_blank:
        if not doc["signatures"].get(_k):
            _blank.add(f"signatures.{_k}")
    _head_band = _norm_key("".join(
        ln.get("text") or "" for ln in (lines or []) if 0.62 * H <= _cy(ln) <= 0.92 * H
    ))
    if not (doc.get("remarks") or "").strip() and "备注" in _head_band:
        _blank.add("remarks")     # 备注栏印了、但票面没写内容

    # 票种元信息：kind 决定"要不要密码区掩膜"，header_rows 决定基本信息显示哪几栏。
    # 二者都必须落到 schema 里，渲染层才能真正按票种输出，而不是所有票都套同一份栏位。
    _kind = _invoice_kind(_norm_key(full), doc["header"], lines, W, H)
    if not doc["header"].get("invoice_type"):
        # 标题被印章压住或拆框时（北京普票读成「通发票」，连省份都不完整），
        # 用票种判定结果补一个规范标题。这是**从标题区证据反推出的规范名**，
        # 不是凭空造字段——判不出票种时上面那条路会落到 vat_general，同样有据。
        doc["header"]["invoice_type"] = {
            "e_invoice": "电子发票",
            "vat_special": "增值税专用发票",
            "vat_general": "增值税普通发票",
        }[_kind]
    # 标题补省份：电子普票/新版普票的发票代码前 2 位不再是行政区划，代码推断会失手。
    # 但省份**只能从标题区取**（页面上 25%），用整页文本会把购销方地址里的省份
    # 当成开票地（宁波票曾被补成"上海普通发票"，因为销售方地址在上海）。
    _head_txt = _norm_key("".join(
        (ln.get("text") or "") for ln in sorted(lines or [], key=lambda x: (_cy(x), x["x0"]))
        if _cy(ln) <= 0.25 * H
    ))
    _t = str(doc["header"].get("invoice_type") or "")
    _place = list(PROVINCE_NAMES) + list(PLAN_CITIES)
    if _t and not any(p in _t for p in _place):
        # 取**在标题区文本里出现位置最靠前**的那个地名，而不是按列表顺序取第一个。
        # 重庆票的标题区同时含「重庆」（标题）和「天津城区」（购买方地址），
        # 按列表顺序会先命中「天津」，把标题补成「天津增值税专用发票」——这是错值。
        _hits = [(_head_txt.find(p), p) for p in _place if p in _head_txt]
        if _hits:
            doc["header"]["invoice_type"] = min(_hits)[1] + _t
    doc["meta"] = {
        "kind": _kind,
        # 密码区是否存在**只看版面**，不从票种反推：纸质「增值税电子普通发票」
        # 也有密码区，按"只有专票才有"去推会漏掉它；而数电票一定没有。
        "has_password_area": _has_password_area(lines or [], W, H) and _kind != "e_invoice",
        "header_rows": _pick_header_rows(doc["header"], full, _kind),
        "item_columns": _detect_item_columns(lines or [], H),
        "blank": sorted(_blank),
    }
    return doc


# ----------------------------- 渲染层 -----------------------------
# 渲染分派总原则：
#   1) md 与 html 都必须「人能读」；json 才是给 RAG / 资料库接入用的结构化载体。
#   2) 有 schema 的凭证类（发票 / 后续的证书等）→ 严格按 schema 输出，
#      该字段没抽到就写「（未识别）」，绝不用破折号混淆成"本来就为空"。
#   3) 无 schema 的普通文档 / 图片 → html 尽量还原原图版式；md 输出可读文本。
#   4) 原始 OCR 全文一律不进 md/html，只落 LOG 目录（见 log_store.py）。

_MISS = "（未识别）"
# 「票面空白」是第三种状态，必须与「未识别」分开：
#   - 未识别 = 票面印了内容，但没抽出来 → 复核人要去看原图/换手段重做；
#   - 票面空白 = 票面这一栏本来就空（专票收款人、备注栏）→ 复核人无需动作。
# 混成一个标记会让复核人对着 16 张都空的备注栏反复确认，纯属噪声。
_BLANK = "（票面空白）"
_ITEM_HEAD = ["name", "spec", "unit", "quantity", "unit_price", "amount", "tax_rate", "tax_amount"]
_ITEM_HEAD_CN = {"name": "名称", "spec": "规格型号", "unit": "单位", "quantity": "数量",
                 "unit_price": "单价", "amount": "金额", "tax_rate": "税率", "tax_amount": "税额"}


def _item_cols(doc):
    """明细表要渲染哪几列：优先按抽取层探测到的「票面实印列」，否则全 8 列。"""
    cols = [c for c in ((doc.get("meta") or {}).get("item_columns") or []) if c in _ITEM_HEAD]
    return cols or list(_ITEM_HEAD)


def _md_cell(v, blank=False):
    """markdown 表格单元格：竖线要转义（否则切坏表格）、换行压成空格、空值标状态。"""
    if v in (None, ""):
        return _BLANK if blank else _MISS
    return str(v).replace("|", "丨").replace("\n", " ").strip() or (_BLANK if blank else _MISS)


def _md_kv(rows):
    """把 [(标签, 值), (标签, 值, blank), ...] 渲染为两列 markdown 表。"""
    out = ["| 项目 | 内容 |", "| --- | --- |"]
    for r in rows:
        label, val = r[0], r[1]
        blank = bool(r[2]) if len(r) > 2 else False
        out.append(f"| {_md_cell(label)} | {_md_cell(val, blank)} |")
    return "\n".join(out)


def _combine(*vals):
    """组合字段（地址+电话、开户行+账号）：有值就拼，全缺才返回 None（交给 _md_cell 标未识别）。"""
    parts = [str(v).strip() for v in vals if v not in (None, "") and str(v).strip()]
    return "  ".join(parts) if parts else None


# 基本信息候选栏目：**不是每个票种都有全 6 项**。专票无校验码、电子发票无联次、
# 纸质普票有校验码。全量输出会让普票/电子票满屏「（未识别）」，把"结构上不存在"
# 和"没识别出来"混为一谈——对复核人是灾难。故按"票面是否印有该栏"裁剪。
# 这里也保留给后续凭证类型复用（证书等各自有候选表）。
_HEADER_CANDIDATES = [
    ("发票代码", "invoice_code"),
    ("发票号码", "invoice_no"),
    ("开票日期", "issue_date"),
    ("机器编号", "machine_no"),
    ("校验码", "check_code"),
    ("联次", "copy_type"),
]


# 数电票（全电发票）票面上**不存在**的栏目：它没有联次、没有机器编号、没有校验码。
# 注意纸质「增值税电子普通发票」不属于这里——它有这三栏，只有 20 位号码的才是数电票。
_KIND_EXCLUDE = {
    "e_invoice": {"copy_type", "machine_no", "check_code"},
}


def _pick_header_rows(header, full, kind=""):
    """按票面实际印制的栏目挑基本信息行（栏位随票种变化）。

    Args:
        header: schema 的 header 字典。
        full: 明文 + 坐标行拼接文本，用于判断票面上是否印有该栏目标签。
        kind: 票种（``e_invoice`` / ``vat_special`` / ``vat_general``），用于剔除
            该票种结构上不存在的栏目。

    Returns:
        ``[{"label": 栏名, "value": 值或 None}, ...]``；始终至少含发票号码与开票日期。
    """
    n = _norm_key(full)
    drop = _KIND_EXCLUDE.get(kind, set())
    rows = [{"label": lb, "value": header.get(k)}
            for lb, k in _HEADER_CANDIDATES
            if k not in drop and (header.get(k) or lb in n)]
    have = {r["label"] for r in rows}
    for lb, k in (("发票号码", "invoice_no"), ("开票日期", "issue_date")):
        if lb not in have:   # 票面标签被整体漏识别时的兜底，避免基本信息整块消失
            rows.append({"label": lb, "value": header.get(k)})
    # 按候选表原始顺序排列，保证不同票种的输出顺序一致可读
    order = {lb: i for i, (lb, _k) in enumerate(_HEADER_CANDIDATES)}
    return sorted(rows, key=lambda r: order.get(r["label"], 99))


def _has_password_area(lines, W, H):
    """用**版面特征**判断票面上是否存在密码区（只有专票有）。

    密码区是机器可读密文，OCR 往往认不出「密码区」这三个字，却认得那块乱码
    （``57<2*-1/227≤7--0+1292<4*599``）。所以判据取"位置 + 尺寸 + 字符构成"：
    票面中上部、宽度超过 1/7 页宽、汉字占比 ≤5% 的长串。

    这比找标签词稳，也比按标题判可靠（标题常被拆框读歪）；普票上不存在这样的
    区域，因此不会误掩。
    """
    if not lines or not W or not H:
        return False
    dense = []
    for ln in lines:
        if not (0.15 * H <= _cy(ln) <= 0.55 * H):
            continue
        t = (ln["text"] or "").strip()
        # 宽度下限取 1/7 页宽：密码区是**拆成多行**的长密文，每行只有整块的
        # 五六成宽（北京票每行 189~264px，页宽 1280）。按 1/4 页宽卡会整块漏判，
        # 进而把专票认成普票，密码区掩膜和栏位分派跟着一起错。
        if len(t) < 10 or (ln["x1"] - ln["x0"]) < 0.14 * W:
            continue
        cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
        if cjk / len(t) <= 0.05:
            dense.append(ln)
    # 单行长数字串不算：普票的 20 位校验码、银行卡号都是这个形态。
    # 密码区是**纵向堆叠的多行密文块**，故要求同一 x 区间内至少 3 行。
    if len(dense) < 3:
        return False
    # 再按**左边缘聚类**：密码区是一个左对齐的矩形块（每行起笔位置几乎相同），
    # 而税号、银行账号、发票号码都在别的列上，一聚类就被分出去。
    # 不做这一步，安徽票的 `No 10000001`（左 0.70）和税号（左 0.21）会把整块的
    # x 跨度撑到 0.70 页宽，明明有密码区却判成没有。
    tol_x = max(8.0, 0.05 * W)
    groups, cur = [], []
    for ln in sorted(dense, key=lambda x: x["x0"]):
        if cur and ln["x0"] - cur[-1]["x0"] > tol_x:
            groups.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        groups.append(cur)
    block = max(groups, key=len)
    if len(block) < 3:
        return False
    left = min(b["x0"] for b in block)
    span_x = max(b["x1"] for b in block) - left
    # 密码区一定在票面右侧（税控票版式固定）；左下角的同形长串是税号/账号，不是密文。
    return left >= 0.35 * W and span_x <= 0.45 * W


def _title_kind_from_head(n):
    """只凭**标题区文本**判票种（标题常被拆框/读歪，需要模糊判据）。

    发票标题是一个封闭集合，只有「专用发票」和「普通发票」两种后缀。汉字读歪之后
    仍有一条极其稳定的线索：**「通」只出现在「普通」里，专票标题永远没有「通」**。
    样本实测：`普通发票`/`首通发票`(山东)/`纸通发票`(北京普票)/`电普通发票`(上海)
    全都含 `通发票`；而 `专用发票`/`大用发票`(上海/湖南/广东)/`栈用发票`(安徽)/
    `税用发票`(湖北) 全都含 `用发票` 且不含 `通`。

    判据取 `通发票` 而不是单字 `通`，是为了避开地名——「南通增值税专用发票」
    里的「通」后面跟的是「增值税」，不会误判成普票。

    Args:
        n: 已 ``_norm_key`` 归一化的标题区文本。

    Returns:
        ``"vat_general"`` / ``"vat_special"`` / ``""``（判不出）。
    """
    if "通发票" in n:
        return "vat_general"
    if "用发票" in n:
        return "vat_special"
    return ""


def _invoice_kind(full, header, lines=None, W=0, H=0):
    """判定票种：``e_invoice``（数电票）/ ``vat_special`` / ``vat_general``。

    **只有数电票（全电发票）才没有密码区、校验码、机器编号、联次**。
    这是一个容易踩的坑：纸质「增值税电子普通发票」——名字里有"电子"——其实是
    税控系统的打印件，票面上密码区、校验码、机器编号一应俱全（北京发票样2 即如此）。
    所以**不能**用"标题含电子发票"来判数电票，硬判据只有一个：**发票号码 20 位**。

    判据顺序（标题最权威，因为它来自票面标题区且已归一）：
      1. 号码 20 位 / 明文含「数电」「全电」→ 数电票；
      2. 标题区模糊判别（见 :func:`_title_kind_from_head`）；
      3. 已抽出的标题里的「专用发票」/「普通发票」字面；
      4. 都判不出时按普票处理——**绝不能用"有没有密码区"来推专票**：
         增值税普通发票同样印密码区（北京普票、山东普票实拍均有），
         这个旁证是无效的，用了只会把普票批量误判成专票。
    """
    no = str(header.get("invoice_no") or "")
    title = str(header.get("invoice_type") or "")
    if len(re.sub(r"\D", "", no)) >= 20 or "数电" in full or "全电" in full:
        return "e_invoice"
    if "专用发票" in title:
        return "vat_special"
    if "普通发票" in title:
        return "vat_general"
    _tk = _title_kind_from_head(
        _norm_key("".join(ln.get("text") or "" for ln in (lines or []) if _cy(ln) <= 0.25 * H))
        if lines else full
    )
    if _tk:
        return _tk
    if "普通发票" in full:
        return "vat_general"
    if "专用发票" in full:
        return "vat_special"
    return "vat_general"

# --------------------------------------------------------------------------
# 公开别名：provider 层用不带下划线的名字调用，内部命名保持搬移前的原样，
# 这样"逐字搬移"的等价性可以继续用 git diff 一眼核对。
# --------------------------------------------------------------------------
extract_invoice = _extract_invoice
