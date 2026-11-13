# -*- coding: utf-8 -*-
"""IR → schema 包（v2 五件套：manifest / schema.yaml / README / standard / samples）。

四条不可动摇的输出约束（规格 §6.4 / §6.5 / §6.7 / §3.1.1）：

  * **不生成** ``layout.html``：国标只有数据模型，没有版式；
  * **不生成定位策略**：字段只有 ``key`` / ``label`` / ``locator: pending``，
    没有 ``regex`` / ``anchor`` / ``required``；``strict: false``；
  * **保守 detect + 最低优先级**：``detect.any`` 只放标准号，``priority: 900``；
  * **原生 v2 包**：写 ``contract_version: 2`` / ``form: declarative`` /
    ``layout_source: none``，并产出 ``README.md`` / ``standard.json`` /
    ``samples/*.expected.json``；但**仍不生成** ``layout.html``。

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

from schemas.lint import CONTRACT_VERSION

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
    """构造 ``manifest.json``；``x-*`` 键 MUST NOT 影响装载。

    v2 字段用 ``schemas.lint.CONTRACT_VERSION`` 单一来源，避免生成器与 lint
    各写一份数字。``layout_source: none`` 是**如实声明**：国标只有数据模型，
    没有版式，不编造 ``layout.html``（规格 §3.1.1 三态）。
    """
    return {
        "id": ir.schema_id,
        "title": ir.title,
        "version": ir.version,
        "kind": "user",
        "contract_version": CONTRACT_VERSION,
        "form": "declarative",
        "layout_source": "none",
        "standard": "standard.json",
        "license": "MIT",
        "priority": GENERATED_PRIORITY,
        "schema": "schema.yaml",
        "x-provenance": ir.provenance(__version__),
    }


def build_standard(ir: IR) -> Dict[str, Any]:
    """构造 ``standard.json``：只描述**输出数据模型**，不含坐标与定位策略。

    标准（PDF / XSD）给的是数据元与字段类型，通篇没有版式与正则；定位策略
    一律在 ``schema.yaml`` 里以 ``locator: pending`` 占位。字段名跨实体复用时
    按首次出现去重（生成包实测 342 字段元素 = 120 个唯一名）。
    """
    fields: Dict[str, Dict[str, Any]] = {}
    for sec in ir.sections:
        for f in sec.fields:
            fields.setdefault(f.key, {
                "type": "string",
                "required": False,
                "label": f.label,
                "entity": f.entity,
            })
    return {
        "standard": {
            "code": ir.standard or "（生成器未指定标准编号）",
            "title": ir.title,
            "source_url": "",
            "license": "字段取舍参考；标准全文不内嵌，原文版权归发布方所有",
            "note": "本文件只描述输出数据模型；定位策略见 schema.yaml（当前全部 locator: pending）",
        },
        "fields": fields,
    }


def _readme_text(ir: IR) -> str:
    return f"""# {ir.title}

## 功能与适用范围

本包由 `python -m schema_gen` 从标准源生成，描述「{ir.title}」的数据模型骨架，
包含 {ir.field_count} 个字段元素、{len(ir.sections)} 个实体分段。
**适用范围：仅作数据结构参考**，不能直接用于真实票面抽取，
因为所有字段的定位策略仍是 `locator: pending`。

## 使用方法

1. `python -m schemas.cli validate {ir.schema_id}` 检查结构；
2. 补齐 `schema.yaml` 的 `detect`、字段别名/锚点与 table 段 `start`/`stop`；
3. 把 `samples/skeleton.txt` 换成真实票面识别文本，并填 `*.expected.json`；
4. `python -m schemas.cli test {ir.schema_id}` 看命中率与逐字段对账报告。

生成时用的标准源哈希与字段数见 `manifest.json` 的 `x-provenance`。

## 主要特点与已知边界

- 声明式零 Python，由通用 `runtime.py` 驱动；`strict: false`，
  `priority: {GENERATED_PRIORITY}`（最低），`detect` 只用标准号作保守关键词。
- 已知边界：无跨页合并、几何推理；**抽取效果尚未验证**
  （骨架包实测 `hit_rate=0.0`，见规格 §6.6）。
- **HTML 版式只是人读预览，不是合规归档版式**：本包 `layout_source: none`，
  不生成 `layout.html`。归档需含数字签名的 XML 数据电文，版式为 OFD。

## 字段来源与标准编号

字段字典见 `standard.json`（`standard.code` / `standard.license` 与 `fields`）。
标准只描述数据模型，**不含坐标、锚点与版式**；定位策略全在 `schema.yaml`。
标准引用仅写编号与字段映射，不整份内嵌受版权保护的全文。

## 版本与变更记录

- {ir.version}：由 `python -m schema_gen` 生成的初始骨架。

## 版权与许可

本包随 pic2edit 以 MIT 许可发布；第三方署名见仓库 `THIRD_PARTY_NOTICES`。
所引标准的原文版权归发布方所有，本包仅作字段取舍参考，不含标准全文。
"""


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
    standard = build_standard(ir)
    # 骨架包所有字段都是 locator: pending，无法给出有意义的期望字段值；
    # 写空的 golden 文件是**结构诚实**，不是漏做（规格 §6.6：效果只报告）。
    golden = {
        "note": "骨架样张：定位策略待补齐，暂无可断言的期望字段",
        "fields": {},
        "skip": [],
    }
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "schema.yaml": yaml.safe_dump(spec, allow_unicode=True,
                                      sort_keys=False, default_flow_style=False),
        "README.md": _readme_text(ir),
        "standard.json": json.dumps(standard, ensure_ascii=False, indent=2) + "\n",
        "samples/README.md": _SAMPLE_README,
        "samples/skeleton.txt": _sample_text(ir),
        "samples/skeleton.expected.json": json.dumps(
            golden, ensure_ascii=False, indent=2) + "\n",
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
