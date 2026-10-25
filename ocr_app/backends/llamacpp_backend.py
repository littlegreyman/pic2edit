"""llama.cpp 后端：连接本地 llama-server（OpenAI 兼容 API）做离线 OCR。

llama.cpp 直接跑量化 GGUF（如 Q4_K_M ~1.95GB / Q8_0 ~3.1GB），比 Ollama 默认的
6.7GB F16 轻很多，单文件推理引擎，最适合 8GB 显存。
启动 llama-server（**重点，已踩坑并修正**）：
    llama-server -m DeepSeek-OCR-Q4_K_M.gguf --mmproj mmproj-DeepSeek-OCR-Q4_K_M.gguf \
        --port 8080
    —— 不要加 --chat-template deepseek-ocr ！

关于 media marker（关键，已用真实服务端反复验证）：
DeepSeek-OCR 的 llama.cpp「原生模板」会**自动**把 media marker 注入到 prompt、
并把 image_url 图片块正确绑定到视觉编码器。因此**客户端只要发 image_url 块 + 提示词，
文本里不要写任何 marker**。实测（build b11853）：
    · image_url 块 + 无 marker           -> 200，返回真实 OCR 文本 ✓
    · image_url 块 + 任何 marker(含/props)-> 400 (2 markers vs 1 bitmap) ✗
    · 加了 --chat-template deepseek-ocr   -> 裸模板不自动注入也不绑视觉，必坏 ✗
所以：
  1) 服务端**绝不能**传 --chat-template deepseek-ocr（裸模板既不自动注入 marker、
     也不把 image_url 接到视觉塔，结果要么 400 要么只回通用废话）。让 llama.cpp
     自己按模型选原生 deepseek-ocr 模板即可。
  2) 客户端**不要**在文本里前置 `<image>` 或 GET /props 的 media_marker——
     那会让自动注入的构建出现“2 markers vs 1 bitmap”而 400。
  3) 服务端返回的 400 响应体是 {"error":{"message":"Failed to tokenize prompt"}}，
     是通用文案，不含任何 marker 线索，不能靠匹配它来判断/重试。
"""
import base64

import requests

from .base import OCRBackend
from utils.logging_setup import get_logger
from utils.text_utils import trim_trailing_repetition

_logger = get_logger("backends.llamacpp")


class LlamaCppBackend(OCRBackend):
    name = "llama_cpp"

    def __init__(self, base_url: str = "http://localhost:8080", model: str = "deepseek-ocr"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def available(self) -> bool:
        # llama_cpp.server (FastAPI) does not reliably expose /health across
        # versions; /v1/models is always present once the model is loaded, so
        # probe both and treat either 200 as "ready".
        for path in ("/health", "/v1/models"):
            try:
                r = requests.get(self.base_url + path, timeout=3)
                if r.status_code == 200:
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _build_messages(prompt: str, image_b64: str):
        return [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }]

    def _chat(self, messages):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            # 关键：DeepSeek-OCR 在密集页面 + 贪心解码下极易“复读”死循环
            # （把最后一行无限重复直到 max_tokens 耗尽）。repeat_penalty=1.0（默认）
            # 不惩罚重复，必触发。设 1.05 即可可靠止住复读且基本不影响 OCR 质量。
            # 即便服务端启动没带 --repeat-penalty，请求里带也能覆盖默认，立即生效。
            "repeat_penalty": 1.05,
            "max_tokens": 8192,
        }
        url = self.base_url + "/v1/chat/completions"
        try:
            r = requests.post(url, json=payload, timeout=600)
            r.raise_for_status()
            return self._unpack(r.json())
        except requests.HTTPError as e:
            resp = getattr(e, "response", None)
            status = resp.status_code if resp is not None else "?"
            body = ""
            try:
                body = (resp.text or "")[:500]
            except Exception:
                body = ""
            _logger.error(
                "llama.cpp request failed: url=%s status=%s body=%r",
                url, status, body,
            )
            raise

    @staticmethod
    def _unpack(data):
        """严格按 OpenAI chat.completion 返回包拆包：choices[0].message.content。

        返回包结构（HTTP 200）：
            {"choices":[{"message":{"content":"<文本>"}, ...}], ...}
        文本即模型输出的 Markdown/纯文本，无额外包裹层。
        """
        if not isinstance(data, dict):
            raise ValueError("llama.cpp response is not a JSON object")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"llama.cpp response missing 'choices': {str(data)[:200]}")
        first = choices[0]
        if not isinstance(first, dict):
            raise ValueError(f"llama.cpp response choice not an object: {str(first)[:200]}")
        message = first.get("message")
        if not isinstance(message, dict):
            raise ValueError(f"llama.cpp response missing 'message': {str(first)[:200]}")
        content = message.get("content")
        if content is None:
            raise ValueError(f"llama.cpp response missing 'content': {str(message)[:200]}")
        return content.strip()

    def ocr_image(self, image_path: str, prompt: str) -> str:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        # 关键：原生 deepseek-ocr 模板会“自动”注入 media marker 并把 image_url
        # 图片绑定到视觉编码器。所以**文本里不要写任何 marker**（不要 <image>，也
        # 不要 GET /props 的 media_marker）——多写一个就会变成“2 markers vs 1 bitmap”
        # 直接 400。这是用真实服务端（build b11853）反复验证过的结论。
        messages = self._build_messages(prompt, b64)
        raw = self._chat(messages)
        # DeepSeek-OCR 在密集页面 + 贪心解码下会陷入“复读”死循环（同一行/同一块
        # 无限重复直到 max_tokens 耗尽）。repeat_penalty 只能缓解、无法根治，所以这里
        # 再加一道客户端兜底：检测并裁掉尾部循环。实测 tempo_package.jpg 上能把 10273
        # 字符的复读结果裁成完整干净的 ~600 字符真实 OCR（真实内容用全角括号（20xx），
        # 循环体用方括号 [20xx]，首个方括号即循环起点）。
        return trim_trailing_repetition(raw)
