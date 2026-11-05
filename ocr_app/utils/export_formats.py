"""统一导出：把多页 OCR 结果（每页 Markdown 文本）落盘为 5 种交付类型。

与 pic2edit-ocr skill 的交付类型对齐：
- md  / txt ：装配后的可读文本（md 保留 Markdown 标记，txt 等价但按纯文本阅读）；
- docx     ：Markdown -> Word（每页之间插入真实分页符）；
- html     ：Markdown -> 独立可打开的 HTML（标题/表格/列表/分页按原意还原）；
- json     ：结构化 JSON（逐页 {index, name, text}），便于入库 / 二次处理。

本模块只依赖纯文本结果（InputItem.result），不引入 Qt，故可被 headless 测试直接覆盖。

凭证 schema（Phase 3.2）：页面上若命中了某个 schema（``InputItem.schema`` 非空），
该页的交付文本改为**严格按 schema 排版**（字段化 / 票面版式），而不是 OCR 原文。
未命中的页一切照旧——这是「有 schema 更好、没 schema 也能用」的落地方式。
"""
import json
import os
import re
from datetime import datetime
from typing import List

import markdown as _md_lib

from schemas import render_schema, schema_css

from .assemble import assemble_document
from .export_docx import save_text, md_pages_to_docx
from .faithful_html import faithful_css, page_with_image
from .html_safety import sanitize_html

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


def _page_dict(it) -> dict:
    """把 InputItem 投影成 schema 渲染需要的 page 字典。

    ``render_schema`` 只认这个形状（纯数据），这样 GUI 与 Skill 走的是**同一条**
    渲染路径，不会出现「同一张发票 GUI 一套、CLI 另一套」。
    """
    text = getattr(it, "result", "") or ""
    return {
        "schema": getattr(it, "schema", None),
        "schema_id": getattr(it, "schema_id", None),
        "schema_version": getattr(it, "schema_version", None),
        "text": text,
        "raw_text": text,
        "lines": getattr(it, "lines", None) or [],
        "img_w": getattr(it, "img_w", 0) or 0,
        "img_h": getattr(it, "img_h", 0) or 0,
    }


def _schema_versions(pages: List) -> dict:
    """``{schema_id: version}``：本次导出实际用到的 schema 及其版本。

    产物必须能回答「这份数据是按哪一版规则抽的」。只记 id 时，schema 从 1.0.0 改到
    1.1.0（例如补了一个字段）之后，历史产物无法解释两次导出为何不同。
    """
    out = {}
    for it in pages:
        sid = getattr(it, "schema_id", None)
        if sid and getattr(it, "schema", None):
            out[sid] = getattr(it, "schema_version", None) or ""
    return out


def _schema_stamp(pages: List) -> str:
    """``"invoice@1.0.0、asset_card@1.0.0"``；没有命中则返回空串。"""
    return "、".join(f"{k}@{v}" for k, v in sorted(_schema_versions(pages).items()) if v)


def item_text(it, fmt: str = "md") -> str:
    """单页交付文本：命中 schema 且渲染成功 → 用 schema 版式；否则原识别文本。"""
    if getattr(it, "schema", None):
        rendered = render_schema(_page_dict(it), fmt)
        if rendered:
            return rendered
    return getattr(it, "result", "") or ""


def _pages_data(pages: List) -> List[tuple]:
    """把 InputItem 列表展开为 [(index, name, text), ...]。

    text 已 strip；缺 result 的页给空串，交由各导出函数决定如何处理（空白页）。
    """
    out = []
    for i, it in enumerate(pages, 1):
        text = item_text(it, "md").strip()
        name = getattr(it, "name", f"第{i}页")
        out.append((i, name, text))
    return out


def export_markdown(pages: List, path: str):
    """导出为 Markdown（装配后的多页可读文本）。

    命中 schema 时在文首加一行 ``<!-- schema: id@version -->``：md 注释在渲染后不可见，
    但保留了「这份文件按哪一版规则抽取」的溯源信息。
    """
    body = assemble_document([item_text(it, "md") for it in pages])
    stamp = _schema_stamp(pages)
    if stamp:
        body = f"<!-- schema: {stamp} -->\n\n{body}"
    save_text(body, path)


def export_text(pages: List, path: str):
    """导出为纯文本（与 md 同源，保留分页标记供人/脚本阅读）。"""
    save_text(assemble_document([item_text(it, "txt") for it in pages]), path)


def export_docx(pages: List, path: str):
    """导出为 Word（每页之间插入真实分页符）。"""
    md_pages_to_docx([item_text(it, "md") for it in pages], path)


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
    """导出为独立 HTML：每页一段 <section>。

    三条分派，按保真度从高到低：

    1. 命中凭证 schema → 该 schema 的版式片段（如发票票面）；
    2. 无 schema 但有**原图 + 坐标** → 「原图垫底 + 透明文字层」（``page_with_image``）。
       这就是"可搜索 PDF"的做法，位置来自 OCR 而不是猜测；用户的三案例之②
       「只规定数据格式、复现尽量按来源版式」在 GUI 侧靠这条成立。
    3. 都没有 → Markdown 转 HTML 重排。

    此前只有 skill 侧接了第 2 档，GUI 一律走第 3 档，于是**同一份文件从两个入口
    导出的 html 长得不一样**，且重排会把版面推断错。

    所有分支的产物都过 :func:`sanitize_html`：识别文本是不可信输入，而
    ``markdown`` 默认保留原始 HTML 直通（``<script>``、``<img src=http://…>`` 会被
    原样写进交付文件，并在 GUI 预览时产生外连）。
    """
    blocks = []
    page_dicts = []
    for idx, it in enumerate(pages, 1):
        if not (getattr(it, "result", "") or "").strip():
            continue
        name = getattr(it, "name", f"第{idx}页")
        pd = _page_dict(it)
        page_dicts.append(pd)
        body = ""
        if getattr(it, "schema", None):
            body = sanitize_html(render_schema(pd, "html"))
        if not body:
            # 第 2 档：忠实版式。需要原图路径 + 行框 + 原图尺寸，缺一即返回 None。
            pd["image"] = getattr(it, "image_path", "") or ""
            body = page_with_image(pd, uid=f"p{idx}", title=f"第 {idx} 页") or ""
            body = sanitize_html(body)
        if not body:
            body = sanitize_html(_md_lib.markdown(item_text(it, "md"),
                                                  extensions=["tables", "fenced_code"]))
        blocks.append(
            f"<section class='page'>\n<h2 class='page-title'>第 {idx} 页 · {_esc_html(name)}</h2>\n{body}\n</section>"
        )
    if not blocks:
        blocks.append("<p>（无内容）</p>")
    extra_css = schema_css(page_dicts) + "\n" + faithful_css()
    stamp = _schema_stamp(pages)
    meta = f'<meta name="pic2edit-schema" content="{_esc_html(stamp)}">\n' if stamp else ""
    html = (
        "<!DOCTYPE html>\n<html lang='zh-CN'>\n<head>\n<meta charset='utf-8'>\n"
        + meta +
        "<title>识别结果</title>\n<style>" + _HTML_CSS + "\n" + extra_css + "</style>\n</head>\n<body>\n"
        + "\n".join(blocks) + "\n</body>\n</html>"
    )
    save_text(html, path)


def export_json(pages: List, source: str, path: str):
    """导出为结构化 JSON：逐页 {index, name, text[, schema]}，附来源与时间戳，便于入库。

    命中凭证 schema 的页额外带 ``schema_id`` / ``schema_version`` 与结构化字段——
    这正是 RAG / 资料库真正想要的部分（字段值可直接建索引），而不是一张图的 OCR 散文。

    ``source`` 只写**文件名**：完整路径会连目录结构与用户名一起外泄
    （``/home/alice/客户资料/2026/发票.pdf``），而票据恰恰是最敏感的一类。
    需要完整路径做审计的调用方可以自己在外面记，不要把它塞进对外交付物。
    """
    rows = []
    for idx, name, text in _pages_data(pages):
        row = {"index": idx, "name": name, "text": text}
        it = pages[idx - 1] if idx - 1 < len(pages) else None
        sid = getattr(it, "schema_id", None) if it is not None else None
        doc = getattr(it, "schema", None) if it is not None else None
        if sid and doc:
            row["schema_id"] = sid
            row["schema_version"] = getattr(it, "schema_version", None) or None
            row["schema"] = doc
        rows.append(row)
    versions = _schema_versions(pages)
    data = {
        "source": os.path.basename(source) if source else "",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "format": "json",
        "page_count": len(pages),
        "schema_pages": sum(1 for r in rows if r.get("schema_id")),
        # 顶层汇总：一份产物用了哪些 schema、各自哪一版。只记 id 时，schema 升版后
        # 无法解释"同一批文件两次导出为何不同"。
        "schema": versions,
        "pages": rows,
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
