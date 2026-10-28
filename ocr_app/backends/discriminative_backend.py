"""判别式 OCR 后端（RapidOCR）—— 默认引擎，DeepSeek-OCR 仅作兜底。

通过子进程调用独立 venv（Python 3.13）中的 _rapidocr_cli.py，
避免把 onnxruntime 等重型依赖塞进主 3.14 venv，也规避 3.14 wheel 可能缺失。
"""
import os
import sys
import json
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OCR_VENV = os.path.join(HERE, "..", ".venv_ocr")
CLI = os.path.join(HERE, "..", "_rapidocr_cli.py")


def _venv_python():
    cand = os.path.join(OCR_VENV, "bin", "python")
    return cand if os.path.exists(cand) else None


class DiscriminativeBackend:
    name = "rapidocr"

    def __init__(self):
        self._py = _venv_python()
        if self._py is None:
            raise RuntimeError(
                "判别式 OCR 子环境缺失：请在 ocr_app 下创建 .venv_ocr(Python3.13) "
                "并 pip install rapidocr_onnxruntime"
            )

    def available(self) -> bool:
        return self._py is not None

    def _raw(self, image_path):
        out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        out.close()
        try:
            r = subprocess.run([self._py, CLI, image_path, out.name],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return None
            with open(out.name, encoding="utf-8") as f:
                return json.load(f)
        finally:
            try:
                os.unlink(out.name)
            except Exception:
                pass

    def ocr_image(self, image_path, prompt=None) -> str:
        """返回 Markdown 文本（阅读顺序拼接，已剔除页眉/页脚/页码）。识别失败返回空串。"""
        data = self._raw(image_path)
        if not data or "error" in data:
            return ""
        lines = data.get("lines") or []
        if not lines:
            return ""
        H = data.get("img_h") or 0
        # 版面感知（阶段3）：剔除页眉(<4%H)、页脚(>96%H) 与纯页码行，避免噪声串入
        keep = []
        for ln in lines:
            cy = (ln["y0"] + ln["y1"]) / 2.0
            if H and (cy < 0.04 * H or cy > 0.96 * H):
                continue
            if re.fullmatch(r"\s*\d+\s*/\s*\d+\s*", ln["text"]):
                continue
            keep.append(ln)
        keep.sort(key=lambda l: (round(l["y0"]), l["x0"]))
        return "\n".join(l["text"] for l in keep)
