# -*- coding: utf-8 -*-
"""内置 schema：增值税发票（抽取 + md/txt/html 三套渲染）。

抽取层 ``extract`` 有 1300 行，因为发票的难点根本不在字段多，而在**版式多变**：
专票 / 普票 / 电子票 / 数电票、各省差异、密码区底纹、星号粘连、跨栏明细、
大写金额校验。纯 regex + 标签邻近抽不出这种鲁棒性，所以它保留为「Python 插件」
而不是声明式 yaml——这是 ``schemas/README`` 里 L2/L3 分档的 L3。

相比之下，结构规整的单据（资产卡片、报销单、证照）应走声明式 yaml，
用户填一份表就能新增，不必再写一千行。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from . import extract as _ex
from . import html_layout as _html
from . import render as _rd

#: 数电票票名特征（决策 1）：出现即交给独立包 ``invoice_digital``。
#: 数电票删除了发票代码/密码区/地址电话/银行账号栏、号码改 20 位，
#: 套用本包 27 字段的旧版纸电票模型必然抽错。两包 detect 必须互斥，
#: 否则 auto 模式下恒真的 invoice 会把数电票全部抢走。
_DIGITAL_MARKERS = (
    "电子发票（普通发票）",
    "电子发票（增值税专用发票）",
    "全面数字化电子发票",
    "数电票",
)


class InvoiceProvider:
    """发票 schema 提供者。"""

    id = "invoice"
    title = "增值税发票"
    version = "1.0.0"
    kind = "builtin"
    #: 票种判定本身依赖明细表/密码区的坐标证据，只能在 extract 里做，
    #: 因此 priority 取大值让它最后被尝试。detect 不再恒真：显式排除数电票
    #: 票名，把那一类交给 priority=50 的 invoice_digital（见 _DIGITAL_MARKERS）。
    priority = 100
    #: 不声明 layout：``layout`` 是**声明式** schema 的 HTML 版式文件，
    #: 而发票的票面 HTML 由 ``render_html()`` 现算（html_layout.py 是代码不是模板）。
    #: 曾错误地写成 "html_layout.py"，让 lint / registry 把 .py 当版式文件处理。
    layout: Optional[str] = None

    def __init__(self, package_dir: str = "") -> None:
        self.package_dir = package_dir or os.path.dirname(os.path.abspath(__file__))

    # ---------- 判定 / 抽取 ----------
    def detect(self, text: str, lines, width: int, height: int) -> bool:
        # 只做「票种互斥」这一件事：数电票交给独立包，其余仍交给 extract
        # 用版面证据判定（保持「不靠关键词猜票种」的既有行为）。
        body = text or ""
        return not any(marker in body for marker in _DIGITAL_MARKERS)

    def extract(self, text: str, lines, width: int, height: int) -> Optional[Dict[str, Any]]:
        return _ex.extract_invoice(text, lines, width, height)

    # ---------- 渲染 ----------
    def render_md(self, doc: Dict[str, Any]) -> str:
        return _rd.render_md_schema(doc)

    def render_txt(self, doc: Dict[str, Any]) -> str:
        return _rd.render_txt_schema(doc)

    def render_html(self, doc: Dict[str, Any]) -> str:
        return _html.invoice_fragment(doc)

    def css(self) -> str:
        return _html.INVOICE_CSS

    # ---------- 供测试与工具使用 ----------
    def full_html(self, doc: Dict[str, Any]) -> str:
        """自包含的整页 HTML（内含样式），便于人工核对抽取是否归位。"""
        return _html.render_invoice_html(doc)


def get_provider(package_dir: str = "", manifest: Optional[Dict[str, Any]] = None):
    return InvoiceProvider(package_dir)
