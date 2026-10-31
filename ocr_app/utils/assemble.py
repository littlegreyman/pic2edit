"""多页文档装配：把若干页 OCR 结果按页序拼成一个整文档（Markdown / 文本）。

- 对于 Markdown / 文本交付，用分隔线 + 页码注释标记每页边界；
- 对于 Word 交付，分页符在 export_docx.md_pages_to_docx 中以 add_page_break 实现，
  本模块只负责纯文本的装配。
"""
from typing import List

# Markdown / 文本 分页分隔符：标准 Markdown 主题分隔线（预览中会渲染成横线），
# 前后留空行，并被表格/标题解析安全隔离。
PAGE_SEP = "\n\n---\n\n"


def _page_text(page) -> str:
    """从一页对象（InputItem 或纯字符串）取出文本。"""
    if hasattr(page, "result"):
        return (page.result or "").strip()
    return (page or "").strip()


def assemble_document(pages: List, sep: str = None) -> str:
    """把多页结果按页序拼接为一个整文档文本。

    pages: list[InputItem] 或 list[str]。
    每页之间插入分隔符；空页也保留“第 N 页”占位，便于对照原文档页数。
    返回拼接后的字符串。
    """
    sep = sep if sep is not None else PAGE_SEP
    blocks = []
    for i, page in enumerate(pages, 1):
        text = _page_text(page)
        if text:
            blocks.append(f"<!-- 第 {i} 页 -->\n{text}")
        else:
            blocks.append(f"<!-- 第 {i} 页（无内容）-->")
    return sep.join(blocks)
