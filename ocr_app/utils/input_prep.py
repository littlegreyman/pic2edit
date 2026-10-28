"""输入适配：把 jpg/jpeg/tif/tiff/pdf 统一拆成一系列待 OCR 的“图像项”。

- 图片：直接作为一个图像项。
- TIF/TIFF：多页/多帧逐帧拆成独立图像（Pillow ImageSequence）。
- PDF：整页渲染为高 DPI 图像（保证完整版面 OCR）。

每个图像项落地为临时 PNG，便于预览与后端调用。

PDF 渲染使用 pypdfium2（Apache-2.0），不引入 PyMuPDF（AGPL-3.0）等
与 MIT 不兼容的依赖。历史上曾尝试“额外抽取每页内嵌图作为 OCR 项”，
但实践证明：扫描件里的插图/logo 无文字，判别式后端输出空、生成式后端
却会脑补英文垃圾（章程 PDF 曾因此混入 ~35% 幻觉段），故现已移除该分支，
仅保留整页渲染。
"""
import os

from PIL import Image, ImageSequence

try:
    import pypdfium2 as pdfium  # Apache-2.0，MIT 兼容
except ImportError:
    pdfium = None

PDF_AVAILABLE = pdfium is not None


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
    """PDF -> 整页渲染图像（每页一张高 DPI PNG）。

    使用 pypdfium2 渲染；不再抽取每页内嵌图（见模块 docstring 说明）。
    """
    if not PDF_AVAILABLE:
        raise RuntimeError(
            "未安装 pypdfium2，无法处理 PDF。请先 `pip install pypdfium2`。"
        )
    name = os.path.basename(path)
    base = os.path.splitext(name)[0]
    items = []
    doc = pdfium.PdfDocument(path)
    scale = dpi / 72.0  # pypdfium2 的 scale=1 对应 72 DPI
    for i in range(len(doc)):
        page = doc[i]
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        dst = os.path.join(work_dir, _safe_name(f"{base}_page", i + 1))
        pil.save(dst, "PNG")
        items.append(InputItem(f"{name} - 第{i + 1}页", dst, path))
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
