"""统一 OCR 流水线：判别式优先 + VLM 兜底 + 版面感知 + 表格结构化。

取代原先“每个 InputItem 直接丢给 VLM”的做法，从根上消除生成式幻觉：
- 干净印刷体 -> RapidOCR（零幻觉，纯 CPU 即可，不依赖 GPU 引擎）
- 判别式失败/空/低置信 -> DeepSeek-OCR（需 llama-server 在线，作为兜底）
- 表格区域（mode=table 且表格识别依赖可用）-> pix2text-table-rec

关键收益：即使不拉起 llama-server，也能产出可编辑文件（只是“智能页”质量下降），
与方案文档 §5 一致。
"""
import os
import sys
import re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from utils.input_prep import InputItem
from backends.discriminative_backend import DiscriminativeBackend

ZH_PROMPT = ("<|grounding|>请逐字识别图中的中文文本，保留原意与版面，只输出原文对应的 "
             "Markdown，不要翻译、不要改写、不要补充英文。")


def _vlm_backend(url, model):
    try:
        from backends.llamacpp_backend import LlamaCppBackend
        b = LlamaCppBackend(url, model)
        return b if b.available() else None
    except Exception:
        return None


def _quality_ok(md):
    if not md or len(md.strip()) < 5:
        return False
    han = sum(1 for c in md if '\u4e00' <= c <= '\u9fff')
    lat = sum(1 for c in md if c.isascii() and c.isalpha())
    return (han + lat) >= 5


def run(items, server_url="http://localhost:8080", model="deepseek-ocr",
        use_vlm_fallback=True, mode="markdown"):
    """填充每个 InputItem.result（Markdown）。返回 items。"""
    disc = None
    try:
        disc = DiscriminativeBackend()
    except Exception:
        disc = None

    # 表格识别（阶段4，可选依赖）
    table_rec = None
    if mode == "table":
        try:
            from backends.table_backend import TableRecognizer
            tr = TableRecognizer()
            table_rec = tr if tr.available() else None
        except Exception:
            table_rec = None

    vlm = None
    if use_vlm_fallback:
        vlm = _vlm_backend(server_url, model)

    for it in items:
        md = ""
        # 表格模式：优先表格识别
        if table_rec is not None:
            try:
                t = table_rec.recognize(it.image_path)
                if t and t.strip():
                    md = t
            except Exception:
                md = ""
        # 判别式优先
        if not _quality_ok(md) and disc is not None and disc.available():
            md = disc.ocr_image(it.image_path)
        # VLM 兜底
        if not _quality_ok(md) and vlm is not None:
            try:
                md = vlm.ocr_image(it.image_path, ZH_PROMPT)
            except Exception:
                md = md or ""
        it.result = md
        it.status = "完成" if md else "失败"
    return items
