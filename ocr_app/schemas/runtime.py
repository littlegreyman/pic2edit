# -*- coding: utf-8 -*-
"""声明式 schema 运行时：把 ``schema.yaml``（+ ``layout.html``）变成可运行的 provider。

这是**生产侧**的地基。在此之前，新增一种凭证的唯一办法是写一千行 Python；
本模块让"填一张表"成为可行路径：yaml 描述字段与定位策略，html 描述版式，
运行时负责 detect / extract / render 三件事。

三条设计约束（都是有代价的选择，写下来便于日后回看）
----------------------------------------------------
1. **不做图灵完备的 DSL。** 不支持 ``when`` / ``transform`` / 表达式求值。
   一旦 yaml 能编程，它就变成一门比 Python 更难调试的语言。复杂版式请退到
   ``provider.py``（Python 插件），那是**明确的**逃生口，而不是把 yaml 撑大。
2. **不引模板引擎。** layout.html 只支持 ``{{ 字段 }}`` 占位与 ``data-repeat``
   重复区块。引 jinja2 意味着多一个打包依赖、多一套注入面，而我们需要的能力
   只有"填值"和"重复行"两件。
3. **抽不到就返回 None，不猜。** 一个 required 字段都没有取到值时，本页按
   「未命中」处理，让调用方回退到通用渲染——宁可不结构化，也不要输出一张
   半空的可信度不明的表。

字段取值三级策略（按顺序尝试，先到先得）
--------------------------------------
- ``regex``：显式正则，最可靠，适合号码/金额这类有固定形态的字段；
- **行内标签**：``卡片编号：AC-001`` 这种标签与值在同一行的情形，按标签后
  的冒号/空白切出值——这是 OCR 文本里最常见的形态；
- **坐标锚点**：``anchor: right`` 取同一视觉行右侧最近的框，``anchor: below``
  取下方最近的框。用于"标签一格、值一格"的表格式票面。

因此 schema 里的字段应当**同时**写 ``label`` 与（必要时）``regex`` 和
``anchor``：label 是票面字面词，regex 是形状约束，anchor 是几何位置。
"""
from __future__ import annotations

import html as _html
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # 内核内可用时接统一日志；单独用也不至于 ImportError
    from utils.logging_setup import get_logger

    _log = get_logger("schemas.runtime")
except Exception:  # pragma: no cover - 独立运行兜底
    import logging

    _log = logging.getLogger("pic2edit.schemas.runtime")

try:
    import yaml
except Exception:  # pragma: no cover - 缺依赖时给出可执行的提示
    yaml = None  # type: ignore[assignment]

Rows = List[Dict[str, Any]]

#: 从 yaml 里认识的分段模式。新增模式要先在 runtime 里实现，故此处是白名单。
SECTION_MODES = ("kv", "table", "text")

#: 坐标锚点方向白名单。
ANCHORS = ("right", "below", "inline", "none")

#: 行内标签与值之间的分隔符（冒号可有可无，全角半角都收）。
#: **不含换行**：`原值\n净值` 是"标签没有值、下一个标签接上"的排版，
#: 若把 \n 也当分隔符吃掉，就会把隔壁字段的标签当成自己的值。
_SEP_RE = re.compile(r"^[ \t\u3000:：]+")

#: 值里允许出现的"标签后残余"，用于截断到行尾或连续两个空格。
_VALUE_STOP_RE = re.compile(r"\s{2,}")


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------
def load_spec(path: str) -> Dict[str, Any]:
    """读一个 yaml schema 文件并做最小结构校验。"""
    if yaml is None:  # pragma: no cover
        raise RuntimeError("缺少 PyYAML：请 pip install 'PyYAML>=6.0'")
    with open(path, encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}
    if not isinstance(spec, dict):
        raise ValueError(f"{path}: 顶层必须是映射（key: value）")
    if not spec.get("id"):
        raise ValueError(f"{path}: 缺少 id")
    if not isinstance(spec.get("sections"), list) or not spec["sections"]:
        raise ValueError(f"{path}: 缺少 sections（至少一段）")
    return spec


def build_provider(pkg_dir: str, manifest: Dict[str, Any]) -> "DeclarativeProvider":
    """注册表调用的入口：把一个 schema 目录变成 provider 实例。"""
    name = manifest.get("schema") or "schema.yaml"
    path = os.path.join(pkg_dir, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"找不到 {name}")
    spec = load_spec(path)
    return DeclarativeProvider(pkg_dir, manifest, spec)


class DeclarativeProvider:
    """由 yaml + （可选）layout.html 驱动的 schema 提供者。

    属性 ``id/title/version/kind/priority/layout`` 由注册表按 manifest 覆盖，
    构造时给出的是 yaml 里的默认值（manifest 才是元信息的权威来源）。
    """

    def __init__(self, pkg_dir: str, manifest: Dict[str, Any], spec: Dict[str, Any]) -> None:
        self.pkg_dir = pkg_dir
        self.spec = spec
        self.id: str = spec.get("id") or manifest.get("id") or "unnamed"
        self.title: str = spec.get("title") or manifest.get("title") or self.id
        self.version: str = str(spec.get("version") or manifest.get("version") or "1.0.0")
        self.kind: str = "declarative"
        self.priority: int = int(spec.get("priority", 50))
        self.layout: Optional[str] = manifest.get("layout") or (
            "layout.html" if os.path.isfile(os.path.join(pkg_dir, "layout.html")) else None
        )
        self._layout_text: Optional[str] = None
        self._layout_css: Optional[str] = None

    # ---------- 版式资源 ----------
    def _layout(self) -> str:
        if self._layout_text is None:
            if not self.layout:
                self._layout_text = ""
            else:
                p = os.path.join(self.pkg_dir, self.layout)
                try:
                    with open(p, encoding="utf-8") as f:
                        self._layout_text = f.read()
                except Exception as e:
                    _log.warning("schema %s 版式读取失败（%s）: %s", self.id, self.layout, e)
                    self._layout_text = ""
        return self._layout_text

    def _split_style(self) -> Tuple[str, str]:
        """把 layout.html 拆成 (css, body)。版式文件里的 <style> 归 css()。

        HTML 注释在这里就删掉：注释里常写着 `data-repeat` 的用法示例，
        留在正文里会被重复区块的正则当成真区块匹配，渲染出一堆错位的行。
        """
        raw = self._layout()
        if not raw:
            return "", ""
        raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
        m = re.search(r"<style[^>]*>(.*?)</style>", raw, re.S | re.I)
        if not m:
            return "", raw.strip()
        body = (raw[:m.start()] + raw[m.end():]).strip()
        return m.group(1), body

    # ---------- detect ----------
    def detect(self, text: str, lines: Sequence[Dict[str, Any]],
               width: int, height: int) -> bool:
        rules = self.spec.get("detect") or {}
        if not isinstance(rules, dict):
            rules = {}
        body = text or ""
        # 没有规则时：有正文就算"可能是"，真正的判断交给 extract 的 required 校验
        if not any(rules.get(k) for k in ("any", "all", "none", "regex")):
            return bool(body.strip())
        for kw in rules.get("all") or []:
            if kw and kw not in body:
                return False
        for kw in rules.get("none") or []:
            if kw and kw in body:
                return False
        for pat in rules.get("regex") or []:
            if not re.search(pat, body):
                return False
        any_kws = [k for k in (rules.get("any") or []) if k]
        if any_kws and not any(k in body for k in any_kws):
            return False
        return True

    # ---------- extract ----------
    def extract(self, text: str, lines: Sequence[Dict[str, Any]],
                width: int, height: int) -> Optional[Dict[str, Any]]:
        body = text or ""
        ln = list(lines or [])
        flat: Dict[str, Any] = {}
        sections: List[Dict[str, Any]] = []
        tables: Dict[str, Rows] = {}
        missing_required: List[str] = []

        for sec in self.spec.get("sections") or []:
            key = sec.get("key") or ""
            mode = (sec.get("mode") or "kv").lower()
            label = sec.get("label") or key
            if mode == "table":
                rows = self._table_rows(sec, body, ln, width, height)
                tables[key] = rows
                sections.append({"key": key, "label": label, "mode": "table", "rows": rows})
                if sec.get("required") and not rows:
                    missing_required.append(label)
                continue
            if mode == "text":
                # text 分段用 section 自身的 regex/label 描述"这一整块文字"，
                # 不当成字段处理，否则 section 的 label（如"三、备注"）会被当成
                # 票面标签去匹配，永远匹配不上。
                f = {"key": key, "label": sec.get("field_label") or "",
                     "regex": sec.get("regex"), "anchor": sec.get("anchor", "none")}
                val = self._field_value(f, body, ln, width, height)
                sections.append({"key": key, "label": label, "mode": "text", "value": val or ""})
                if val:
                    # 也放进 flat：版式里写 {{ remarks }} 直接可用，不必记住它属于哪一段
                    flat[key] = val
                if sec.get("required") and not val:
                    missing_required.append(label)
                continue
            # 默认 kv
            fields = []
            for f in sec.get("fields") or []:
                fkey = f.get("key") or ""
                val = self._field_value(f, body, ln, width, height)
                fields.append({"key": fkey, "label": f.get("label") or fkey, "value": val or ""})
                if val:
                    flat[fkey] = val
                elif f.get("required"):
                    missing_required.append(f.get("label") or fkey)
            sections.append({"key": key, "label": label, "mode": "kv", "fields": fields})

        # 一个字段都没抽到 / required 缺失 → 视为未命中，交给调用方回退
        if not flat and not any(tables.values()):
            return None
        if missing_required and self.spec.get("strict", True):
            _log.info("schema %s 未命中：缺少必填 %s", self.id, "、".join(missing_required))
            return None
        return {
            "type": self.id,
            "version": self.version,
            "sections": sections,
            "flat": flat,
            "tables": tables,
            "missing": missing_required,
        }

    # ---------- 单字段取值 ----------
    def _field_value(self, f: Dict[str, Any], text: str,
                     lines: Sequence[Dict[str, Any]],
                     width: int, height: int) -> Optional[str]:
        label = (f.get("label") or "").strip()
        # 1) 显式正则
        pat = f.get("regex")
        if pat:
            m = re.search(pat, text, re.M)
            if m:
                got = (m.group(1) if m.groups() else m.group(0)) or ""
                got = got.strip()
                if got and got not in (label,):
                    return got
        if not label:
            return None
        # 2) 行内标签（"标签：值" 同一行）
        got = _inline_value(text, label)
        if got:
            return got
        # 3) 坐标锚点
        anchor = (f.get("anchor") or "right").lower()
        if anchor in ("none", "inline"):
            return None
        return _anchored_value(lines, label, anchor, width, height)

    # ---------- 表格分段 ----------
    def _table_rows(self, sec: Dict[str, Any], text: str,
                    lines: Sequence[Dict[str, Any]],
                    width: int, height: int) -> Rows:
        cols = [c for c in (sec.get("columns") or []) if c.get("key")]
        if not cols:
            return []
        start_at = (sec.get("start") or "").strip()
        stop_at = (sec.get("stop") or "").strip()
        # 先把"这一段"从整页里裁出来：不裁的话，票据上半部分的键值行会被
        # 当成表格行（每行两格，正好凑成两列），抽出来是一张假表。
        seg_text, seg_lines = _cut_segment(text, list(lines or []), start_at, stop_at)
        raw_rows: List[List[str]] = []
        if seg_lines:
            raw_rows = _rows_from_lines(seg_lines, width, height)
        if not raw_rows:
            raw_rows = _rows_from_text(seg_text)
        if not raw_rows:
            return []
        header_words = {str(c.get("label") or c["key"]).strip() for c in cols}
        out: Rows = []
        for cells in raw_rows:
            cells = [c for c in (x.strip() for x in cells) if c]
            if not cells:
                continue
            joined = "".join(cells)
            if stop_at and stop_at in joined:
                break
            # 表头行：整行几乎都是列名，跳过
            if sum(1 for c in cells if c in header_words) >= max(1, len(cells) // 2):
                continue
            row: Dict[str, Any] = {}
            for i, c in enumerate(cols):
                if i < len(cells):
                    row[c["key"]] = cells[i]
                elif i == len(cols) - 1 and len(cells) > len(cols):
                    row[c["key"]] = " ".join(cells[i:])
            if any(row.get(c["key"]) for c in cols):
                out.append(row)
            if sec.get("limit") and len(out) >= int(sec["limit"]):
                break
        return out

    # ---------- 渲染 ----------
    def render_md(self, doc: Dict[str, Any]) -> str:
        out: List[str] = []
        for sec in doc.get("sections") or []:
            out.append(f"### {sec['label']}")
            if sec["mode"] == "table":
                out.append(_md_table(sec.get("rows") or [], self._cols_of(sec["key"])))
            elif sec["mode"] == "text":
                out.append(sec.get("value") or "_（未识别到）_")
            else:
                for f in sec.get("fields") or []:
                    val = f.get("value") or "_（未识别到）_"
                    out.append(f"- **{f['label']}**：{val}")
            out.append("")
        return "\n".join(out).strip()

    def render_txt(self, doc: Dict[str, Any]) -> str:
        out: List[str] = []
        for sec in doc.get("sections") or []:
            out.append(f"[{sec['label']}]")
            if sec["mode"] == "table":
                cols = self._cols_of(sec["key"])
                out.append("\t".join(str(c.get("label") or c["key"]) for c in cols))
                for r in sec.get("rows") or []:
                    out.append("\t".join(str(r.get(c["key"], "")) for c in cols))
            elif sec["mode"] == "text":
                out.append(sec.get("value") or "")
            else:
                for f in sec.get("fields") or []:
                    out.append(f"{f['label']}\t{f.get('value') or ''}")
            out.append("")
        return "\n".join(out).strip()

    def render_html(self, doc: Dict[str, Any]) -> str:
        body = self._split_style()[1]
        if body.strip():
            try:
                return render_layout(body, doc)
            except Exception as e:  # 版式写坏了 → 退回通用表，不让导出整体失败
                _log.warning("schema %s 版式渲染失败，回退通用表: %s", self.id, e)
        return _generic_html(doc)

    def css(self) -> str:
        if self._layout_css is None:
            self._layout_css = self._split_style()[0] or ""
        return self._layout_css

    # ---------- 辅助 ----------
    def _cols_of(self, key: str) -> List[Dict[str, Any]]:
        for sec in self.spec.get("sections") or []:
            if (sec.get("key") or "") == key:
                return [c for c in (sec.get("columns") or []) if c.get("key")]
        return []


# ---------------------------------------------------------------------------
# 取值：行内标签
# ---------------------------------------------------------------------------
def _inline_value(text: str, label: str) -> Optional[str]:
    """在正文里找 ``标签：值``，返回同一行的值部分。"""
    if not text or not label:
        return None
    # 标签里可能带正则元字符（如"金额(元)"），转义后再找
    for m in re.finditer(re.escape(label), text):
        rest = text[m.end():]
        rest = _SEP_RE.sub("", rest, count=1)
        if not rest or rest.startswith("\n"):
            continue
        line = rest.split("\n", 1)[0]
        line = _VALUE_STOP_RE.split(line, 1)[0].strip()
        # 值里若又出现别的标签词，说明这行是"标签1：标签2："的空排版
        if line and len(line) <= 120:
            return line
    return None


# ---------------------------------------------------------------------------
# 取值：坐标锚点
# ---------------------------------------------------------------------------
def _box(line: Dict[str, Any]) -> Tuple[float, float, float, float]:
    return (float(line.get("x0", 0)), float(line.get("y0", 0)),
            float(line.get("x1", 0)), float(line.get("y1", 0)))


def _cy(line: Dict[str, Any]) -> float:
    _x0, y0, _x1, y1 = _box(line)
    return (y0 + y1) / 2.0


def _anchored_value(lines: Sequence[Dict[str, Any]], label: str,
                    anchor: str, width: int, height: int = 0) -> Optional[str]:
    """按几何位置取标签右侧/下方的值框。"""
    cand = [ln for ln in lines if label in str(ln.get("text") or "")]
    if not cand:
        return None
    lab = cand[0]
    lx0, ly0, lx1, ly1 = _box(lab)
    lh = max(ly1 - ly0, 1.0)
    band = lh * 0.6  # 同一视觉行的判定带宽
    if anchor == "right":
        same = []
        for ln in lines:
            if ln is lab:
                continue
            x0, _y0, _x1, _y1 = _box(ln)
            if x0 >= lx1 - 2 and abs(_cy(ln) - _cy(lab)) <= band:
                same.append((x0, ln))
        if same:
            same.sort(key=lambda t: t[0])
            txt = str(same[0][1].get("text") or "").strip()
            return txt or None
        return None
    if anchor == "below":
        below = []
        for ln in lines:
            if ln is lab:
                continue
            x0, y0, _x1, _y1 = _box(ln)
            if y0 >= ly1 - 1 and x0 < lx1 and _cy(ln) > _cy(lab):
                below.append((y0, ln))
        if below:
            below.sort(key=lambda t: t[0])
            txt = str(below[0][1].get("text") or "").strip()
            return txt or None
    return None


# ---------------------------------------------------------------------------
# 表格行：坐标 / 纯文本两条路
# ---------------------------------------------------------------------------
def _cut_segment(text: str, lines: List[Dict[str, Any]],
                 start_at: str, stop_at: str) -> Tuple[str, List[Dict[str, Any]]]:
    """把表格那一段从整页里裁出来。

    声明了 ``start`` 却找不到该标题时，返回空——"声明了边界就要守住边界"。
    退化成"整页都当表格"会把票据上半部分的键值行错当成数据行，
    产出一张看似完整、实则混入表头的假表，这比抽不到更糟。
    """
    body = text or ""
    if start_at:
        i = body.find(start_at)
        if i < 0:
            return "", []
        j = body.rfind("\n", 0, i)
        body = body[j + 1:] if j >= 0 else body[i:]
    if stop_at:
        k = body.find(stop_at)
        if k > 0:
            j = body.rfind("\n", 0, k)
            body = body[:j] if j > 0 else body[:k]

    if not lines:
        return body, []
    y_lo = y_hi = None
    for ln in lines:
        t = str(ln.get("text") or "")
        if start_at and start_at in t:
            y0 = _box(ln)[1]
            y_lo = y0 if y_lo is None else min(y_lo, y0)
        if stop_at and stop_at in t:
            y0 = _box(ln)[1]
            y_hi = y0 if y_hi is None else min(y_hi, y0)
    if start_at and y_lo is None:
        return body, []
    sel = []
    for ln in lines:
        y0 = _box(ln)[1]
        if y_lo is not None and y0 <= y_lo:
            continue
        if y_hi is not None and y0 >= y_hi:
            continue
        sel.append(ln)
    return body, sel


def _rows_from_lines(lines: Sequence[Dict[str, Any]], width: int, height: int) -> List[List[str]]:
    """按 y 聚类成视觉行，行内按 x 排序。"""
    if not lines:
        return []
    hs = [max(_box(ln)[3] - _box(ln)[1], 1.0) for ln in lines]
    band = max(6.0, 0.6 * sorted(hs)[len(hs) // 2])
    buckets: Dict[int, List[Dict[str, Any]]] = {}
    for ln in lines:
        buckets.setdefault(int(round(_cy(ln) / band)), []).append(ln)
    rows: List[List[str]] = []
    for key in sorted(buckets):
        grp = sorted(buckets[key], key=lambda ln: _box(ln)[0])
        texts = [str(g.get("text") or "").strip() for g in grp]
        texts = [t for t in texts if t]
        if texts:
            rows.append(texts)
    return rows


def _rows_from_text(text: str) -> List[List[str]]:
    """无坐标时：按连续空白（2+）或制表符切列。"""
    rows: List[List[str]] = []
    for raw in (text or "").split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        cells = [c for c in re.split(r"\t|\s{2,}", raw) if c.strip()]
        if len(cells) >= 2:
            rows.append(cells)
    return rows


# ---------------------------------------------------------------------------
# layout.html：占位替换 + 重复区块
# ---------------------------------------------------------------------------
_PLACEHOLDER_RE = re.compile(r"\{\{\s*!?\s*([A-Za-z_][\w.]*)\s*\}\}")


def render_layout(template: str, doc: Dict[str, Any]) -> str:
    """渲染 layout.html。

    只支持两种语法，够用且不会变成一门语言：
    - ``{{ 路径 }}``            取标量并做 HTML 转义（``{{! 路径 }}`` 不转义）
    - ``data-repeat="分段key"`` 该元素按表格行重复，内部占位按行取值
    """
    # 注释先去掉：注释里常写着 data-repeat 的用法示例，留着会被当成真区块
    template = re.sub(r"<!--.*?-->", "", template, flags=re.S)
    tables = doc.get("tables") or {}

    def sub_repeat(m: "re.Match[str]") -> str:
        tag = m.group(1)
        attrs = m.group(2)
        key = m.group(3)
        inner = m.group(4)
        rows = tables.get(key) or []
        if not rows:
            return ""
        chunks = []
        for row in rows:
            flat = _flat_of(doc, key)
            scope = dict(flat)
            scope.update(row)
            chunks.append(_sub_scalars(inner, scope, key))
        # 去掉 data-repeat 属性后重复
        clean_attrs = re.sub(r'\s*data-repeat="[^"]*"', "", attrs)
        return "".join(f"<{tag}{clean_attrs}>{c}</{tag}>" for c in chunks)

    out = _REPEAT_RE.sub(sub_repeat, template)
    return _sub_scalars(out, doc.get("flat") or {}, None, doc=doc)


_REPEAT_RE = re.compile(
    r"<(\w+)([^>]*\bdata-repeat=\"([\w.]+)\"[^>]*)>(.*?)</\1>", re.S | re.I
)


def _flat_of(doc: Dict[str, Any], section_key: str) -> Dict[str, Any]:
    for sec in doc.get("sections") or []:
        if sec.get("key") == section_key and sec.get("mode") == "kv":
            return {f["key"]: f.get("value") or "" for f in sec.get("fields") or []}
    return {}


def _sub_scalars(template: str, scope: Dict[str, Any], section: Optional[str],
                 doc: Optional[Dict[str, Any]] = None) -> str:
    def repl(m: "re.Match[str]") -> str:
        raw = m.group(0)
        path = m.group(1)
        val = _lookup(doc or {}, scope, section, path)
        text = "" if val is None else str(val)
        return text if raw.startswith("{{!") else _html.escape(text)

    return _PLACEHOLDER_RE.sub(repl, template)


def _lookup(doc: Dict[str, Any], scope: Dict[str, Any], section: Optional[str],
            path: str) -> Any:
    if "." in path:
        head, rest = path.split(".", 1)
        if head in scope:
            return scope.get(rest)
        nested = _flat_of(doc, head)
        if rest in nested:
            return nested.get(rest)
        return doc.get(head, {}).get(rest) if isinstance(doc.get(head), dict) else None
    if path in scope:
        return scope.get(path)
    return (doc.get("flat") or {}).get(path)


# ---------------------------------------------------------------------------
# 通用渲染兜底
# ---------------------------------------------------------------------------
_GENERIC_CSS = """
.doc-sec { margin: 0 0 14px; }
.doc-sec > h3 { font-size: 13px; margin: 0 0 6px; color: #333;
  border-left: 3px solid #b8860b; padding-left: 6px; }
.doc-kv { border-collapse: collapse; width: 100%; font-size: 12px; }
.doc-kv td { border: 1px solid #d8d8d8; padding: 3px 6px; }
.doc-kv td.k { background: #faf7ef; width: 26%; color: #555; }
"""


def _generic_html(doc: Dict[str, Any]) -> str:
    parts: List[str] = []
    for sec in doc.get("sections") or []:
        parts.append(f"<section class='doc-sec'><h3>{_html.escape(str(sec['label']))}</h3>")
        if sec["mode"] == "table":
            rows = sec.get("rows") or []
            if rows:
                keys = list(rows[0].keys())
                head = "".join(f"<th>{_html.escape(k)}</th>" for k in keys)
                body = "".join(
                    "<tr>" + "".join(f"<td>{_html.escape(str(r.get(k, '')))}</td>" for k in keys) + "</tr>"
                    for r in rows
                )
                parts.append(f"<table class='doc-table'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        elif sec["mode"] == "text":
            parts.append(f"<p>{_html.escape(sec.get('value') or '')}</p>")
        else:
            cells = "".join(
                f"<tr><td class='k'>{_html.escape(str(f['label']))}</td>"
                f"<td>{_html.escape(str(f.get('value') or ''))}</td></tr>"
                for f in sec.get("fields") or []
            )
            parts.append(f"<table class='doc-kv'>{cells}</table>")
        parts.append("</section>")
    return "".join(parts)


def generic_css() -> str:
    """无版式时的最小样式（有版式则由 layout.html 自带）。"""
    return _GENERIC_CSS


def _md_table(rows: Rows, cols: Sequence[Dict[str, Any]]) -> str:
    if not rows or not cols:
        return "_（未识别到表格）_"
    head = "| " + " | ".join(str(c.get("label") or c["key"]) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = "\n".join(
        "| " + " | ".join(str(r.get(c["key"], "") or "").replace("|", "\\|") for c in cols) + " |"
        for r in rows
    )
    return "\n".join([head, sep, body])
