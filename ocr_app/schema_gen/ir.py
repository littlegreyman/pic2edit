# -*- coding: utf-8 -*-
"""生成器的中间表示（IR）——**只内存，MUST NOT 落盘**（规格 §6.2）。

IR 是 XSD 模型与最终包之间唯一的转换层：把「标准里有什么字段」翻译成
「包里有哪些段/列」，并在此处做三条决定性裁剪：

  1. 所有字段标 ``locator: pending``，不写 ``required`` / ``regex`` / ``anchor``；
  2. 重复实体（``maxOccurs="unbounded"``）落成 ``table`` 段，``start`` 用
     **保守 hint**（标准号，票面不会出现）→ 运行时找不到就返回空段，
     绝不把整页正文收成明细行；
  3. 单段字段数 ≤ 30（规格 §6.7），超了按原顺序切分，不改字段 key。

包里只落 §3 的文件 + samples + provenance；IR 里的 doc / dtype / shape_regex
等元信息 MUST NOT 泄漏进包（那是「能生成什么、不能生成什么」的分界线，§6.4）。
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, Sequence, Tuple

from .readers import XsdModel

#: 单段字段数上限（规格 §6.7 SHOULD ≤ 30）。
SECTION_MAX_FIELDS = 30
#: 生成包的优先级：最低，绝不抢在内置包前面（§6.4 漏洞 A）。
GENERATED_PRIORITY = 900
#: 定位策略状态：生成包永远是 pending，效果自检因此不设门禁。
LOCATOR_PENDING = "pending"
#: table 段的收行上限，防止边界词恰好出现时把整页吃进去。
TABLE_LIMIT = 50


@dataclass(frozen=True)
class IRField:
    key: str
    label: str
    raw_name: str
    entity: str
    source: str
    locator: str = LOCATOR_PENDING


@dataclass(frozen=True)
class IRSection:
    key: str
    label: str
    mode: str                       # "kv" | "table"
    fields: Tuple[IRField, ...]
    start: str = ""                 # 只有 table 段写
    limit: int = 0                  # 只有 table 段写


@dataclass(frozen=True)
class IR:
    schema_id: str
    title: str
    version: str
    standard: str
    detect_hint: str
    xsd_member: str
    sections: Tuple[IRSection, ...]
    sources: Tuple[Dict[str, Any], ...] = ()
    generated_at: str = ""
    xsd_counts: Dict[str, int] = dc_field(default_factory=dict)

    @property
    def fields(self) -> Tuple[IRField, ...]:
        return tuple(f for s in self.sections for f in s.fields)

    @property
    def field_count(self) -> int:
        return len(self.fields)

    @property
    def locator_pending(self) -> int:
        return sum(1 for f in self.fields if f.locator == LOCATOR_PENDING)

    @property
    def coverage(self) -> Dict[str, int]:
        return {"model": self.field_count, "locator_resolved": 0,
                "locator_pending": self.locator_pending}

    def provenance(self, generator_version: str) -> Dict[str, Any]:
        return {
            "generator": f"schema_gen/{generator_version}",
            "standard": self.standard,
            "xsd_member": self.xsd_member,
            "sources": [dict(s) for s in self.sources],
            "generated_at": self.generated_at,
            "field_count": self.field_count,
            "coverage": dict(self.coverage),
            "xsd_counts": dict(self.xsd_counts),
            "note": "仅生成数据格式骨架；定位策略待人工/样张补齐，不能当作可直接上线的抽取规则",
        }


def _chunks(seq: Sequence, size: int):
    for i in range(0, len(seq), size):
        yield i // size + 1, seq[i:i + size]


def build_ir(model: XsdModel, *, schema_id: str, title: str, version: str,
             standard: str, detect_hint: str,
             sources: Sequence[Dict[str, Any]] = (),
             generated_at: str = "") -> IR:
    """XSD 模型 → IR。字段 key 沿用 readers 的全局去重结果，不再改名。"""
    sections = []
    for ent in model.entities:
        mode = "table" if ent.repeating else "kv"
        fields = tuple(IRField(
            key=f.key, label=f.label, raw_name=f.raw_name,
            entity=ent.raw_name, source=f.source) for f in ent.fields)
        n_parts = max(1, (len(fields) + SECTION_MAX_FIELDS - 1) // SECTION_MAX_FIELDS)
        for part_no, part in _chunks(fields, SECTION_MAX_FIELDS):
            label = ent.raw_name if n_parts == 1 else \
                f"{ent.raw_name}（{part_no}/{n_parts}）"
            sections.append(IRSection(
                key=f"{ent.key}_{part_no}" if n_parts > 1 else ent.key,
                label=label, mode=mode, fields=tuple(part),
                # 保守 start：票面不会出现标准号 → 运行时返回空段（§3.6.3），
                # 而不是把票据上半部的键值行当成数据行。
                start=detect_hint if mode == "table" else "",
                limit=TABLE_LIMIT if mode == "table" else 0))
    return IR(
        schema_id=schema_id, title=title, version=version,
        standard=standard, detect_hint=detect_hint, xsd_member=model.member,
        sections=tuple(sections), sources=tuple(sources),
        generated_at=generated_at,
        xsd_counts={"element_total": model.element_total,
                    "root_refs": model.root_refs,
                    "entities": len(model.entities),
                    "fields": model.field_total,
                    "simple_types": model.simple_type_total})
