"""OCR 后端抽象接口。"""
from abc import ABC, abstractmethod


class OCRBackend(ABC):
    name: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """后端是否可用（Ollama 服务在线 / Transformers 解释器与模型存在）。"""
        ...

    @abstractmethod
    def ocr_image(self, image_path: str, prompt: str) -> str:
        """对单张图像做 OCR，返回 Markdown 文本。"""
        ...

    def ocr_pages(self, image_paths, prompt: str):
        """对多张图像（如 PDF 各页）依次 OCR，返回 [(path, text), ...]。"""
        results = []
        for p in image_paths:
            try:
                text = self.ocr_image(p, prompt)
            except Exception as e:  # 单页失败不影响其余
                text = f"[OCR 失败] {e}"
            results.append((p, text))
        return results
