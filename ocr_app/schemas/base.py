# -*- coding: utf-8 -*-
"""schema 插件协议。

一个「凭证 schema」= 一套字段知识 + 一套版式。它必须能被**按需调用**，
而不是长在转换流程里（Phase 3.2 的核心目标）。

三层职责
--------
- ``detect``  只回答「这页像不像我」。允许恒真（把判断推给 ``extract``），
              因为有些类型（发票）的判定本身就是抽取过程的一部分。
- ``extract`` 真正抽字段；返回 ``None`` 表示「不是我的类型 / 抽不出」。
- ``render_*`` 把抽出的 dict 变回人可读的形态。**渲染不重新看原图**，
              所以同一份 dict 在 GUI 与 Skill 两侧必然一致。

为什么要 ``render_md`` / ``render_txt`` / ``render_html`` 三个而不是一个：
md/docx 走 markdown、txt 走制表符、html 走版式还原，三者的排版约束完全不同。
硬塞成一个函数只会得到三处 ``if fmt ==``。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, Sequence, runtime_checkable

# 一页的输入上下文：文本 +（可选）OCR 行框与图像尺寸。
#   - text   判别式/VLM 得到的正文（必有）
#   - lines  RapidOCR 行框 [{"text","x0","y0","x1","y1"}, ...]，可能为空
#   - width / height 原图像素尺寸；无坐标时为 0
Line = Dict[str, Any]
Lines = Sequence[Line]

Doc = Dict[str, Any]


@runtime_checkable
class SchemaProvider(Protocol):
    """凭证 schema 插件。实现者可以是内置 Python 包，也可以是声明式 yaml。"""

    #: 唯一标识，用于 ``--schema <id>`` 与产物溯源。建议小写下划线。
    id: str
    #: 人可读名称（GUI 下拉里显示的就是它）。
    title: str
    #: 版本号；产物会记录它，便于解释「同一批文件两次导出为何不同」。
    version: str
    #: 来源：``builtin``（随包分发）/ ``user``（用户目录）/ ``declarative``（yaml）。
    kind: str
    #: 小者优先。内置发票取 100（它的 detect 恒真，必须最后兜底）。
    priority: int
    #: 可选：版式文件名（如 ``layout.html``）。无版式时为 None。
    layout: Optional[str]

    def detect(self, text: str, lines: Lines, width: int, height: int) -> bool:
        """这页是否属于本类型。可以恒真（把判断推给 extract）。"""
        ...

    def extract(self, text: str, lines: Lines, width: int, height: int) -> Optional[Doc]:
        """抽取字段。返回 None 表示未命中。"""
        ...

    def render_md(self, doc: Doc) -> str:
        ...

    def render_txt(self, doc: Doc) -> str:
        ...

    def render_html(self, doc: Doc) -> str:
        """版式化 HTML 片段（不含 <html>/<style>）。无版式时可退化为表格。"""
        ...

    def css(self) -> str:
        """该 schema 需要的附加 CSS（拼进导出 html 的 <style>）。无则返回 ""。"""
        ...


class SchemaInfo(dict):
    """``list_schemas()`` 返回的轻量描述（刻意用 dict，便于直接进 JSON / GUI）。"""


def info_of(p: SchemaProvider) -> SchemaInfo:
    return SchemaInfo(
        id=p.id,
        title=p.title,
        version=p.version,
        kind=p.kind,
        priority=getattr(p, "priority", 50),
        layout=getattr(p, "layout", None),
    )
