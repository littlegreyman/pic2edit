"""Transformers 后端：连接本地 transformers_server.py（OpenAI 兼容 API）做离线 OCR。

服务由 run_transformers_server.bat / .py 启动（独立 Python 3.11 环境加载模型，
常驻显存），本后端仅作为 OpenAI 兼容客户端发送图片（base64 data URL）+ 提示词，
与 LlamaCppBackend 同构，区别只在默认端口（8001）。
"""
import base64

import requests

from .base import OCRBackend
from utils.logging_setup import get_logger

_logger = get_logger("backends.transformers")


class TransformersBackend(OCRBackend):
    name = "transformers"

    def __init__(self, base_url: str = "http://localhost:8001", model: str = "deepseek-ocr-local"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def available(self) -> bool:
        try:
            r = requests.get(self.base_url + "/health", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def ocr_image(self, image_path: str, prompt: str) -> str:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        # The Transformers path calls model.infer(prompt=...), which requires the
        # literal <image> placeholder at the start of the prompt to bind the image.
        # Ollama/llama.cpp inject the image marker themselves, so only here do we add it.
        tf_prompt = "<image>\n" + prompt.strip()
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": tf_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 4096,
        }
        url = self.base_url + "/v1/chat/completions"
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
            _logger.error(
                "transformers request failed: url=%s status=%s body=%r",
                url, status, body,
            )
            raise
        return r.json()["choices"][0]["message"]["content"].strip()
