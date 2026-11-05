# -*- coding: utf-8 -*-
""""原图垫底 + 透明文字层"的忠实版式渲染。

用途
----
有些标准只规定**数据**、不给**版式**（例如 CIPA 的审计数据规范系列）。这类文档
要求"尽量按来源文件的版式复原"，此时正确的做法不是用 HTML 重排一遍——重排要推断
段落、字号、行距、表格结构，任何一处判定错就是错版——而是：

    把扫描页当背景图，把 OCR 已识别出的文字按**原坐标**透明地叠上去。

保真度接近 100%，因为位置不是猜的，是 OCR 给的；文字仍然可选中、可复制、可搜索，
本质上就是"可搜索 PDF"的做法。这也是本模块与 ``schemas/*/layout.html`` 的分工：
后者是**设计好的版式**（有官方式样或自定义模板），本模块是**沿用原件的版式**。

局限（必须写清楚，否则会被误用）
--------------------------------
- 只有坐标没有语义：不会识别"这是表格的第几列"，也不合并单元格；
- 依赖原图：导出的 HTML 会内嵌图片（base64），文件因此变大（约原图 1.33 倍）；
- 舞台按原图像素固定尺寸，页面很宽时由外层横向滚动，不做自适应缩放。
"""
from __future__ import annotations

import base64
import html as _html
import os
from typing import Any, Dict, Optional, Sequence

#: 内嵌图片的大小上限。超过就只留一个相对路径，避免导出一个几十 MB 的 HTML。
MAX_EMBED_BYTES = 12 * 1024 * 1024

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

_OVERLAY_CSS = """
.fx-page { margin: 0 0 18px; }
.fx-page > .fx-bar { font-size: 12px; color: #666; margin-bottom: 4px;
  display: flex; gap: 10px; align-items: center; }
.fx-toggle { margin: 0; }
.fx-label { user-select: none; cursor: pointer; }
.fx-scroll { overflow-x: auto; border: 1px solid #e0e0e0; background: #fff; }
.fx-stage { position: relative; }
.fx-stage > img { position: absolute; left: 0; top: 0; width: 100%; height: 100%; }
.fx-stage .t { position: absolute; white-space: pre; color: transparent;
  overflow: visible; }
.fx-toggle:checked ~ .fx-scroll .t { color: #c0392b; background: rgba(255, 255, 255, 0.78); }
"""


def embed_image(path: str, max_bytes: int = MAX_EMBED_BYTES) -> str:
    """把本地图片转成 data URI。读不到或过大时返回 ""（调用方退回不垫底）。"""
    if not path or not os.path.isfile(path):
        return ""
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            return ""
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    mime = _MIME.get(os.path.splitext(path)[1].lower(), "image/png")
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def build_overlay_html(lines: Sequence[Dict[str, Any]], img_w: int, img_h: int,
                       image_src: str = "", *, uid: str = "p1",
                       title: str = "", show_toggle: bool = True) -> str:
    """生成一页"垫底 + 文字层"的 HTML 片段。

    Args:
        lines: OCR 行框，每项含 ``text/x0/y0/x1/y1``。
        img_w/img_h: 原图像素尺寸；任一为 0 则无法定位，返回 ""。
        image_src: 已可用的图片地址（通常是 ``embed_image()`` 的结果）。
            为空时仍然输出文字层（白底透明，等于"看不到字"），因此**不推荐**；
            调用方应在这种情况下改用重排渲染。
        uid: 同一份导出里多页时用于区分复选框 id。
    """
    if not lines or not img_w or not img_h:
        return ""
    spans = []
    for ln in lines:
        txt = str(ln.get("text") or "")
        if not txt.strip():
            continue
        try:
            x0 = float(ln.get("x0", 0))
            y0 = float(ln.get("y0", 0))
            x1 = float(ln.get("x1", 0))
            y1 = float(ln.get("y1", 0))
        except (TypeError, ValueError):
            continue
        h = max(y1 - y0, 1.0)
        w = max(x1 - x0, 1.0)
        spans.append(
            f'<span class="t" style="left:{x0:.1f}px;top:{y0:.1f}px;'
            f'width:{w:.1f}px;height:{h:.1f}px;'
            f'font-size:{h * 0.82:.1f}px;line-height:{h:.1f}px">{_esc(txt)}</span>'
        )
    if not spans:
        return ""

    img = (f'<img src="{image_src}" alt="">' if image_src else "")
    bar = ""
    if show_toggle:
        tid = f"fx-{uid}"
        bar = (f'<div class="fx-bar"><input type="checkbox" class="fx-toggle" id="{tid}">'
               f'<label class="fx-label" for="{tid}">显示文字层（核对叠字位置）</label>'
               f'<span>{_esc(title)}</span></div>')
    return (
        f'<div class="fx-page">{bar}'
        f'<div class="fx-scroll"><div class="fx-stage" '
        f'style="width:{int(img_w)}px;height:{int(img_h)}px">{img}{"".join(spans)}</div></div>'
        f"</div>"
    )


def faithful_css() -> str:
    """配套样式；导出 HTML 时应拼进 ``<style>``。"""
    return _OVERLAY_CSS


def _esc(s: str) -> str:
    return _html.escape(s, quote=False)


def page_with_image(page: Dict[str, Any], *, uid: str = "p1",
                    title: str = "") -> Optional[str]:
    """便捷入口：从一个"页字典"直接产出片段（page 需含 image/lines/img_w/img_h）。"""
    src = page.get("image_data") or ""
    if not src and page.get("image"):
        src = embed_image(str(page["image"]))
    if not src:
        # 没有底图却输出文字层，等于给用户一个"白底看不见字"的页面。
        # 这种情况必须由调用方改走重排渲染，所以这里明确返回 None。
        return None
    out = build_overlay_html(page.get("lines") or [], int(page.get("img_w") or 0),
                             int(page.get("img_h") or 0), src, uid=uid, title=title)
    return out or None
