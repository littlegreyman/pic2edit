# -*- coding: utf-8 -*-
"""IR → schema 包（``manifest.json`` + ``schema.yaml`` + ``samples/``）。

三条不可动摇的输出约束（规格 §6.4 / §6.5 / §6.7）：

  * **不生成** ``layout.html``：国标只有数据模型，没有版式；
  * **不生成定位策略**：字段只有 ``key`` / ``label`` / ``locator: pending``，
    没有 ``regex`` / ``anchor`` / ``required``；``strict: false``；
  * **保守 detect + 最低优先级**：``detect.any`` 只放标准号，``priority: 900``。

产物 MUST NOT 写进 ``schemas/``（注册表扫描根）或项目根之外。两次相同输入
（含冻结时间）必须逐字节一致——所以 YAML 用 ``sort_keys=False`` 的固定插入顺序、
JSON 用固定键序，不把当前时间偷偷塞进文件。
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List

import yaml

from . import __version__
from .ir import GENERATED_PRIORITY, IR

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUILTIN_ROOT = os.path.join(_APP_ROOT, "schemas")


class EmitError(Exception):
    """输出路径非法或写入失败。MUST 停止，不得换个目录偷偷写。"""


def assert_out_dir(out_dir: str) -> str:
    """校验并返回绝对路径；拒绝一切可能污染仓库/注册表的位置。"""
    root = os.path.realpath(_APP_ROOT)
    path = os.path.realpath(os.path.abspath(os.path.expanduser(out_dir)))
    try:
        inside = os.path.commonpath([path, root]) == root
    except ValueError:                 # Windows 跨盘符
        inside = False
    if not inside or path == root:
        raise EmitError(f"拒绝写入项目根之外或项目根本身：{path}（根：{root}）")
    builtin = os.path.realpath(_BUILTIN_ROOT)
    if path == builtin or os.path.commonpath([path, builtin]) == builtin:
        raise EmitError(
            f"拒绝写入 {builtin}：那是注册表 BUILTIN_ROOT，放进去会被当成 schema 包装载")
    parts = path.split(os.sep)
    if ".git" in parts:
        raise EmitError(f"拒绝写入 .git 路径：{path}")
    if os.path.islink(out_dir):
        raise EmitError(f"拒绝写入符号链接目录：{out_dir}")
    return path


def safe_id(schema_id: str) -> str:
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_\-]{0,31}", schema_id or ""):
        raise EmitError("id 必须是 1–32 位、小写字母开头的 [a-z0-9_-]（与 schemas.cli 同规则）")
    return schema_id


def build_spec(ir: IR) -> Dict[str, Any]:
    """构造 ``schema.yaml`` 的内容（不落盘，便于测试直接断言）。"""
    sections: List[Dict[str, Any]] = []
    for sec in ir.sections:
        item: Dict[str, Any] = {
            "key": sec.key, "label": sec.label, "mode": sec.mode,
        }
        if sec.mode == "table":
            item["start"] = sec.start       # 保守 hint，找不到就返回空段
            item["limit"] = sec.limit
            item["columns"] = [{"key": f.key, "label": f.label,
                                "locator": f.locator} for f in sec.fields]
        else:
            item["fields"] = [{"key": f.key, "label": f.label,
                               "locator": f.locator} for f in sec.fields]
        sections.append(item)
    return {
        "id": ir.schema_id,
        "title": ir.title,
        "version": ir.version,
        "priority": GENERATED_PRIORITY,
        "strict": False,
        "detect": {"any": [ir.detect_hint], "none": []},
        "sections": sections,
    }


def build_manifest(ir: IR) -> Dict[str, Any]:
    """构造 ``manifest.json``；``x-*`` 键 MUST NOT 影响装载。"""
    return {
        "id": ir.schema_id,
        "title": ir.title,
        "version": ir.version,
        "kind": "user",
        "priority": GENERATED_PRIORITY,
        "schema": "schema.yaml",
        "x-provenance": ir.provenance(__version__),
    }


_SAMPLE_README = """# 样张说明（生成器产出）

本目录下的样张是**骨架文本，不是真实票面**。

生成包只描述数据格式（字段集、实体分段、类型线索），**没有定位策略**：
所有字段标为 `locator: pending`，`strict: false`，`detect` 用标准号作保守关键词。

因此 `schemas.cli test` 的 `hit_rate` 在这个阶段**不构成抽取效果证据**
（零字段时 `extract()` 返回 `None`，骨架包实测 `hit_rate=0.0`；补齐部分策略后
又可能是「字段几乎全空」的 1.0），`schema_gen verify` 会把它单独列为
「效果未验证」。补齐票面别名、锚点与 `start/stop` 边界、并换成真实票面
样张之后，才启用 `hit_rate >= 0.8` 门禁。
"""


def _sample_text(ir: IR) -> str:
    return (f"{ir.detect_hint}\n"
            f"（{ir.title} · 生成器骨架样张，非真实票面）\n"
            f"（{ir.field_count} 个字段的定位策略待补齐，"
            f"全部标记 locator: pending）\n")


def render_files(ir: IR) -> Dict[str, str]:
    """序列化出全部包文件（**唯一实现入口**）。

    ``write_package`` 落盘、``verify`` 做确定性对账都走这里，避免第二份拼装逻辑
    悄悄漂移（项目吃过两次「两条路径各写一遍」的亏）。
    """
    manifest = build_manifest(ir)
    spec = build_spec(ir)
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "schema.yaml": yaml.safe_dump(spec, allow_unicode=True,
                                      sort_keys=False, default_flow_style=False),
        "samples/README.md": _SAMPLE_README,
        "samples/skeleton.txt": _sample_text(ir),
    }


def write_package(ir: IR, out_dir: str, *, force: bool = False) -> Dict[str, Any]:
    """把 IR 写成完整包。返回 ``{"pkg", "files", "manifest", "spec"}``。"""
    safe_id(ir.schema_id)
    path = assert_out_dir(out_dir)
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise EmitError(f"输出路径已存在且不是目录：{path}")
        if os.listdir(path) and not force:
            raise EmitError(f"目录已存在且非空：{path}（加 --force 覆盖）")
        if os.listdir(path):
            # 只在项目目录内、且已显式 --force 才清；产物不进仓库。
            shutil.rmtree(path)
    os.makedirs(os.path.join(path, "samples"), exist_ok=True)

    files = render_files(ir)
    for name, content in files.items():
        with open(os.path.join(path, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    return {"pkg": path, "files": list(files),
            "manifest": build_manifest(ir), "spec": build_spec(ir)}
