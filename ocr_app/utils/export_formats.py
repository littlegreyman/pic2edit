"""统一导出：把多页 OCR 结果（每页 Markdown 文本）落盘为 5 种交付类型。

与 pic2edit-ocr skill 的交付类型对齐：
- md  / txt ：装配后的可读文本（md 保留 Markdown 标记，txt 等价但按纯文本阅读）；
- docx     ：Markdown -> Word（每页之间插入真实分页符）；
- html     ：Markdown -> 独立可打开的 HTML（标题/表格/列表/分页按原意还原）；
- json     ：结构化 JSON（逐页 {index, name, text}），便于入库 / 二次处理。

本模块只依赖纯文本结果（InputItem.result），不引入 Qt，故可被 headless 测试直接覆盖。
"""
import json
import os
import re
from datetime import datetime
from typing import List

import markdown as _md_lib

from .assemble import assemble_document
from .export_docx import save_text, md_pages_to_docx

# 导出对话框的过滤器顺序（与 GUI 保持一致）
FILTERS = "Word 文档 (*.docx);;Markdown (*.md);;文本 (*.txt);;HTML (*.html);;JSON (*.json)"

# 导出类型 -> 文件扩展名
EXT_BY_FORMAT = {
    "docx": "docx",
    "md": "md",
    "txt": "txt",
    "html": "html",
    "json": "json",
}

_FILTER_RE = re.compile(r"\*\.([A-Za-z0-9]+)")


def ext_from_filter(filt: str) -> str:
    """从 QFileDialog 返回过滤器串（如 ``"Word 文档 (*.docx)"``）解析出小写扩展名。

    解析失败或未提供时返回空串，调用方据此回退到路径已有的扩展名或默认 md。
    """
    if not filt:
        return ""
    m = _FILTER_RE.search(filt)
    return m.group(1).lower() if m else ""


def _pages_data(pages: List) -> List[tuple]:
    """把 InputItem 列表展开为 [(index, name, text), ...]。

    text 已 strip；缺 result 的页给空串，交由各导出函数决定如何处理（空白页）。
    """
    out = []
    for i, it in enumerate(pages, 1):
        text = (getattr(it, "result", "") or "").strip()
        name = getattr(it, "name", f"第{i}页")
        out.append((i, name, text))
    return out


def export_markdown(pages: List, path: str):
    """导出为 Markdown（装配后的多页可读文本）。"""
    save_text(assemble_document(pages), path)


def export_text(pages: List, path: str):
    """导出为纯文本（与 md 同源，保留分页标记供人/脚本阅读）。"""
    save_text(assemble_document(pages), path)


def export_docx(pages: List, path: str):
    """导出为 Word（每页之间插入真实分页符）。"""
    md_pages_to_docx([(getattr(it, "result", "") or "") for it in pages], path)


# ---- HTML 导出：复用已安装的 markdown 库，保证表格/列表/标题等忠实还原 ----
_HTML_CSS = (
    "body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
    "margin:24px;color:#222;line-height:1.6}"
    ".page{border:1px solid #ddd;border-radius:8px;padding:8px 16px;margin:16px 0}"
    ".page-title{margin:8px 0 4px;font-size:1.05em;color:#444}"
    "table{border-collapse:collapse;margin:8px 0;width:100%}"
    "td,th{border:1px solid #bbb;padding:4px 8px;text-align:left;vertical-align:top}"
    "th{background:#f3f3f3}"
    "pre{white-space:pre-wrap;font-family:monospace}"
)


def _esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def export_html(pages: List, path: str):
    """导出为独立 HTML：每页一段 <section>，页内 Markdown 经 markdown 库转 HTML。

    表格走 markdown 的 ``tables`` 扩展、围栏代码走 ``fenced_code``，最大限度保留版面。
    """
    blocks = []
    for idx, name, text in _pages_data(pages):
        if not text:
            continue
        body = _md_lib.markdown(text, extensions=["tables", "fenced_code"])
        blocks.append(
            f"<section class='page'>\n<h2 class='page-title'>第 {idx} 页 · {_esc_html(name)}</h2>\n{body}\n</section>"
        )
    if not blocks:
        blocks.append("<p>（无内容）</p>")
    html = (
        "<!DOCTYPE html>\n<html lang='zh-CN'>\n<head>\n<meta charset='utf-8'>\n"
        "<title>识别结果</title>\n<style>" + _HTML_CSS + "</style>\n</head>\n<body>\n"
        + "\n".join(blocks) + "\n</body>\n</html>"
    )
    save_text(html, path)


def export_json(pages: List, source: str, path: str):
    """导出为结构化 JSON：逐页 {index, name, text}，附来源与时间戳，便于入库。"""
    data = {
        "source": source or "",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "format": "json",
        "page_count": len(pages),
        "pages": [
            {"index": idx, "name": name, "text": text}
            for idx, name, text in _pages_data(pages)
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def export_by_ext(pages: List, path: str, ext: str, source: str = ""):
    """按扩展名分派到对应导出函数（ext 为小写，不含点）。

    pages：InputItem 列表；path：目标文件；source：源文件路径（仅 json 使用）。
    """
    ext = (ext or "").lower()
    if ext == "docx":
        export_docx(pages, path)
    elif ext == "html":
        export_html(pages, path)
    elif ext == "json":
        export_json(pages, source, path)
    elif ext == "txt":
        export_text(pages, path)
    else:  # md 及未知类型统一按 markdown 处理
        export_markdown(pages, path)
