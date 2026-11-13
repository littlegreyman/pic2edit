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

v1.2 新增 ``_lint_v2_contract()``：只对显式声明 ``contract_version: 2`` 的包
强制执行「五件套」检查（README / standard.json / 字段对账 / 版式来源 / golden
样例）。存量包不声明就按 v1 规则走，只给一条 info —— 这样契约可分阶段纳管，
不会把仓库里所有历史包一次性判红。详见规格 §3.1.1 / §3.13.1。
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

#: v2 包契约（规格 §3.1.1 / §3.3.1）。只有显式声明 contract_version: 2 的包
#: 才受这些 MUST 约束；未声明 = v1 兼容，见 D4 渐进纳管。
CONTRACT_VERSION = 2
FORM_VALUES = ("declarative", "plugin")
LAYOUT_SOURCES = ("package", "generic", "none")
README_SECTIONS = (
    "功能与适用范围",
    "使用方法",
    "主要特点与已知边界",
    "字段来源与标准编号",
    "版本与变更记录",
    "版权与许可",
)


def _err(out: List[Dict[str, str]], level: str, where: str, msg: str) -> None:
    out.append({"level": level, "where": where, "message": msg})


def _flatten_doc(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """把 ``extract()`` 返回的 doc 递归展平成点路径 + ``[i]`` 索引（唯一入口）。

    声明式 doc 形如 ``{type, version, sections, flat, tables, missing}``；
    插件式 doc 是自由 dict（发票是 ``{buyer, seller, items, totals, ...}``）。
    两种形状都要能写进 ``expected.json`` 的 ``fields`` 路径里，故统一展平。
    """
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(_flatten_doc(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}[{i}]"
            out.update(_flatten_doc(v, key))
    elif prefix:
        out[prefix] = obj
    return out


def _expected_lookup(doc: Optional[Dict[str, Any]], path: str):
    """返回 ``(found, value)``；声明式 ``doc.flat`` 额外支持不带前缀的裸键。"""
    if not isinstance(doc, dict):
        return False, None
    flat = _flatten_doc(doc)
    if path in flat:
        return True, flat[path]
    dflat = doc.get("flat")
    if isinstance(dflat, dict) and path in dflat:
        return True, dflat[path]
    return False, None


def _as_text(value: Any) -> Optional[str]:
    """golden 比较统一按字符串；``None`` 与空串都视为「抽不到」。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return text or None


def _implemented_keys(manifest: Dict[str, Any], spec: Optional[Dict[str, Any]]) -> Set[str]:
    """实现里实际会产出的字段 key：声明式从 yaml 取，插件式从 manifest.fields 取。"""
    if manifest.get("entry"):
        return {str(k) for k in (manifest.get("fields") or []) if k}
    keys: Set[str] = set()
    for sec in (spec or {}).get("sections") or []:
        mode = (sec.get("mode") or "kv").lower()
        if mode == "table":
            for c in sec.get("columns") or []:
                if c.get("key"):
                    keys.add(str(c["key"]))
        elif mode == "text":
            if sec.get("key"):
                keys.add(str(sec["key"]))
        else:
            for f in sec.get("fields") or []:
                if f.get("key"):
                    keys.add(str(f["key"]))
    return keys


def _lint_v2_contract(pkg_dir: str, manifest: Dict[str, Any],
                      issues: List[Dict[str, str]]) -> Dict[str, Any]:
    """检查 v2 五件套。返回 ``{"v2": bool, "standard": ..., "spec": ...}`` 供复用。

    刻意**不抛异常**：任何坏文件都转成一条 error，让 ``validate`` / ``pack``
    能一次报全所有缺口，而不是修一个报一个。
    """
    if manifest.get("contract_version") != CONTRACT_VERSION:
        _err(issues, "info", "manifest.json",
             "未声明 contract_version: 2，按 v1 规则检查；建议补齐 v2 五件套（规格 §3.1.1）")
        return {"v2": False}

    # ---- ① README 六个固定章节 ----
    rp = os.path.join(pkg_dir, "README.md")
    if not os.path.isfile(rp):
        _err(issues, "error", "README.md", "v2 包缺少 README.md")
    else:
        with open(rp, encoding="utf-8") as f:
            readme = f.read()
        for sec in README_SECTIONS:
            if sec not in readme:
                _err(issues, "error", "README.md", f"缺少固定章节：{sec}")

    # ---- ② standard.json：字段字典 + 标准来源 ----
    std_name = manifest.get("standard") or "standard.json"
    std_path = os.path.join(pkg_dir, std_name)
    standard: Dict[str, Any] = {}
    if not os.path.isfile(std_path):
        _err(issues, "error", std_name, "v2 包缺少数据规范 standard.json")
    else:
        try:
            with open(std_path, encoding="utf-8") as f:
                standard = json.load(f)
            if not isinstance(standard, dict) or not isinstance(standard.get("fields"), dict):
                _err(issues, "error", std_name, "standard.json 需含 fields 字典")
                standard = {}
            else:
                meta = standard.get("standard") or {}
                for k in ("code", "license"):
                    if not meta.get(k):
                        _err(issues, "warning", std_name,
                             f"standard.{k} 未声明（标准编号 / 引用许可）")
        except Exception as e:
            _err(issues, "error", std_name, f"解析失败: {e}")
            standard = {}

    # ---- ③ form / layout_source ----
    form = manifest.get("form")
    if form not in FORM_VALUES:
        _err(issues, "error", "manifest.json",
             f"form 必须是 {'/'.join(FORM_VALUES)}（不得复用 kind），实得 {form!r}")
    src = manifest.get("layout_source")
    if src not in LAYOUT_SOURCES:
        _err(issues, "error", "manifest.json",
             f"layout_source 必须是 {'/'.join(LAYOUT_SOURCES)}，实得 {src!r}")
    else:
        layout = manifest.get("layout")
        if src in ("generic", "none") and layout:
            _err(issues, "error", "manifest.json",
                 f"layout_source={src} 时 MUST NOT 声明 layout 版式文件")
        if src == "package" and form == "declarative":
            f = layout or ("layout.html" if os.path.isfile(
                os.path.join(pkg_dir, "layout.html")) else None)
            if not f or not os.path.isfile(os.path.join(pkg_dir, f)):
                _err(issues, "error", "manifest.json",
                     "layout_source=package 的声明式包必须有存在的 layout 版式文件")
        if src == "package" and form == "plugin" and not manifest.get("entry"):
            _err(issues, "error", "manifest.json",
                 "layout_source=package 的插件式包必须有 entry（版式由 render_html() 提供）")

    # ---- ④ 字段对账：实现不得抽取标准之外的野字段 ----
    spec: Optional[Dict[str, Any]] = None
    if standard:
        try:
            if not manifest.get("entry"):
                spec = runtime.load_spec(
                    os.path.join(pkg_dir, manifest.get("schema") or "schema.yaml"))
        except Exception as e:
            _err(issues, "error", manifest.get("schema") or "schema.yaml",
                 f"字段对账时解析失败: {e}")
        declared = set(standard["fields"])
        wild = sorted(_implemented_keys(manifest, spec) - declared)
        if wild:
            _err(issues, "error", std_name,
                 f"实现字段不在 standard.json.fields 中（标准之外的野字段）：{', '.join(wild[:8])}"
                 + (f" …共 {len(wild)} 个" if len(wild) > 8 else ""))
        else:
            implemented = _implemented_keys(manifest, spec)
            pending = sorted(declared - implemented)
            if pending:
                _err(issues, "info", std_name,
                     f"标准定义了 {len(declared)} 个字段，实现定位了 {len(implemented)} 个；"
                     f"未定位 {len(pending)} 个（生成器骨架的 locator: pending 属正常）")

    # ---- ⑤ samples：.txt + 同名 .expected.json ----
    samp_dir = os.path.join(pkg_dir, "samples")
    txts = sorted(f for f in os.listdir(samp_dir) if f.lower().endswith(".txt"))         if os.path.isdir(samp_dir) else []
    if not txts:
        _err(issues, "error", "samples/", "v2 包至少需要 1 份 .txt 样张")
    for name in txts:
        ep = os.path.join(samp_dir, name[:-4] + ".expected.json")
        where = f"samples/{name[:-4]}.expected.json"
        if not os.path.isfile(ep):
            _err(issues, "error", where, f"样张 {name} 缺同名期望输出")
            continue
        try:
            with open(ep, encoding="utf-8") as f:
                golden = json.load(f)
            if not isinstance(golden, dict) or not isinstance(golden.get("fields"), dict):
                _err(issues, "error", where, "需含 fields 字典")
            elif not isinstance(golden.get("skip", []), list):
                _err(issues, "error", where, "skip 必须是数组")
        except Exception as e:
            _err(issues, "error", where, f"解析失败: {e}")
    return {"v2": True, "standard": standard, "spec": spec}


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
    # layout 只对声明式有意义：它指的是 layout.html 这个**版式文件**。
    # 插件式的 HTML 由 render_html() 现算，声明 layout 会让"文件存在就算过"的检查
    # 读到一个 .py 当版式——语义混淆，明确提示。
    layout = manifest.get("layout")
    if layout:
        _err(issues, "warning", "manifest.json",
             "entry 与 layout 同时声明：layout 是声明式的 HTML 版式文件，"
             "插件式的 HTML 应由 render_html() 提供，请删除 layout 键")
        if not os.path.isfile(os.path.join(pkg_dir, layout)):
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

    # v2 契约检查必须在插件/声明式分派之前：两条路径都要被同一套规则覆盖。
    # 未声明 contract_version 的包只拿一条 info（渐进纳管，见规格 D4）。
    _lint_v2_contract(pkg_dir, manifest, issues)

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

    # D2：manifest 是元信息的权威来源（§3.2），yaml 里若显式写了同名键却与它不同，
    # 说明两份文件靠人脑同步已经漂移。只对**显式声明**的键比对：yaml 不写就是
    # 「交给 manifest」，不算不一致。只告警不阻断，避免破坏存量包。
    for k in ("title", "version", "priority"):
        if k in spec and k in manifest and str(spec[k]) != str(manifest[k]):
            _err(issues, "warning", spec_name,
                 f"{k} 与 manifest 不一致：yaml={spec[k]!r} / manifest={manifest[k]!r}"
                 "（manifest 是元信息权威，以它为准）")

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
            # D1：start 在 §3.6.3 是 MUST（不写会把上半部分的「键：值」行当数据行，
            # 产出混入表头的假表）。运行语义已经安全（声明了找不到就返回空），
            # 但**不写**时此前没有任何提示；这里补 warning，不阻断以兼容存量包。
            if not sec.get("start"):
                _err(issues, "warning", where,
                     f"表格分段 {key} 缺 start：运行时无法界定明细区起点，"
                     "票据上半部分的键值行会被收成数据行（规范 §3.6.3 MUST）")
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


def _provider_for(pkg_dir: str, manifest: Dict[str, Any]):
    """取 provider：插件式 MUST 走注册表，声明式可本地构建。

    ``runtime.build_provider()`` 只认 ``schema.yaml``，对 ``entry`` 插件包会抛
    FileNotFoundError。此前内置 invoice 没有 samples/，这个分支从未被走到；
    v2 五件套给插件式包补了样张后才暴露。注册表是**唯一**能同时装载两种档位
    的入口（相对 import 的内置子包只能按包名导入），这里不复制第二份加载逻辑。
    """
    mid = manifest.get("id")
    if mid:
        try:
            from . import get_registry

            prov = get_registry().get(mid)
            pdir = getattr(prov, "package_dir", "") or getattr(prov, "pkg_dir", "")
            if prov is not None and pdir and                     os.path.realpath(pdir) == os.path.realpath(pkg_dir):
                return prov
        except Exception:
            pass  # 注册表扫不到（如临时目录里的新包）→ 回退本地构建
    return runtime.build_provider(pkg_dir, manifest)


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
        provider = _provider_for(pkg_dir, manifest)
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
        width = height = 0
        lp = os.path.join(samp_dir, name[:-4] + ".lines.json")
        if os.path.isfile(lp):
            with open(lp, encoding="utf-8") as f:
                raw = json.load(f) or []
            if isinstance(raw, dict):
                # 插件式抽取（发票）依赖图幅尺寸做几何判定。允许 .lines.json
                # 写成 {"lines": [...], "img_w": N, "img_h": M}；纯数组是兼容形式，
                # 尺寸仍为 0（声明式的文本路径不需要尺寸）。
                lines = raw.get("lines") or []
                width = int(raw.get("img_w") or 0)
                height = int(raw.get("img_h") or 0)
            else:
                lines = raw
        doc = provider.extract(text, lines, width, height)
        # 字段数必须说真话：声明式 doc 有 ``flat`` 字典，插件式 doc 是自由
        # dict（发票是 buyer/seller/items/...）。旧写法恒取 ``doc.get("flat")``，
        # 插件式明明抽出了字段却打印「字段 0 个」，与同一次运行的 golden
        # 「匹配 11」自相矛盾。两类文档各自按自己的真实口径计数，并把口径
        # 写进 ``fields_kind`` 让 CLI 能如实标注，不混称。
        dflat = doc.get("flat") if isinstance(doc, dict) else None
        if isinstance(dflat, dict):
            n_fields, fields_kind = len(dflat), "flat"
        elif doc:
            n_fields, fields_kind = len(_flatten_doc(doc)), "flattened"
        else:
            n_fields, fields_kind = 0, "none"
        row: Dict[str, Any] = {
            "sample": name,
            "hit": bool(doc),
            "fields": n_fields,
            "fields_kind": fields_kind,
            "tables": {k: len(v) for k, v in (doc or {}).get("tables", {}).items()},
        }
        # 逐字段对账是 **report**：只打印差异，不进 issues、不影响 ok / 退出码。
        # 沿用规格 §6.6「结构自检设门禁、抽取效果只报告」的既定口径。
        ep = os.path.join(samp_dir, name[:-4] + ".expected.json")
        if os.path.isfile(ep):
            try:
                with open(ep, encoding="utf-8") as f:
                    golden = json.load(f)
                expected = golden.get("fields") or {}
                skip = set(golden.get("skip") or [])
                report: Dict[str, Any] = {
                    "expected": len(expected), "matched": 0,
                    "mismatched": [], "missing": [], "skipped": sorted(skip),
                }
                for path, want in expected.items():
                    if path in skip:
                        continue
                    found, got = _expected_lookup(doc, path)
                    want_s, got_s = _as_text(want), _as_text(got)
                    if not found and want_s is None:
                        # 期望「抽不到」：路径不存在正是正确行为（用于锁住
                        # buyer.address="密码区" / 「合」行混入明细这类回归）。
                        report["matched"] += 1
                    elif not found:
                        report["missing"].append({"field": path, "expected": want})
                    elif want_s == got_s:
                        report["matched"] += 1
                    else:
                        report["mismatched"].append(
                            {"field": path, "expected": want, "actual": got})
                row["golden"] = report
            except Exception as e:
                row["golden"] = {"error": str(e)}
        results.append(row)
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
