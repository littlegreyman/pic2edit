"""OCR 后端包（共享内核内部实现）。

暴露统一的 OCRBackend 接口与多实现：
- OllamaBackend：调用本机 Ollama 的 deepseek-ocr GGUF 模型（推荐，离线、适配 8GB 显存）。
- TransformersBackend：通过子进程调用 transformers_infer.py，使用本地 HF 模型（可选 4/8bit 量化）。
- LlamaCppBackend：本地 llama-server 的 OpenAI 兼容 API（deepseek-ocr GGUF）。
- DiscriminativeBackend：RapidOCR 判别式优先（零幻觉，纯 CPU，支持 Python 3.14）。
另含 TableRecognizer（表格识别，可选依赖）。

注意：本包为共享内核内部模块，前端（GUI / Skill）应只经由 ocr_app.backend_api
门面调用，勿直接 import 内部后端实现。
"""
from .base import OCRBackend
from .ollama_backend import OllamaBackend
from .transformers_backend import TransformersBackend
from .llamacpp_backend import LlamaCppBackend
from .discriminative_backend import DiscriminativeBackend
from .table_backend import TableRecognizer

__all__ = ["OCRBackend", "OllamaBackend", "TransformersBackend", "LlamaCppBackend",
           "DiscriminativeBackend", "TableRecognizer"]
