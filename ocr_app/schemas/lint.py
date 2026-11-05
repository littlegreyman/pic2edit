# -*- coding: utf-8 -*-
"""schema 包的一致性检查（生产侧门禁）。

为什么必须有这个东西
--------------------
这个项目已经吃过一次亏：``docs/invoice_schema.md`` 与抽取代码各写一份字段表，
靠人脑同步，结果文档长期落后于实现。现在换成 ``schema.yaml`` + ``layout.html``
两文件，如果不做机器对账，三个月后必然出现「yaml 有、版式没有」或
「版式渲染了一个永远为空的字段」——而且没人会发现，因为页面看起来是正常的。

三条硬检查
----------
1. **字段 → 版式**：kv 字段在 layout 里有对应占位（否则抽了不显示）；
2. **版式 → 字段**：每个占位都能在 schema 里找到（否则永远渲染成空，等于打错字）；
3. **重复区块**：``data-repeat`` 指向的分段必须存在且是 table。

第 2 条是**错误**，第 1 条是**警告**——只取数不用版式是合法用法（比如只要 JSON）。

另有一项 ``check_samples()``：把 ``samples/`` 里的样张文本跑一遍抽取，
报命中率与字段召回。它是新 schema 上线前唯一能提前发现"抽歪了"的手段。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Set

from . import runtime

#: 与 runtime 保持一致的白名单，避免两处漂移。
_MODES = runtime.SECTION_MODES
_ANCHORS = runtime.ANCHORS


def _err(out: List[Dict[str, str]], level: str, where: str, msg: str) -> None:
    out.append({"level": level, "where": where, "message": msg})


def _lint_plugin(pkg_dir: str, manifest: Dict[str, Any],
                 issues: List[Dict[str, str]]) -> Dict[str, Any]:
    """Python 插件的检查：不查 yaml，只查协议实现是否完整、能否真正加载。"""
    entry = manifest["entry"]
    if not os.path.isfile(os.path.join(pkg_dir, entry)):
        _err(issues, "error", entry, "entry 指向的文件不存在")
        return {"ok": False, "issues": issues, "pkg": pkg_dir, "id": manifest.get("id")}
    try:
        from . import get_registry

        provider = get_registry().get(manifest.get("id") or "")
        if provider is None:
            _err(issues, "error", entry,
                 "注册表未能加载该包（检查 get_provider() 是否返回了实例）")
        else:
            for name in ("detect", "extract", "render_md", "render_txt", "render_html", "css"):
                if not callable(getattr(provider, name, None)):
                    _err(issues, "error", entry, f"实现缺少 {name}()")
    except Exception as e:
        _err(issues, "error", entry, f"加载失败: {e}")
    layout = manifest.get("layout")
    if layout and not os.path.isfile(os.path.join(pkg_dir, layout)):
        _err(issues, "error", layout, "文件不存在")
    ok = not any(i["level"] == "error" for i in issues)
    return {"ok": ok, "issues": issues, "pkg": pkg_dir, "id": manifest.get("id")}


def lint_package(pkg_dir: str) -> Dict[str, Any]:
    """检查一个 schema 目录。返回 ``{"ok": bool, "issues": [...]}``（不抛异常）。"""
    issues: List[Dict[str, str]] = []
    manifest_path = os.path.join(pkg_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        _err(issues, "error", "manifest.json", "文件不存在")
        return {"ok": False, "issues": issues, "pkg": pkg_dir}

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        _err(issues, "error", "manifest.json", f"解析失败: {e}")
        return {"ok": False, "issues": issues, "pkg": pkg_dir}

    mid = manifest.get("id")
    if not mid:
        _err(issues, "error", "manifest.json", "缺少 id")
    if not manifest.get("title"):
        _err(issues, "warning", "manifest.json", "缺少 title（GUI 下拉会显示 id）")
    if not manifest.get("version"):
        _err(issues, "warning", "manifest.json", "缺少 version（产物溯源需要）")

    # Python 插件（如内置发票）：没有 yaml 可查，改为查"协议是否实现完整"
    if manifest.get("entry"):
        return _lint_plugin(pkg_dir, manifest, issues)

    spec_name = manifest.get("schema") or "schema.yaml"
    spec_path = os.path.join(pkg_dir, spec_name)
    try:
        spec = runtime.load_spec(spec_path)
    except Exception as e:
        _err(issues, "error", spec_name, f"解析失败: {e}")
        return {"ok": False, "issues": issues, "pkg": pkg_dir}

    if mid and spec.get("id") and spec["id"] != mid:
        _err(issues, "error", spec_name, f"id 与 manifest 不一致：{spec['id']} != {mid}")

    detect = spec.get("detect") or {}
    if not any(detect.get(k) for k in ("any", "all", "none", "regex")):
        _err(issues, "warning", "detect", "没有任何识别规则：auto 模式下会匹配每一页")

    # ---- 分段 ----
    fields: Dict[str, Dict[str, Any]] = {}
    tables: Dict[str, Dict[str, Any]] = {}
    seen_sections: Set[str] = set()
    for i, sec in enumerate(spec.get("sections") or []):
        where = f"sections[{i}]"
        key = sec.get("key")
        if not key:
            _err(issues, "error", where, "缺少 key")
            continue
        if key in seen_sections:
            _err(issues, "error", where, f"key 重复: {key}")
        seen_sections.add(key)
        mode = (sec.get("mode") or "kv").lower()
        if mode not in _MODES:
            _err(issues, "error", where, f"未知 mode: {mode}（可选 {', '.join(_MODES)}）")
            continue
        if not sec.get("label"):
            _err(issues, "warning", where, f"分段 {key} 缺少 label（导出标题会用它）")

        if mode == "table":
            cols = sec.get("columns") or []
            if not cols:
                _err(issues, "error", where, f"表格分段 {key} 没有 columns")
            col_keys = set()
            for j, c in enumerate(cols):
                ck = c.get("key")
                if not ck:
                    _err(issues, "error", f"{where}.columns[{j}]", "缺少 key")
                    continue
                if ck in col_keys:
                    _err(issues, "error", f"{where}.columns[{j}]", f"列 key 重复: {ck}")
                col_keys.add(ck)
                fields.setdefault(ck, {"in": f"{key}.{ck}", "kind": "column"})
            tables[key] = sec
            continue

        if mode == "text":
            if not sec.get("regex") and not sec.get("field_label"):
                _err(issues, "info", where, f"text 分段 {key} 没有 regex：只会按标签取值")
            fields[key] = {"in": key, "kind": "text"}
            continue

        for j, fdef in enumerate(sec.get("fields") or []):
            fwhere = f"{where}.fields[{j}]"
            fk = fdef.get("key")
            if not fk:
                _err(issues, "error", fwhere, "缺少 key")
                continue
            if fk in seen_sections:
                _err(issues, "error", fwhere, f"key 与分段同名，版式取值会歧义: {fk}")
            if fk in fields:
                _err(issues, "error", fwhere, f"字段 key 重复: {fk}")
            if not fdef.get("label") and not fdef.get("regex"):
                _err(issues, "warning", fwhere, f"字段 {fk} 既无 label 也无 regex，永远抽不到")
            anchor = (fdef.get("anchor") or "right").lower()
            if anchor not in _ANCHORS:
                _err(issues, "error", fwhere, f"未知 anchor: {anchor}（可选 {', '.join(_ANCHORS)}）")
            if fdef.get("regex"):
                try:
                    re.compile(fdef["regex"])
                except re.error as e:
                    _err(issues, "error", fwhere, f"正则编译失败: {e}")
            fields[fk] = {"in": fk, "kind": "field", "required": bool(fdef.get("required"))}

    # ---- 版式对账 ----
    layout = manifest.get("layout") or (
        "layout.html" if os.path.isfile(os.path.join(pkg_dir, "layout.html")) else None
    )
    if layout:
        lp = os.path.join(pkg_dir, layout)
        if not os.path.isfile(lp):
            _err(issues, "error", layout, "文件不存在")
        else:
            with open(lp, encoding="utf-8") as f:
                html = f.read()
            html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
            used = set(re.findall(r"\{\{\s*!?\s*([A-Za-z_][\w.]*)\s*\}\}", html))
            repeats = set(re.findall(r'data-repeat="([\w.]+)"', html))
            scalars = {k for k, v in fields.items() if v["kind"] in ("field", "text")}
            for name in sorted(used):
                head = name.split(".", 1)[0]
                if head in fields or head in tables or name in fields:
                    continue
                _err(issues, "error", layout,
                     f"占位 {{{{{name}}}}} 在 schema 里不存在（会永远渲染成空）")
            for name in sorted(repeats):
                if name not in tables:
                    _err(issues, "error", layout,
                         f'data-repeat="{name}" 不是 table 分段（会渲染成空块）')
            unused = sorted(k for k in scalars if k not in used and k not in repeats)
            if unused:
                _err(issues, "warning", layout,
                     f"这些字段在版式里没有出现：{', '.join(unused)}")

    ok = not any(i["level"] == "error" for i in issues)
    return {"ok": ok, "issues": issues, "pkg": pkg_dir, "id": mid}


def check_samples(pkg_dir: str, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """用 ``samples/`` 里的样张跑一遍抽取，报命中率与字段召回。

    样张是纯文本（``.txt``，可附同名 ``.lines.json`` 提供行框）。没有样张时
    返回 ``{"samples": 0}``——不是错误，但新 schema 上线前应该有。
    """
    if manifest is None:
        mp = os.path.join(pkg_dir, "manifest.json")
        if not os.path.isfile(mp):
            return {"samples": 0, "error": "缺少 manifest.json"}
        with open(mp, encoding="utf-8") as f:
            manifest = json.load(f)
    try:
        provider = runtime.build_provider(pkg_dir, manifest)
    except Exception as e:
        return {"samples": 0, "error": str(e)}

    samp_dir = os.path.join(pkg_dir, "samples")
    if not os.path.isdir(samp_dir):
        return {"samples": 0, "with_samples": False}
    files = sorted(f for f in os.listdir(samp_dir) if f.lower().endswith(".txt"))
    results = []
    for name in files:
        with open(os.path.join(samp_dir, name), encoding="utf-8") as f:
            text = f.read()
        lines: Sequence[Dict[str, Any]] = []
        lp = os.path.join(samp_dir, name[:-4] + ".lines.json")
        if os.path.isfile(lp):
            with open(lp, encoding="utf-8") as f:
                lines = json.load(f) or []
        doc = provider.extract(text, lines, 0, 0)
        results.append({
            "sample": name,
            "hit": bool(doc),
            "fields": len(doc.get("flat", {})) if doc else 0,
            "tables": {k: len(v) for k, v in (doc or {}).get("tables", {}).items()},
        })
    hits = sum(1 for r in results if r["hit"])
    return {
        "samples": len(results),
        "with_samples": True,
        "hit_rate": (hits / len(results)) if results else 0.0,
        "results": results,
    }


def lint_dir(root: str) -> List[Dict[str, Any]]:
    """检查一个目录下的所有 schema 包。"""
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        pkg = os.path.join(root, name)
        if os.path.isdir(pkg) and os.path.isfile(os.path.join(pkg, "manifest.json")):
            out.append(lint_package(pkg))
    return out
