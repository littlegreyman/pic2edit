"""Ollama 后端：调用本机 Ollama 的 deepseek-ocr 模型做离线 OCR。

Ollama 在首次 `ollama pull deepseek-ocr` 后会把模型缓存在本地，
之后完全离线运行，且 4/8bit GGUF 量化后显存占用小（约 3–5GB），适配 8GB 显卡。
"""
import base64

import requests

from .base import OCRBackend
from utils.logging_setup import get_logger

_logger = get_logger("backends.ollama")


class OllamaBackend(OCRBackend):
    name = "ollama"

    def __init__(self, url: str = "http://localhost:11434", model: str = "deepseek-ocr"):
        self.url = url.rstrip("/")
        self.model = model

    def available(self) -> bool:
        try:
            r = requests.get(self.url + "/api/tags", timeout=3)
            if r.status_code != 200:
                return False
            # 检查目标模型是否已拉取
            models = [m.get("name", "") for m in r.json().get("models", [])]
            return any(self.model in m for m in models)
        except Exception:
            return False

    def ocr_image(self, image_path: str, prompt: str) -> str:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt, "images": [b64]}],
            "stream": False,
        }
        url = self.url + "/api/chat"
        try:
            r = requests.post(url, json=payload, timeout=600)
            r.raise_for_status()
        except requests.HTTPError as e:
            resp = getattr(e, "response", None)
            status = resp.status_code if resp is not None else "?"
            body = ""
            try:
                body = (resp.text or "")[:500]
            except Exception:
                body = ""
            _logger.error("ollama request failed: url=%s status=%s body=%r", url, status, body)
            raise
        content = r.json().get("message", {}).get("content", "")
        return content.strip()
