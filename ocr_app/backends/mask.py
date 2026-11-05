# -*- coding: utf-8 -*-
"""密码区（防伪底纹）定位与掩膜。

为什么这项能力要下沉到内核：
掩膜必须发生在 **OCR 之前**，所以它是流水线的预处理能力，而不是某个前端的
私有技巧。此前只有 skill 有这套"找竖排『密码区』标签 → 取右侧褐色框 → 用区域内
文字是否为乱码来验证"的逻辑，GUI 完全没有——同一个需求被实现了零次或一次，
取决于你从哪个入口进来。

安全约束（照抄自原实现，不要改）：
只有增值税**专用**发票有密码区，普通发票/电子发票/数电票都没有。
因此**默认不掩膜**：盲掩会把正常正文整块涂白，比底纹噪声危害大得多。
定位失败时退回增值税通用预设矩形，而不是"随便涂一块"。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Sequence, Tuple

# 增值税专用/普通发票密码区通用预设（归一化坐标 x0, y0, x1, y1）
DEFAULT_VAT_RECT: Tuple[float, float, float, float] = (0.50, 0.20, 0.95, 0.44)

Rect = Tuple[float, float, float, float]


def is_gibberish(text: str) -> bool:
    """密码区字符的特征：多为数字/特殊符号，基本没有汉字。"""
    if not text or len(text.strip()) < 4:
        return False
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return cjk == 0


def find_password_label(lines: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """找『密码区』竖排标签。支持整词识别或单字竖排堆叠两种情形。"""
    if not lines:
        return None
    exact = next((ln for ln in lines if "密码区" in (ln.get("text") or "").replace(" ", "")), None)
    if exact:
        return exact
    # 单字竖排：在偏右上区域寻找 密/码/区 字符，按纵向堆叠定位
    cand = [ln for ln in lines
            if len((ln.get("text") or "").replace(" ", "")) == 1
            and (ln.get("text") or "").strip() in "密码区"]
    if len(cand) >= 2:
        return {"x1": max(c["x1"] for c in cand),
                "y0": min(c["y0"] for c in cand),
                "y1": max(c["y1"] for c in cand)}
    # 含 密码 / 码区 的片段
    for ln in lines:
        t = (ln.get("text") or "").replace(" ", "")
        if "密码" in t or "码区" in t:
            return ln
    return None


def _ocr_lines(image_path: str):
    """取带坐标的逐行结果；不可用时返回 (None, 0, 0)。"""
    from backends.discriminative_backend import DiscriminativeBackend

    try:
        backend = DiscriminativeBackend()
        if not backend.available():
            return None, 0, 0
        _text, data = backend.ocr_image_with_lines(image_path)
    except Exception:
        return None, 0, 0
    if not data:
        return None, 0, 0
    return data.get("lines") or [], data.get("img_w") or 0, data.get("img_h") or 0


def locate_password_region(image_path: str) -> Rect:
    """智能定位密码区，返回归一化矩形。

    步骤：先找『密码区』竖排标签 → 取其右侧褐色矩形 → 用区域内文字是否均为
    数字/符号乱码来验证 → 失败则回退增值税通用预设。
    """
    lines, W, H = _ocr_lines(image_path)
    if not (lines and W and H):
        return DEFAULT_VAT_RECT
    label = find_password_label(lines)
    if label is None:
        return DEFAULT_VAT_RECT
    lx1, ly0, ly1 = label["x1"], label["y0"], label["y1"]
    h = max(ly1 - ly0, 1)
    x0 = min(W * 0.97, lx1 + 0.002 * W)
    # 褐色密码框宽约为竖排标签高的 3~4 倍（实测噪声框右缘约在 lx1+3.8h 处）
    x1 = min(W * 0.965, lx1 + h * 3.8)
    y0 = max(0, ly0 - 0.15 * h)
    # 不越过下方「税率/金额」表头，避免误伤货物明细表
    y1 = min(H * 0.405, ly1 + 0.15 * h)
    cx0, cy0, cx1, cy1 = x0 / W, y0 / H, x1 / W, y1 / H
    region = [ln.get("text") or "" for ln in lines
              if cx0 <= (ln["x0"] + ln["x1"]) / 2 / W <= cx1
              and cy0 <= (ln["y0"] + ln["y1"]) / 2 / H <= cy1]
    if region and sum(is_gibberish(t) for t in region) / len(region) >= 0.5:
        return (cx0, cy0, cx1, cy1)
    return DEFAULT_VAT_RECT


def parse_mask_rect(text: str) -> Optional[Rect]:
    """解析 ``PIC2EDIT_MASK_RECT``（``x0,y0,x1,y1`` 归一化比例）；非法/缺失返回 None。"""
    if not text:
        return None
    try:
        x0, y0, x1, y1 = [float(v) for v in text.split(",")]
    except Exception:
        return None
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        return None
    return (x0, y0, x1, y1)


def mask_copy(src: str, dst: str, rect: Rect) -> str:
    """把 src 中 rect（归一化）区域涂白后存为 dst（PNG），返回 dst。

    依赖 PIL；缺失时抛错由调用方兜底（宁可"不掩膜"也不要转换失败）。
    """
    from PIL import Image, ImageDraw

    im = Image.open(src).convert("RGB")
    W, H = im.size
    x0, y0, x1, y1 = rect
    box = (int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H))
    ImageDraw.Draw(im).rectangle(box, fill=(255, 255, 255))
    im.save(dst)
    return dst


def apply_password_mask(src: str, dst: Optional[str] = None, rect: Optional[Rect] = None) -> str:
    """一步完成"定位 + 涂白"。

    Args:
        src: 原图路径。
        dst: 输出路径；None 时在同目录生成 ``<stem>_masked.png``。
        rect: 显式矩形；None 时先尝试智能定位，再回退通用预设。

    Returns:
        掩膜后的图片路径。
    """
    if dst is None:
        stem, _ = os.path.splitext(src)
        dst = stem + "_masked.png"
    use = rect or locate_password_region(src)
    return mask_copy(src, dst, use)
