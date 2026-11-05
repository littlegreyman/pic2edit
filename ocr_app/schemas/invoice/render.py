# -*- coding: utf-8 -*-
"""发票渲染层（md / txt 两种纯文本版式）。

本模块由 skill 转换脚本机械搬移而来（Phase 3.2.1，逐字保留、行为零变化）：
原先发票 schema 的抽取/渲染**内嵌在转换流程**里，导致
  ① 转换任意文档都要先跑一遍「这是不是发票」的判定；
  ② 新增一种凭证必须改消费侧代码并重新发版。

搬出后，它可以被 GUI / Skill / 未来的声明式运行时**按需调用**，
转换流程本身不再知道发票的存在（见 schemas/registry.py 与 schemas/base.py）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .extract import (
    _BLANK,
    _HEADER_CANDIDATES,
    _ITEM_HEAD_CN,
    _MISS,
    _combine,
    _item_cols,
    _md_cell,
    _md_kv,
)
from .html_layout import invoice_title_text


def _render_md_schema(doc):
    """发票 schema → 可读 markdown。

    栏目顺序固定（对齐纸质票面的阅读顺序）。缺值分两态：``（未识别）`` 与 ``（票面空白）``，
    见 ``_BLANK`` 的说明——这是"未识别比例偏高"观感的主要来源之一。
    """
    h = doc.get("header") or {}
    buyer = doc.get("buyer") or {}
    seller = doc.get("seller") or {}
    items = doc.get("items") or []
    totals = doc.get("totals") or {}
    incl = doc.get("total_incl_tax") or {}
    sig = doc.get("signatures") or {}
    meta = doc.get("meta") or {}
    blank = set(meta.get("blank") or [])

    parts = [f"# {invoice_title_text(h)}"]

    rows = meta.get("header_rows") or [
        {"label": lb, "value": h.get(k)} for lb, k in _HEADER_CANDIDATES
    ]
    parts.append("## 一、发票基本信息\n" + _md_kv([(r["label"], r["value"]) for r in rows]))

    for title, party in (("二、购买方", buyer), ("三、销售方", seller)):
        parts.append(f"## {title}\n" + _md_kv([
            ("名称", party.get("name")),
            ("纳税人识别号", party.get("taxpayer_id")),
            ("地址、电话", _combine(party.get("address"), party.get("phone"))),
            ("开户行及账号", _combine(party.get("bank_name"), party.get("bank_account"))),
        ]))

    cols = _item_cols(doc)
    if items:
        head = "| " + " | ".join(_ITEM_HEAD_CN[c] for c in cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        body = ["| " + " | ".join(_md_cell(it.get(c)) for c in cols) + " |" for it in items]
        parts.append("## 四、货物或应税劳务、服务名称\n" + "\n".join([head, sep] + body))
    else:
        parts.append(f"## 四、货物或应税劳务、服务名称\n{_MISS}")

    parts.append("## 五、合计与价税合计\n" + _md_kv([
        ("金额合计", totals.get("total_excl_tax")),
        ("税额合计", totals.get("total_tax")),
        ("价税合计（大写）", incl.get("capital")),
        ("价税合计（小写）", incl.get("figures")),
    ]))

    _rm = str(doc.get("remarks") or "").strip()
    parts.append("## 六、备注\n" + (_rm or (_BLANK if "remarks" in blank else _MISS)))

    parts.append("## 七、签章\n" + _md_kv([
        ("收款人", sig.get("payee"), "signatures.payee" in blank),
        ("复核", sig.get("reviewer"), "signatures.reviewer" in blank),
        ("开票人", sig.get("issuer"), "signatures.issuer" in blank),
    ]))

    return "\n\n".join(parts)


def _render_txt_schema(doc):
    """发票 schema → 纯文本（`标签：值` 逐行；明细用 TAB 分隔），便于喂给下游脚本。"""
    h = doc.get("header") or {}
    meta = doc.get("meta") or {}
    blank = set(meta.get("blank") or [])
    out = [invoice_title_text(h), ""]

    def kv(label, val, is_blank=False):
        out.append(f"{label}：{_BLANK if is_blank else (_MISS if val in (None, '') else str(val).strip())}")

    out.append("[发票基本信息]")
    _rows = meta.get("header_rows") or [
        {"label": lb, "value": h.get(k)} for lb, k in _HEADER_CANDIDATES
    ]
    for r in _rows:
        kv(r["label"], r["value"])

    for sec, party in (("购买方", doc.get("buyer") or {}), ("销售方", doc.get("seller") or {})):
        out.append(f"[{sec}]")
        kv("名称", party.get("name"))
        kv("纳税人识别号", party.get("taxpayer_id"))
        kv("地址、电话", _combine(party.get("address"), party.get("phone")))
        kv("开户行及账号", _combine(party.get("bank_name"), party.get("bank_account")))

    cols = _item_cols(doc)
    items = doc.get("items") or []
    out.append("[货物或应税劳务、服务名称]")
    if items:
        out.append("\t".join(_ITEM_HEAD_CN[c] for c in cols))
        for it in items:
            out.append("\t".join(_MISS if it.get(c) in (None, "") else str(it.get(c)) for c in cols))
    else:
        out.append(_MISS)

    totals = doc.get("totals") or {}
    incl = doc.get("total_incl_tax") or {}
    out.append("[合计与价税合计]")
    kv("金额合计", totals.get("total_excl_tax"))
    kv("税额合计", totals.get("total_tax"))
    kv("价税合计（大写）", incl.get("capital"))
    kv("价税合计（小写）", incl.get("figures"))

    out.append("[备注]")
    out.append(str(doc.get("remarks") or "").strip()
               or (_BLANK if "remarks" in blank else _MISS))

    sig = doc.get("signatures") or {}
    out.append("[签章]")
    kv("收款人", sig.get("payee"))
    kv("复核", sig.get("reviewer"))
    kv("开票人", sig.get("issuer"))
    return "\n".join(out)

# 公开别名（同上：内部命名不动，只加一层对外的名字）
render_md_schema = _render_md_schema
render_txt_schema = _render_txt_schema
