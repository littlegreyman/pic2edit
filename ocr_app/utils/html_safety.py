# -*- coding: utf-8 -*-
"""不可信 HTML 的白名单清洗。

为什么需要它
------------
本项目有两处会把 **OCR/VLM 产出的文字**当 HTML 处理：

1. 导出 HTML：``markdown.markdown()`` 默认保留原始 HTML 直通，于是识别文本里的
   ``<script>`` / ``<iframe>`` / ``<img src=http://…>`` 会原样写进交付文件；
2. GUI 预览：``QTextBrowser.setHtml()`` 不执行 JS，但**会加载** ``<img src>``、
   ``<link>`` 指向的资源 —— 恶意文档里的远程地址就成了内网探测/外连通道。

而项目的 ``html_table`` 模式是**主动让模型输出 HTML**，等于给提示词注入开了一条
稳定的产出通道。所以不能依赖"Qt 目前没有那条 CVE"，只能按白名单清洗。

策略
----
``bleach`` 太重（且不是本项目的依赖），这里用标准库 ``html.parser`` 重写一遍：
只放行排版必需的标签与属性；``href`` / ``src`` 只允许 ``https:`` 与 ``data:image/``；
``style`` 里出现 ``url(`` / ``expression`` 直接丢弃。未知标签**保留文字、丢掉标签**，
避免把内容整段删掉（清洗不该改变"用户看到的文字"）。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List

#: 允许保留的标签。刻意不含 script/iframe/object/embed/form/link/meta/base/svg。
ALLOWED_TAGS = frozenset("""
a b blockquote br caption code col colgroup dd del div dl dt em figcaption figure
h1 h2 h3 h4 h5 h6 hr i img ins li ol p pre q s small span strong sub sup table
tbody td tfoot th thead tr u ul
""".split())

#: 允许保留的属性。id/class/style 用于版式；colspan/rowspan/align 用于表格。
ALLOWED_ATTRS = frozenset("""
align alt class colspan height href id rowspan src style title valign width
""".split())

#: 自闭合标签
VOID_TAGS = frozenset("br col hr img".split())

_SAFE_HREF_RE = re.compile(r"^(?:https?:|mailto:)", re.I)
#: img 只允许内嵌图片：远程图片在 GUI 预览里会真的发起请求（内网探测/追踪像素），
#: 而本项目自己垫底用的就是 data:image，不需要任何远程资源。
_SAFE_SRC_RE = re.compile(r"^data:image/", re.I)
_BAD_STYLE_RE = re.compile(r"url\s*\(|expression\s*\(|@import", re.I)


def _safe_url(value: str, attr: str) -> str:
    v = (value or "").strip()
    # 相对路径与 file:// / javascript: / data:text/html 一律拒绝：
    # 导出的 HTML 是独立文件，合法资源只有内嵌 data:image 与显式 https 链接。
    if attr == "src":
        return v if _SAFE_SRC_RE.match(v) else ""
    return v if _SAFE_HREF_RE.match(v) else ""


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self._skip_depth = 0

    # ---- 标签 ----
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object", "embed", "svg"):
            # 连内容一起丢：这些标签的内容不是"文字"，是代码或外部资源。
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag not in ALLOWED_TAGS:
            return                      # 未知标签：保留文字，丢掉标签
        clean = []
        for k, v in attrs:
            k = (k or "").lower()
            if k not in ALLOWED_ATTRS:
                continue
            if k in ("href", "src"):
                v = _safe_url(v or "", k)
                if not v:
                    continue
            if k == "style" and _BAD_STYLE_RE.search(v or ""):
                continue
            clean.append(f'{k}="{_attr_escape(v or "")}"')
        self.out.append("<" + tag + (" " + " ".join(clean) if clean else "") + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS and not self._skip_depth:
            self.out.append(f"</{tag}>")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object", "embed", "svg"):
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    # ---- 内容 ----
    def handle_data(self, data):
        if self._skip_depth:
            return
        self.out.append(_text_escape(data))

    def handle_comment(self, data):
        # 注释可能是条件注释（IE）或用来隐藏脚本；直接丢弃
        return

    def get_html(self) -> str:
        return "".join(self.out)


def _text_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _attr_escape(s: str) -> str:
    return _text_escape(s).replace('"', "&quot;")


def sanitize_html(fragment: str) -> str:
    """把不可信 HTML 片段洗成"只有排版标签 + 安全链接"的片段。

    Args:
        fragment: 原始片段（可能来自 Markdown 直通或 VLM 输出）。

    Returns:
        清洗后的片段。未闭合的标签由调用方所在文档结构容纳，不额外补齐。
    """
    if not fragment:
        return ""
    p = _Sanitizer()
    try:
        p.feed(fragment)
        p.close()
    except Exception:
        # 清洗器自身出错时宁可输出转义后的纯文本，也不要把原文透出去
        return "<pre>" + _text_escape(fragment) + "</pre>"
    return p.get_html()
