"""输入适配：把 jpg/jpeg/tif/tiff/pdf 统一拆成一系列待 OCR 的“图像项”。

- 图片：直接作为一个图像项。
- TIF/TIFF：多页/多帧逐帧拆成独立图像（Pillow ImageSequence）。
- PDF：
    * 整页渲染为高 DPI 图像（保证完整版面 OCR）；
    * 同时抽取内嵌的“图形/图片”作为额外图像项（满足“pdf 中的图形”需求）。
每个图像项落地为临时 PNG，便于预览与后端调用。
"""
import os

from PIL import Image, ImageSequence

try:
    import pymupdf as fitz  # PyMuPDF >= 1.23
except ImportError:  # 旧版本
    import fitz


class InputItem:
    def __init__(self, name: str, image_path: str, source: str):
        self.name = name          # 列表显示名，如 "doc.pdf - 第1页"
        self.image_path = image_path
        self.source = source      # 原始文件
        self.result = ""          # OCR 结果（Markdown）
        self.status = "待转换"     # 待转换 / 转换中 / 完成 / 失败


def _safe_name(base: str, n: int) -> str:
    return f"{base}_{n:03d}.png"


def prepare_image(path: str, work_dir: str) -> list:
    name = os.path.basename(path)
    dst = os.path.join(work_dir, _safe_name(os.path.splitext(name)[0], 1))
    with Image.open(path) as im:
        im.convert("RGB").save(dst, "PNG")
    return [InputItem(f"{name}", dst, path)]


def prepare_tif(path: str, work_dir: str) -> list:
    name = os.path.basename(path)
    base = os.path.splitext(name)[0]
    items = []
    n = 0
    with Image.open(path) as im:
        for frame in ImageSequence.Iterator(im):
            n += 1
            dst = os.path.join(work_dir, _safe_name(base, n))
            frame.convert("RGB").save(dst, "PNG")
            items.append(InputItem(f"{name} - 第{n}帧", dst, path))
    if not items:  # 单页兜底
        return prepare_image(path, work_dir)
    return items


def prepare_pdf(path: str, work_dir: str, dpi: int = 200) -> list:
    """PDF -> 整页渲染图像 + 内嵌图形抽取。"""
    name = os.path.basename(path)
    base = os.path.splitext(name)[0]
    items = []
    doc = fitz.open(path)
    # 1) 整页渲染
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        dst = os.path.join(work_dir, _safe_name(f"{base}_page", i + 1))
        pix.save(dst)
        items.append(InputItem(f"{name} - 第{i + 1}页", dst, path))
    # 2) 抽取内嵌图像（即“pdf 中的图形”）——只保留“大尺寸/独立”插图，
    #    跳过 logo / 装饰 / 页码类小图。这些小图被 VLM 读不到文字时会脑补成
    #    英文废话（章程 PDF 曾因此混入 ~35% 垃圾段），是必须修的 bug。
    img_index = 0
    for i, page in enumerate(doc):
        pw = page.rect.width * dpi / 72.0   # 渲染后页面像素宽
        ph = page.rect.height * dpi / 72.0  # 渲染后页面像素高
        page_area = pw * ph
        for img in page.get_images(full=True):
            img_index += 1
            try:
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:  # CMYK 转 RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                fig_area = pix.width * pix.height
                # 面积占比过小（装饰/logo/页码）或非独立小图 -> 跳过
                if fig_area / page_area < 0.30 or min(pix.width, pix.height) < 200:
                    continue
                dst = os.path.join(work_dir, _safe_name(f"{base}_fig", img_index))
                pix.save(dst)
                items.append(
                    InputItem(f"{name} - 第{i + 1}页图{img_index}", dst, path)
                )
            except Exception:
                continue
    doc.close()
    return items


def prepare_input(path: str, work_dir: str) -> list:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".tif", ".tiff"):
        return prepare_tif(path, work_dir)
    if ext == ".pdf":
        return prepare_pdf(path, work_dir)
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        return prepare_image(path, work_dir)
    raise ValueError(f"不支持的文件类型：{ext}")
