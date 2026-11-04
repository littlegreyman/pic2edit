"""共享内核公共门面（facade）。

GUI 与 Skill 两个前端都**只应通过本模块**访问 OCR 后端能力，不要直接
``import backends.*`` 内部实现（内部后端可能随版本重构，门面保持稳定契约）。

导出：
- ``run``：统一 OCR 流水线（判别式优先 + VLM 兜底）。
- ``RoutingPolicy``：路由策略（何时跳过判别式 / 是否启用幻觉守卫）。
- ``EngineLauncher``：非 Qt 的引擎拉起器（进程组管理，避免孤儿进程）。
- ``create_backend``：按名称构造兜底 VLM 后端。
- 各后端类与基类 ``OCRBackend``。
"""
from __future__ import annotations

from backends.ocr_pipeline import run
from backends import (
    OCRBackend,
    OllamaBackend,
    TransformersBackend,
    LlamaCppBackend,
    DiscriminativeBackend,
    TableRecognizer,
)
from backends.routing import RoutingPolicy
from engine_launcher import EngineLauncher


def create_backend(name: str, *, url: str, model: "str | None" = None, model_params=None):
    """按名称构造一个兜底 VLM 后端实例。

    name ∈ {ollama, llama_cpp, transformers}；discriminative（RapidOCR）是流水线
    内部默认，通常不在此处显式构造。
    """
    if name == "ollama":
        return OllamaBackend(url, model or "deepseek-ocr")
    if name == "llama_cpp":
        return LlamaCppBackend(url, model or "deepseek-ocr", model_params)
    if name == "transformers":
        return TransformersBackend(url, model or "deepseek-ocr-local")
    raise ValueError(f"unknown backend: {name!r}")


__all__ = [
    "run",
    "RoutingPolicy",
    "EngineLauncher",
    "create_backend",
    "OCRBackend",
    "OllamaBackend",
    "TransformersBackend",
    "LlamaCppBackend",
    "DiscriminativeBackend",
    "TableRecognizer",
]
