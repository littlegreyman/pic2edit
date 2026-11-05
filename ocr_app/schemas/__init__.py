# -*- coding: utf-8 -*-
"""凭证 schema 子系统（消费侧）：发现、选择、抽取、渲染。

**本包只回答「怎么用 schema」，不回答「怎么造 schema」。**

- 消费侧（本包）：目录发现 → 三态选择（none / auto / \\<id\\>）→ 按页抽取 → 按格式渲染
- 生产侧（``runtime`` / ``lint`` / ``cli``，Phase 4）：把 yaml + layout.html
  变成可运行的 schema 包

两边的唯一契约是 ``manifest.json`` + ``schema.yaml`` + ``layout.html`` 这三个文件，
转换流程永远不需要知道具体有哪些凭证类型。

三态选择的语义（GUI 下拉 / ``--schema`` 共用同一套）
---------------------------------------------------
- ``none``：**完全不启用**。连 ``schema`` 键都不写进 page，下游据此区分
  「没启用」与「启用了但没命中」。这是"没有 schema 的世界"的回归基线。
- ``auto``：按 priority 依次试 ``detect``，第一个抽得出字段的胜出。
- ``<id>``：只用指定 schema；未命中时告警但不中断。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .base import SchemaProvider, info_of
from .registry import MODE_AUTO, MODE_NONE, SchemaRegistry

__all__ = [
    "SchemaProvider",
    "SchemaRegistry",
    "MODE_NONE",
    "MODE_AUTO",
    "get_registry",
    "reset_registry",
    "list_schemas",
    "resolve_schema",
    "apply_schemas",
    "render_schema",
    "schema_css",
]

_registry: Optional[SchemaRegistry] = None


def get_registry() -> SchemaRegistry:
    global _registry
    if _registry is None:
        _registry = SchemaRegistry()
    return _registry.ensure()


def reset_registry(extra_dirs: Optional[Sequence[str]] = None) -> SchemaRegistry:
    """丢弃缓存后重扫（测试与用户新增 schema 后刷新用）。"""
    global _registry
    _registry = SchemaRegistry(extra_dirs)
    return _registry.ensure()


def list_schemas() -> List[Dict[str, Any]]:
    """可用 schema 描述列表（GUI 下拉 / ``schema list`` 用）。"""
    return get_registry().infos()


def resolve_schema(mode: str) -> Any:
    return get_registry().resolve(mode)


def apply_schemas(pages: Sequence[Dict[str, Any]], mode: str = MODE_AUTO) -> Dict[str, Any]:
    """就地为每页写 ``schema`` / ``schema_id``，返回统计。"""
    return get_registry().apply(pages, mode)


def render_schema(page: Dict[str, Any], fmt: str) -> str:
    """按页命中的 schema 渲染；未命中返回 ""（调用方负责兜底）。"""
    return get_registry().render(page, fmt)


def schema_css(pages: Sequence[Dict[str, Any]]) -> str:
    return get_registry().css_for(pages)
