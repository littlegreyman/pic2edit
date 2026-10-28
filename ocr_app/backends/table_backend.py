"""可选表格识别后端（阶段4）。依赖 pix2text-table-rec（需另行安装，较重）。

若未安装或导入失败，available() 返回 False，流水线自动跳过、回退判别式/VLM。
注：pix2text-table-rec 的具体 API 随版本变化，recognize() 返回表格的 HTML/LaTeX
文本；部署到桌面后需用真实表格图验证一次。
"""
import os
import sys


class TableRecognizer:
    name = "pix2text-table-rec"

    def __init__(self):
        self._mod = None
        try:
            from pix2text_table_rec import Pix2TextTableRec
            self._mod = Pix2TextTableRec
        except Exception:
            self._mod = None

    def available(self):
        return self._mod is not None

    def recognize(self, image_path):
        if self._mod is None:
            raise RuntimeError("pix2text-table-rec 未安装（请在 .venv_ocr 中 pip install）")
        engine = self._mod()
        out = engine.recognize(image_path)
        if isinstance(out, dict):
            out = out.get("text") or out.get("html") or out.get("latex") or ""
        return str(out)
