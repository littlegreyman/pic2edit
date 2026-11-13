# -*- coding: utf-8 -*-
"""发票抽取器回归：锁住真机上海普票暴露的两处错抽。

样张 ``testdata/shanghai_invoice_ocr.json`` 由一张真机扫描件的判别式 OCR 结果
（文本 + 行框 + 尺寸）**脱敏**而来：公司名/人名/地址/银行替换为示例值，
所有 >=8 位连续字母数字（税号、账号、发票代码、机器编号、商品编码）置 0。
版式与坐标保持原样，因此仍能复现：
  ① ``buyer.address`` 把「密码区」当地址；
  ② 「合 计」被 OCR 拆开后，汇总行进了明细，出现 ``name == "合"``。
详见 ``docs/schema-package-v2-plan.md`` B4。
"""
from __future__ import annotations

import json
import os
import re

from schemas.invoice.extract import extract_invoice

_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "testdata", "shanghai_invoice_ocr.json"
)


def _fixture() -> dict:
    with open(_FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _doc() -> dict:
    d = _fixture()
    return extract_invoice(d["text"], d["lines"], d["img_w"], d["img_h"])


def test_fixture_is_anonymized():
    """结构性自检：样张不得残留未脱敏的长标识串。"""
    blob = json.dumps(_fixture(), ensure_ascii=False)
    leftovers = [
        x for x in re.findall(r"[0-9A-Za-z]{8,}", blob) if set(x) != {"0"}
    ]
    assert not leftovers, f"样张含未脱敏标识: {leftovers[:5]}"


def test_buyer_address_is_not_password_area():
    """「密码区」是票面区块标签，不能成为购买方地址。"""
    buyer = _doc()["buyer"]
    assert buyer.get("address") != "密码区"
    assert not (buyer.get("address") or "").strip() or len(buyer["address"]) >= 4


def test_summary_row_is_not_an_item():
    """「合 计」拆框后仍须判为汇总行，不能混进明细。"""
    items = _doc()["items"]
    assert items, "真实明细行不能被误杀"
    assert all(re.sub(r"\s+", "", i.get("name") or "") not in ("合", "计", "合计")
               for i in items), items


def test_key_fields_survive_the_fix():
    """收紧判据不能把正常字段一起丢掉。"""
    doc = _doc()
    assert doc["buyer"].get("name")
    assert doc["seller"].get("name")
    assert doc["totals"].get("total_excl_tax") == "49.94"
    assert doc["totals"].get("total_tax") == "6.49"
