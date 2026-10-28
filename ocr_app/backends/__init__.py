"""OCR 后端包。提供统一的 OCRBackend 接口与两个实现：
- OllamaBackend：调用本机 Ollama 的 deepseek-ocr GGUF 模型（推荐，离线、适配 8GB 显存）。
- TransformersBackend：通过子进程调用 transformers_infer.py，使用本地 HF 模型（可选 4/8bit 量化）。
"""
from .base import OCRBackend
from .ollama_backend import OllamaBackend
from .transformers_backend import TransformersBackend
from .llamacpp_backend import LlamaCppBackend
from .discriminative_backend import DiscriminativeBackend
from .table_backend import TableRecognizer

__all__ = ["OCRBackend", "OllamaBackend", "TransformersBackend", "LlamaCppBackend",
           "DiscriminativeBackend", "TableRecognizer"]
