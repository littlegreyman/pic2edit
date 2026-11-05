#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""invoice_render.py — 把发票 schema 字典渲染为「增值税专用发票/普通发票」经典版式 HTML。

设计目标
--------
在 RAG 平台前端 / 知识库查看器里，把抽取出的结构化发票字段，以**接近国家税务总局
统一印制的纸质发票版式**还原展示（参照三张纸质票样的共同布局），而非简单平铺表格——
便于人工核对「抽取是否完整、字段是否归位」。

版式还原范围（三张纸质专票的共同之处）
--------------------------------
  - 外红框 + 标题（发票名称）+ 右侧联次竖排标签；
  - 右上「发票代码 / 发票号码 / 开票日期 / 机器编号」小表；
  - 购买方块（名称 / 纳税人识别号 / 地址、电话 / 开户行及账号）；
  - 货物或应税劳务、服务名称 8 列明细表（含合计、价税合计大写/小写）；
  - 销售方块（同购买方四字段）；
  - 备注；收款人 / 复核 / 开票人 + 销售方（章）；
  - 密码区（掩膜后仅占位，不显示密文）。

说明
----
  - 纯标准库实现（`html` 模块），**不依赖 pic2edit 项目**，可独立用于任意发票 schema 字典。
  - 视觉版式遵循国家税务总局统一印制的纸质发票共同布局（GB/T 42965 仅规定数据元，
    版式本身由总局印制规范约定；此处按三张票样共同结构还原）。
  - 数电票 / 机动车 / 二手车 因版式差异较大，建议另写 `render_*` 变体（见 §5 子结构）。
"""
from __future__ import annotations

import html
from typing import Any, Dict


# --------------------------------------------------------------------------
# 版式 CSS（内联，自包含，便于直接浏览器打开 / 嵌入知识库前端）
# --------------------------------------------------------------------------
INVOICE_CSS = """
.inv-wrap{font-family:"Microsoft YaHei","SimSun",sans-serif;max-width:920px;margin:12px auto;
  --frame:#9c2b22; --ink:#0b57d0; --tint:#f3e9e7}
.inv{border:3px solid var(--frame);border-radius:4px;padding:0;position:relative;
  background:#fff;color:var(--frame)}
.inv-title-row{display:flex;align-items:center;justify-content:space-between;
  border-bottom:2px solid var(--frame);padding:10px 14px}
.inv-title{font-size:24px;font-weight:700;letter-spacing:2px;color:var(--frame)}
.inv-copy{writing-mode:vertical-rl;font-size:13px;border-left:2px solid var(--frame);
  padding-left:6px;color:var(--frame);max-height:120px}
.inv-topmeta{width:100%;border-collapse:collapse}
.inv-topmeta td{border:1px solid var(--frame);padding:4px 8px;font-size:13px;color:var(--frame)}
.inv-topmeta td.k{background:var(--tint);font-weight:600;width:84px;color:var(--frame)}
.inv-section{border-top:2px solid var(--frame)}
.inv-party{width:100%;border-collapse:collapse}
.inv-party th{width:74px;background:var(--tint);border:1px solid var(--frame);
  vertical-align:middle;text-align:center;font-size:14px;color:var(--frame)}
.inv-party td{border:1px solid var(--frame);padding:4px 8px;font-size:13px;color:var(--frame)}
.inv-party td.lbl{width:120px;background:var(--tint);font-weight:600;color:var(--frame)}
.inv-items{width:100%;border-collapse:collapse}
.inv-items th,.inv-items td{border:1px solid var(--frame);padding:4px 6px;font-size:12.5px;
  text-align:center;color:var(--frame)}
.inv-items th{background:var(--tint)}
.inv-items td.name{text-align:left}
.inv-items tr.sum td{font-weight:700}
.inv-totals{width:100%;border-collapse:collapse}
.inv-totals td{border:1px solid var(--frame);padding:4px 8px;font-size:13px;color:var(--frame)}
.inv-totals td.lbl{background:var(--tint);font-weight:600;width:110px;color:var(--frame)}
.inv-totals td.big{text-align:left;width:60%}
.inv-remark{border-top:2px solid var(--frame);padding:6px 10px;font-size:13px;
  color:var(--frame);min-height:34px}
.inv-sign{display:flex;justify-content:space-around;border-top:2px solid var(--frame);
  padding:8px 10px;font-size:13px;color:var(--frame)}
.inv-pw{position:absolute;right:14px;top:96px;width:188px;height:90px;border:1.5px solid var(--frame);
  border-radius:3px;display:flex;align-items:center;justify-content:center;
  font-size:12px;color:var(--frame);background:#fff;opacity:.92;text-align:center}
.inv-foot{font-size:11px;color:var(--frame);opacity:.7;padding:4px 10px}
/* 填入的内容（纳税人填写的键值）：蓝字 + 楷体（模拟手写填入） */
.inv-val{font-family:"KaiTi","STKaiti","Kaiti SC",cursive;color:var(--ink)}
/* schema 里该字段没抽到：显式标注"未识别"，绝不臆造、也不用破折号混淆成"空值" */
.inv-miss{color:#b03a2e;font-style:italic;font-size:12px;opacity:.85}
/* 票面本来就空（专票收款人、未填写的备注）：中性灰，避免与"没识别出来"混淆 */
.inv-blank{color:#8a8a8a;font-style:italic;font-size:12px;opacity:.8}
"""


def _esc(value: Any) -> str:
    """转义 HTML 特殊字符；None/空转为空串。"""
    if value is None:
        return ""
    return html.escape(str(value))


PLACEHOLDER = "（未识别）"
# 第三态：票面这一栏印了、但内容是空的（专票收款人/复核、备注栏）。
# 它与"没识别出来"对复核人意味着完全不同的动作，必须分开标注。
BLANK = "（票面空白）"


def _empty(value: Any, blank: bool = False) -> str:
    """返回字段展示值。

    有值 → 蓝字楷体（``.inv-val``）；无值 → 显式 ``（未识别）``（``.inv-miss``），
    若已确认是"票面本来就没填"（``blank=True``）则给中性灰的 ``（票面空白）``。
    **不用破折号占位**：破折号会被误读成"该字段本来为空"，而事实是"没识别出来"，
    两者对复核人意味着完全不同的处理动作。
    """
    if value in (None, ""):
        if blank:
            return f'<span class="inv-blank">{BLANK}</span>'
        return f'<span class="inv-miss">{PLACEHOLDER}</span>'
    return f'<span class="inv-val">{_esc(value)}</span>'


def _join(*vals: Any) -> str:
    """把「地址 + 电话」「开户行 + 账号」这类组合字段拼成一个展示值。

    任一部分有值就拼接；全都缺失才整体标 ``（未识别）``，避免出现
    「（未识别）  （未识别）」这种重复噪声。
    """
    parts = [str(v).strip() for v in vals if v not in (None, "") and str(v).strip()]
    if not parts:
        return _empty("")
    return f'<span class="inv-val">{_esc("  ".join(parts))}</span>'


# 行政区划代码（GB/T 2260）前 2 位 → 省份简称；
# 发票代码前 2 位即印制地行政区划，用于从代码推断省份名并前缀到标题。
PROVINCE_CODE = {
    "11": "北京", "12": "天津", "13": "河北", "14": "山西", "15": "内蒙古",
    "21": "辽宁", "22": "吉林", "23": "黑龙江",
    "31": "上海", "32": "江苏", "33": "浙江", "34": "安徽", "35": "福建",
    "36": "江西", "37": "山东",
    "41": "河南", "42": "湖北", "43": "湖南", "44": "广东", "45": "广西", "46": "海南",
    "50": "重庆", "51": "四川", "52": "贵州", "53": "云南", "54": "西藏",
    "61": "陕西", "62": "甘肃", "63": "青海", "64": "宁夏", "65": "新疆",
    "71": "台湾", "81": "香港", "82": "澳门",
}


# 省份名列表（按长度降序，便于正则优先匹配较长省份名，如“内蒙古/黑龙江”）。
PROVINCE_NAMES = sorted(PROVINCE_CODE.values(), key=len, reverse=True)


def invoice_title_text(header: Dict[str, Any]) -> str:
    """推断发票标题的**纯文本**（未做 HTML 转义），供 markdown / 纯文本 / 日志复用。

    优先采用 OCR 识别出的标题（若已含省份，如票样"重庆增值税专用发票"则原样保留，
    避免与发票代码推断的省份冲突）；标题无省份时，用发票代码前 2 位
    （GB/T 2260 行政区划）映射省份前缀。
    """
    base = header.get("invoice_type") or "增值税专用发票"
    if any(p in base for p in PROVINCE_CODE.values()):
        return base
    code = header.get("invoice_code") or ""
    province = PROVINCE_CODE.get(code[:2], "") if isinstance(code, str) and len(code) >= 2 else ""
    return (province + base) if province else base


def _invoice_title(header: Dict[str, Any]) -> str:
    """HTML 用的标题（已转义），逻辑同 :func:`invoice_title_text`。"""
    return _esc(invoice_title_text(header))


def _invoice_fragment(data: Dict[str, Any]) -> str:
    """渲染发票经典版式的 <div class="inv-wrap"> 片段（不含 <html>/<head> 包裹），便于嵌入。"""
    header = data.get("header", {}) or {}
    buyer = data.get("buyer", {}) or {}
    seller = data.get("seller", {}) or {}
    items = data.get("items", []) or []
    totals = data.get("totals", {}) or {}
    incl = data.get("total_incl_tax", {}) or {}
    remarks = data.get("remarks")
    sig = data.get("signatures", {}) or {}

    invoice_type = _invoice_title(header)
    copy_type = _empty(header.get("copy_type"))

    # 购买方 / 销售方 区块
    def _party_block(label: str, p: Dict[str, Any]) -> str:
        rows = [
            ("名称", _empty(p.get("name"))),
            ("纳税人识别号", _empty(p.get("taxpayer_id"))),
            ("地址、电话", _join(p.get("address"), p.get("phone"))),
            ("开户行及账号", _join(p.get("bank_name"), p.get("bank_account"))),
        ]
        # 首行：左侧 rowspan 标签 + 第一个数据行同处一行（避免顶部空条）
        first_k, first_v = rows[0]
        rest = "".join(
            f'<tr><td class="lbl">{_esc(k)}</td><td colspan="3">{v}</td></tr>' for k, v in rows[1:]
        )
        head = (f'<tr><th rowspan="4">{_esc(label)}</th>'
                f'<td class="lbl">{_esc(first_k)}</td><td colspan="3">{first_v}</td></tr>')
        return (f'<div class="inv-section"><table class="inv-party">{head}{rest}</table></div>')

    buyer_block = _party_block("购买方", buyer)
    seller_block = _party_block("销售方", seller)

    # 明细表：只渲染**票面实印的列**。代开版式/部分老普票没有「单位」列，
    # 硬塞 8 列会让每行都挂一个「（未识别）」，把"票面没这栏"读成"识别失败"。
    all_cols = [("name", "货物或应税劳务、服务名称"), ("spec", "规格型号"), ("unit", "单位"),
                ("quantity", "数量"), ("unit_price", "单价"), ("amount", "金额"),
                ("tax_rate", "税率"), ("tax_amount", "税额")]
    keep = [c for c in ((data.get("meta") or {}).get("item_columns") or []) if c != "name"]
    cols = [c for c in all_cols if c[1] and (c[0] == "name" or not keep or c[0] in keep)]
    head = "<tr>" + "".join(f"<th>{_esc(cn)}</th>" for _k, cn in cols) + "</tr>"
    rows_html = []
    for it in items:
        rate = it.get("tax_rate") or it.get("levy_rate") or it.get("tax_rate_or_levy") or ""
        rows_html.append('<tr>' + "".join(
            f'<td class="{"name" if key == "name" else ""}">'
            f'{_empty(rate if key == "tax_rate" else it.get(key))}</td>'
            for key, _cn in cols) + '</tr>')
    if not rows_html:
        rows_html.append(f'<tr><td class="name" colspan="{len(cols)}">'
                         f'<span class="inv-miss">{PLACEHOLDER}</span></td></tr>')
    items_table = (f'<div class="inv-section"><table class="inv-items">{head}'
                   f'{"".join(rows_html)}</table></div>')

    # 合计 + 价税合计
    totals_block = (
        '<div class="inv-section"><table class="inv-totals">'
        f'<tr><td class="lbl">合计</td><td>￥{_empty(totals.get("total_excl_tax"))}</td>'
        f'<td class="lbl">税额</td><td>￥{_empty(totals.get("total_tax"))}</td></tr>'
        f'<tr><td class="lbl">价税合计（大写）</td><td class="big">{_empty(incl.get("capital"))}</td>'
        f'<td class="lbl">小写</td><td>￥{_empty(incl.get("figures"))}</td></tr>'
        '</table></div>')

    # 「票面空白」与「未识别」分开标记（见 _empty）
    blanks = set((data.get("meta") or {}).get("blank") or [])
    remark_block = (f'<div class="inv-remark">备注：'
                    f'{_empty(remarks, "remarks" in blanks)}</div>')

    sign_block = (
        '<div class="inv-sign">'
        f'<span>收款人：{_empty(sig.get("payee"), "signatures.payee" in blanks)}</span>'
        f'<span>复核：{_empty(sig.get("reviewer"), "signatures.reviewer" in blanks)}</span>'
        f'<span>开票人：{_empty(sig.get("issuer"), "signatures.issuer" in blanks)}</span>'
        f'<span>销售方：（章）{_esc(sig.get("seller_seal") and "✓" or "")}</span>'
        '</div>')

    # 密码区：**只有增值税专用发票有**。普通发票/电子发票票面上没有密码区，
    # 给它们也渲染一个"密码区（密文不展示）"格子，等于凭空多印一栏，人会以为漏识别。
    meta = data.get("meta") or {}
    has_pw = meta.get("has_password_area")
    if has_pw is None:
        has_pw = "专用发票" in str(header.get("invoice_type") or "")
    pw_block = ('<div class="inv-pw">密码区<br>（密文不展示）</div>' if has_pw else '')

    # 基本信息：栏位随票种变化（专票无校验码、电子发票无联次），由抽取层给出的
    # header_rows 决定；未提供时回退到全量候选（兼容手写/测试构造的 schema）。
    rows = meta.get("header_rows") or [
        {"label": lb, "value": header.get(k)}
        for lb, k in (("发票代码", "invoice_code"), ("发票号码", "invoice_no"),
                      ("开票日期", "issue_date"), ("机器编号", "machine_no"),
                      ("校验码", "check_code"), ("联次", "copy_type"))
    ]
    cells = [(r.get("label", ""), _empty(r.get("value"))) for r in rows]
    if len(cells) % 2:                      # 两列布局，奇数补空让表格闭合
        cells.append(("", ""))
    trs = "".join(
        f'<tr><td class="k">{_esc(cells[i][0])}</td><td>{cells[i][1]}</td>'
        f'<td class="k">{_esc(cells[i + 1][0])}</td><td>{cells[i + 1][1]}</td></tr>'
        for i in range(0, len(cells), 2)
    )
    top_meta = f'<table class="inv-topmeta">{trs}</table>'

    return (f'<div class="inv-wrap"><div class="inv">'
            f'<div class="inv-title-row"><div class="inv-title">{invoice_type}</div>'
            f'<div class="inv-copy">{copy_type}</div></div>'
            f'{top_meta}{buyer_block}{pw_block}{items_table}{totals_block}'
            f'{seller_block}{remark_block}{sign_block}'
            f'<div class="inv-foot">本页面由 OCR 抽取的结构化字段按发票版式还原，仅供核对；'
            f'最终以票面原件为准。</div>'
            f'</div></div>')


def render_invoice_html(data: Dict[str, Any]) -> str:
    """把发票 schema 字典渲染为完整 HTML 文档（自包含 CSS，可直接浏览器打开）。

    Args:
        data: 符合 docs/invoice_schema.md 主 schema 的字典；缺字段时安全留空，绝不臆造。
    Returns:
        完整 <html> 文档字符串。

    Raises:
        不抛异常；任何缺失字段以占位符呈现，保证渲染永不中断。
    """
    return (f'<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            f'<title>发票</title><style>{INVOICE_CSS}</style></head>'
            f'<body>{_invoice_fragment(data)}</body></html>')


if __name__ == "__main__":
    # 自测：用安徽专票票样数据渲染演示
    import sys
    sample = {
        "header": {"invoice_type": "增值税专用发票", "invoice_code": "3400000000",
                    "invoice_no": "10000001", "issue_date": "2017年12月01日",
                    "copy_type": "第三联：发票联 购买方记账凭证"},
        "buyer": {"name": "示例电机有限公司", "taxpayer_id": "91000000000000001X",
                   "address": "某某省某某市某某路", "phone": "0000-0000001",
                   "bank_name": "示例银行城北支行", "bank_account": "12345678901234567890"},
        "items": [
            {"name": "碳结圈", "spec": "Φ90", "unit": "吨", "quantity": 4.736,
             "unit_price": 3957.2649573, "amount": 18741.61, "tax_rate": 17, "tax_amount": 3186.07},
            {"name": "碳结圈", "spec": "Φ80", "unit": "吨", "quantity": 6.674,
             "unit_price": 3957.2649573, "amount": 26410.79, "tax_rate": 17, "tax_amount": 4489.83},
            {"name": "碳结圈", "spec": "Φ65", "unit": "吨", "quantity": 12.49,
             "unit_price": 3957.2649573, "amount": 49426.24, "tax_rate": 17, "tax_amount": 8402.46},
        ],
        "totals": {"total_excl_tax": 94578.64, "total_tax": 16078.36},
        "total_incl_tax": {"capital": "壹拾壹万零陆佰伍拾柒圆整", "figures": 110657.00},
        "seller": {"name": "示例贸易有限公司", "taxpayer_id": "91000000000000002H",
                    "address": "某某市某某路162号某某大厦B座26楼", "phone": "0000-0000002",
                    "bank_name": "示例银行湖滨支行", "bank_account": "1234567890123456"},
        "remarks": "示例贸易有限公司 91000000000000002H 发票专用章",
        "signatures": {"payee": "张三", "reviewer": "李四", "issuer": "张三", "seller_seal": True},
    }
    out = sys.argv[1] if len(sys.argv) > 1 else "invoice_demo.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_invoice_html(sample))
    print(f"written: {out}")

# 公开别名：provider 层不调用带下划线的私有名
invoice_fragment = _invoice_fragment
