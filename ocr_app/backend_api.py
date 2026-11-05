"""共享内核公共门面（facade）。

GUI 与 Skill 两个前端都**只应通过本模块**访问 OCR 后端能力，不要直接
``import backends.*`` 内部实现（内部后端可能随版本重构，门面保持稳定契约）。

导出：
- ``run`` / ``pipeline_run``：统一 OCR 流水线（判别式优先 + VLM 兜底）。
- ``RoutingPolicy``：路由策略（何时跳过判别式 / 是否启用幻觉守卫）。
- ``EngineLauncher``：非 Qt 的引擎拉起器（进程组管理，避免孤儿进程）。
- ``ServerStarter``：GUI 专用的 Qt 引擎拉起线程（基于 EngineLauncher）。
- ``create_backend``：按名称构造兜底 VLM 后端。
- 各后端类与基类 ``OCRBackend``。
- 输入适配与导出（``prepare_input`` / ``InputItem`` / ``InputDocument``；
  ``save_text`` / ``md_to_docx`` / ``md_pages_to_docx``）。这些此前由前端
  直接 import ``utils.*`` 内部模块，现统一经门面，前端不再触碰内核内部路径。
- 表格能力（``kv_text_to_markdown_table`` / ``build_table_from_lines``）：
  GUI 与 Skill 共用同一套表格重建逻辑，避免两侧能力漂移。
- 引擎发现与命令行构造（``discover_engine`` / ``build_engine_command``）：
  封装 ``run_llamacpp_server`` 的 CLI 细节，前端只拿结果，不依赖其模块名。
- 凭证 schema（``list_schemas`` / ``apply_schemas`` / ``render_schema`` /
  ``schema_css``）：schema 的发现、选择、抽取、渲染。前端只知道
  「有三个态：none / auto / <id>」，不需要知道有哪些凭证类型，更不需要
  在代码里 import 它们——新增类型靠往目录里丢文件，不改前端。
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
from backends.routing import RoutingPolicy, vmem_limited_gb, env_force_skip
from backends.server_launcher import ServerStarter
from engine_launcher import EngineLauncher

# 输入适配与导出（原先由前端直连 utils.*，现收敛到门面）
from utils.input_prep import prepare_input, InputItem, InputDocument
from utils.export_docx import save_text, md_to_docx, md_pages_to_docx

# 表格能力（两门户同源）
from utils.table_utils import kv_text_to_markdown_table
from utils.table_layout import build_table_from_lines

# 凭证 schema（消费侧门面：发现 / 三态选择 / 抽取 / 渲染）
from schemas import (
    MODE_AUTO,
    MODE_NONE,
    SchemaProvider,
    apply_schemas,
    list_schemas,
    render_schema,
    reset_registry,
    resolve_schema,
    schema_css,
)

# 引擎 CLI 细节：仅供门面内部使用，对外只暴露下面两个函数
import run_llamacpp_server as _engine_cli


def discover_engine():
    """定位本机 llama-server 可执行文件与默认模型/投影文件。

    Returns:
        ``(exe, model, mmproj)``；缺失项为 ``None``。前端据此判断是否需要
        提示用户先部署引擎/模型，而无须了解 ``bin/`` ``models/`` 的布局细节。
    """
    return _engine_cli.discover()


def build_engine_command(exe, model, mmproj, model_params=None):
    """按 llama-server 的既有约定构造启动命令行（不含额外覆盖参数）。

    Args:
        exe: ``discover_engine()`` 返回的可执行文件路径。
        model: GGUF 主权重路径。
        mmproj: 多模态投影文件路径（可为 ``None``）。
        model_params: 采样参数；``None`` 时沿用引擎默认。

    Returns:
        可直接交给 ``EngineLauncher.start()`` 的命令列表。
    """
    return _engine_cli.build_command(exe, model, mmproj, model_params)

# 兼容旧调用名：部分前端习惯用 pipeline_run 指代统一流水线入口。
pipeline_run = run


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
    "pipeline_run",
    "RoutingPolicy",
    "vmem_limited_gb",
    "env_force_skip",
    "EngineLauncher",
    "ServerStarter",
    "create_backend",
    "OCRBackend",
    "OllamaBackend",
    "TransformersBackend",
    "LlamaCppBackend",
    "DiscriminativeBackend",
    "TableRecognizer",
    # 输入适配 / 导出
    "prepare_input",
    "InputItem",
    "InputDocument",
    "save_text",
    "md_to_docx",
    "md_pages_to_docx",
    # 表格
    "kv_text_to_markdown_table",
    "build_table_from_lines",
    # 引擎 CLI 适配
    "discover_engine",
    "build_engine_command",
    # 凭证 schema
    "MODE_NONE",
    "MODE_AUTO",
    "SchemaProvider",
    "list_schemas",
    "resolve_schema",
    "apply_schemas",
    "render_schema",
    "schema_css",
    "reset_registry",
]
