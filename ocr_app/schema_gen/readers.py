# -*- coding: utf-8 -*-
"""把标准原件读成生成器内部结构（IR 的前一级）。

三个 reader，都只用标准库与项目已有依赖：

  * :func:`read_xsd`        — S1 主力：XSD → 实体 / 字段 / 类型 / 约束
  * :func:`read_xml_example`— S1 佐证：XML 实例 → 实际出现的实体与字段（做覆盖率对账）
  * :func:`read_pdf_text` / :func:`find_pdf_identifiers` — S2 线索：国标 PDF 的数据元

**这里不做任何「票面怎么定位」的推断**。XSD 只描述数据模型/输出格式，通篇没有
版式与坐标（规格 §6.4）；`shape_regex` 之类的值格式约束只是**候选**，
MUST NOT 写进生成包，否则运行时会把它当成「从文本里抓值」的正则。
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field as dc_field
from typing import Dict, List, Optional, Sequence, Tuple

XS = "{http://www.w3.org/2001/XMLSchema}"

_DTYPE = {
    "xs:string": "string", "xs:normalizedString": "string", "xs:token": "string",
    "xs:decimal": "decimal", "xs:double": "decimal", "xs:float": "decimal",
    "xs:integer": "int", "xs:int": "int", "xs:long": "int", "xs:short": "int",
    "xs:date": "date", "xs:dateTime": "datetime", "xs:time": "time",
    "xs:boolean": "bool",
}


class ReaderError(Exception):
    """原件结构与解析器假设不符。MUST 停止，不得猜一个结果继续生成。"""


# ---------------------------------------------------------------- 数据结构
@dataclass(frozen=True)
class Field:
    raw_name: str
    key: str                      # 全局唯一的 snake_case 标识符
    label: str                    # 中文短语（票面标签**候选**，不是已定位的标签）
    doc: str
    dtype: str
    base: str
    max_length: Optional[int]
    digits: Optional[Tuple[int, int]]
    nillable: bool
    facets: Dict[str, str] = dc_field(default_factory=dict)
    shape_regex: str = ""         # 候选值格式约束，**不进包**
    source: str = ""


@dataclass(frozen=True)
class Entity:
    raw_name: str
    key: str
    label: str
    fields: Tuple[Field, ...]
    repeating: bool
    source: str


@dataclass(frozen=True)
class XsdModel:
    member: str
    root_name: str
    entities: Tuple[Entity, ...]
    element_total: int            # 所有 xs:element（含顶层与 ref）
    root_refs: int
    field_total: int
    simple_type_total: int

    @property
    def fields(self) -> Tuple[Field, ...]:
        return tuple(f for e in self.entities for f in e.fields)


@dataclass(frozen=True)
class XmlExample:
    member: str
    root_name: str
    entities: Dict[str, Tuple[str, ...]]   # 实体 → 出现过的字段名（去重、保序）
    record_counts: Dict[str, int]


# ---------------------------------------------------------------- 标识符
def to_key(name: str) -> str:
    """XSD 名字 → 合法 snake_case 标识符。

    刻意用 ``findall`` 按连续字母数字**分块**，而不是贪婪的 ``(.+)_(.+)`` 之类：
    后者会把 ``PPE_Master_Code`` 吞成一块、把相邻标识符粘起来（项目 11-05 踩过）。
    ``PPE_ID`` → ``ppe_id``；``Tag_Number`` → ``tag_number``。
    """
    parts = re.findall(r"[A-Za-z0-9]+", name)
    key = "_".join(p.lower() for p in parts) or "field"
    if key[0].isdigit():
        key = "f_" + key
    if len(key) > 32:
        # 截断要稳定：同一输入任何进程/任何两次运行都得到同一个后缀。
        suffix = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
        key = f"{key[:25]}_{suffix}"
    return key


def _first_phrase(doc: str, limit: int = 40) -> str:
    """取中文说明的首个短句做 label，不把整段定义塞进导出标题。"""
    head = re.split(r"[。；;，,\r\n]", doc.strip(), maxsplit=1)[0].strip()
    return head[:limit]


def _as_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _shape_regex(dtype: str, max_length: Optional[int],
                 digits: Optional[Tuple[int, int]]) -> str:
    """值格式的**候选**约束（仅留在 IR，emit 时 MUST NOT 写入 schema.yaml）。"""
    if dtype == "decimal" and digits:
        total, frac = digits
        whole = max(1, total - frac)
        return rf"^\d{{1,{whole}}}(?:\.\d{{1,{frac}}})?$"
    if dtype == "int" and max_length:
        return rf"^\d{{1,{max_length}}}$"
    if dtype == "string" and max_length:
        return rf"^.{{1,{max_length}}}$"
    return ""


# ---------------------------------------------------------------- XSD
def _is_root_container(el: ET.Element) -> bool:
    seq = el.find(f"{XS}complexType/{XS}sequence")
    if seq is None:
        return False
    children = [c for c in seq if c.tag == XS + "element"]
    return bool(children) and all(c.get("ref") and not c.get("name") for c in children)


def read_xsd(data: bytes, *, member: str = "PPE.xsd",
             source: str = "S1/XSD") -> XsdModel:
    """解析一份 XSD，返回实体与字段；字段数由解析得出，**不硬编码**。"""
    try:
        root = ET.fromstring(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ET.ParseError) as e:
        raise ReaderError(f"{member} 不是可解析的 UTF-8 XSD：{e}") from e
    top = [c for c in root if c.tag == XS + "element" and c.get("name")]
    if not top:
        raise ReaderError(f"{member} 没有顶层 xs:element")

    containers = [e for e in top if _is_root_container(e)]
    if len(containers) > 1:
        raise ReaderError(f"{member} 有 {len(containers)} 个根容器，无法判定实体边界")
    container = containers[0] if containers else None
    entity_els = [e for e in top if e is not container]

    ref_info: Dict[str, str] = {}
    root_refs = 0
    if container is not None:
        seq = container.find(f"{XS}complexType/{XS}sequence")
        for c in seq:
            if c.tag != XS + "element" or not c.get("ref"):
                continue
            root_refs += 1
            local = c.get("ref").split(":")[-1]
            ref_info[local] = c.get("maxOccurs") or ""

    used_keys: Dict[str, str] = {}     # key → 首次出现的 raw_name
    entities: List[Entity] = []
    for idx, el in enumerate(entity_els, 1):
        raw = el.get("name") or f"Entity{idx}"
        seq = el.find(f"{XS}complexType/{XS}sequence")
        field_els = [c for c in seq if c.tag == XS + "element" and c.get("name")] \
            if seq is not None else []
        parsed: List[Field] = []
        for fe in field_els:
            raw_name = fe.get("name") or ""
            key = to_key(raw_name)
            if key in used_keys:
                # 跨实体同名（多个实体都有 ID）也会撞：lint 的 fields 字典是**全局**的，
                # 重复 key 会让包直接非法。加实体前缀后仍撞就补序号。
                key = to_key(f"{raw}_{raw_name}")
                n = 2
                base = key
                while key in used_keys:
                    key = f"{base}_{n}"
                    n += 1
            used_keys[key] = raw_name

            doc = (fe.findtext(f"{XS}annotation/{XS}documentation") or "").strip()
            restr = fe.find(f"{XS}simpleType/{XS}restriction")
            base = (restr.get("base") if restr is not None else None) \
                or fe.get("type") or "xs:string"
            facets: Dict[str, str] = {}
            if restr is not None:
                facets = {c.tag.replace(XS, ""): c.get("value")
                          for c in restr if c.get("value") is not None}
            max_length = _as_int(facets.get("maxLength"))
            total_d, frac_d = _as_int(facets.get("totalDigits")), _as_int(facets.get("fractionDigits"))
            digits = (total_d, frac_d) if total_d and frac_d is not None else None
            dtype = _DTYPE.get(base, "string")
            parsed.append(Field(
                raw_name=raw_name, key=key,
                label=_first_phrase(doc) or raw_name, doc=doc,
                dtype=dtype, base=base, max_length=max_length, digits=digits,
                nillable=(fe.get("nillable", "false").lower() == "true"),
                facets=facets, shape_regex=_shape_regex(dtype, max_length, digits),
                source=f"{source}/{member}/{raw}/{raw_name}"))
        entities.append(Entity(
            raw_name=raw, key=f"sec_{to_key(raw)}", label=raw,
            fields=tuple(parsed),
            repeating=(ref_info.get(raw, "").lower() == "unbounded"),
            source=f"{source}/{member}/{raw}"))

    model = XsdModel(
        member=member, root_name=container.get("name") if container is not None else "",
        entities=tuple(entities),
        element_total=len(root.findall(".//" + XS + "element")),
        root_refs=root_refs,
        field_total=sum(len(e.fields) for e in entities),
        simple_type_total=len(root.findall(".//" + XS + "simpleType")),
    )
    # 结构自洽校验：漏读/多读一个 element 都要停，不能带着错误的字段数继续生成。
    expected = len(top) + root_refs + model.field_total
    if model.element_total != expected:
        raise ReaderError(
            f"{member} element 计数不自洽：总数 {model.element_total}，"
            f"按「顶层 {len(top)} + 根容器 ref {root_refs} + 字段 {model.field_total}」"
            f"应为 {expected}；结构可能不是本解析器假设的形态")
    return model


# ---------------------------------------------------------------- XML 实例
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def read_xml_example(data: bytes, *, member: str = "PPE.XML") -> XmlExample:
    """读一份 XML 实例，统计实际出现的实体与字段（用于与 XSD 覆盖率对账）。"""
    try:
        root = ET.fromstring(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ET.ParseError) as e:
        raise ReaderError(f"{member} 不是可解析的 XML：{e}") from e
    entities: Dict[str, List[str]] = {}
    counts: Dict[str, int] = {}
    for child in root:
        name = _local(child.tag)
        if name == _local(root.tag) or not list(child):
            continue
        bucket = entities.setdefault(name, [])
        counts[name] = counts.get(name, 0) + 1
        for f in child.iter():
            if f is child or list(f):     # 根或容器不算字段；只看叶子
                continue
            fn = _local(f.tag)
            if fn not in bucket:
                bucket.append(fn)
    return XmlExample(member=member, root_name=_local(root.tag),
                      entities={k: tuple(v) for k, v in entities.items()},
                      record_counts=counts)


# ---------------------------------------------------------------- 国标 PDF（S2）
def read_pdf_text(path: str, *, max_pages: Optional[int] = None) -> str:
    """用 pypdfium2 抽取 PDF 文本层（不 OCR，不改文件）。"""
    try:
        import pypdfium2 as pdfium
    except ImportError as e:      # pragma: no cover - 主 venv 必定有
        raise ReaderError("缺少 pypdfium2，无法读国标 PDF") from e
    doc = pdfium.PdfDocument(path)
    try:
        pages = range(len(doc)) if max_pages is None else range(min(max_pages, len(doc)))
        chunks = []
        for i in pages:
            chunks.append(doc[i].get_textpage().get_text_range())
        return "\n".join(chunks)
    finally:
        doc.close()


_IDENT_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+|\d{2,3}\.\d{2,3})\b")


def find_pdf_identifiers(text: str) -> Sequence[Tuple[str, str]]:
    """从 PDF 文本里找**候选**数据元标识符，附上下文。

    这是 S2 的保守线索，不是权威字段表：国标正文的编号体系与 XSD 名字未必一一对应，
    对不上就登记差距（计划 P3），MUST NOT 拿它杜撰字段定义。
    """
    seen = set()
    out = []
    for m in _IDENT_RE.finditer(text):
        ident = m.group(1)
        if ident in seen:
            continue
        seen.add(ident)
        ctx = re.sub(r"\s+", " ", text[max(0, m.start() - 40):m.end() + 60]).strip()
        out.append((ident, ctx))
    return tuple(out)
