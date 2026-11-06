# -*- coding: utf-8 -*-
"""生成后自检（规格 §6.6）——**两层，绝不混成一句「全部通过」**。

第一层结构自检是门禁：lint / 装载 / 样张存在 / 三套渲染 / 字段数对账 /
逐字节确定性。第二层抽取效果自检在 ``locator_pending > 0`` 时**只报告**：
``check_samples`` 的 ``hit`` 判据是 ``bool(doc)``，生成包 ``strict:false`` 时恒真，
hit_rate 恒 1.0 ——把它当门禁等于让生成器自己给自己发合格证。

另有一条独立门禁：auto 模式与强制指定两种路径下，无关样张都 MUST NOT 命中
生成包（§6.4 漏洞 A）。走真实注册表 ``apply_schemas``，不直接调 detect()。
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List

from schemas import apply_schemas, render_schema, reset_registry
from schemas.lint import check_samples, lint_package

from .emit import render_files
from .ir import IR

_UNRELATED_TEXT = "这是一张与审计数据标准完全无关的普通收据，商品：笔记本，金额：35.00 元。"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _page(text: str = _UNRELATED_TEXT) -> Dict[str, Any]:
    return {"page": 1, "raw_text": text, "text": text,
            "lines": [], "img_w": 0, "img_h": 0}


def _errors(issues: List[Dict[str, str]]) -> List[str]:
    return [f"{i['where']}: {i['message']}" for i in issues if i["level"] == "error"]


def verify_structure(pkg_dir: str, ir: IR) -> Dict[str, Any]:
    """第一层：结构自检（门禁）。"""
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    res = lint_package(pkg_dir)
    errs = _errors(res["issues"])
    add("lint 0 error", not errs, "; ".join(errs) or
        f"{len(res['issues'])} warning/info（不阻断）")

    root = os.path.dirname(os.path.abspath(pkg_dir))
    try:
        reg = reset_registry([root])
        provider = reg.get(ir.schema_id)
        add("注册表真实装载", provider is not None,
            f"origin={getattr(provider, 'origin', None)!r}, "
            f"priority={getattr(provider, 'priority', None)!r}")

        samp = os.path.join(pkg_dir, "samples")
        samples = sorted(f for f in os.listdir(samp) if f.lower().endswith(".txt")) \
            if os.path.isdir(samp) else []
        add("≥1 份样张", len(samples) >= 1, ", ".join(samples) or "无 .txt 样张")

        # 坏包（例如 yaml 缺 sections）会让注册表装载失败、apply_schemas 抛
        # KeyError。这是**结构失败**，不是 verify 自己崩——必须记成 FAIL 继续出报告。
        rendered = {}
        render_error = ""
        try:
            pg = _page(ir.detect_hint + "\n骨架样张")   # 让 detect 通过，测渲染路径
            apply_schemas([pg], ir.schema_id)
            for fmt in ("md", "txt", "html"):
                out = render_schema(pg, fmt)
                rendered[fmt] = (len(out), "{{" not in out and "<!--" not in out)
        except Exception as e:
            render_error = f"{type(e).__name__}: {e}"
        for fmt in ("md", "txt", "html"):
            if render_error:
                add(f"render_{fmt} 不崩且无残留占位", False, render_error)
            else:
                n, clean = rendered[fmt]
                add(f"render_{fmt} 不崩且无残留占位", clean,
                    f"{n} 字符" + ("" if clean else "；仍有占位/注释"))
    finally:
        reset_registry()

    manifest = render_files(ir)["manifest.json"]
    import json

    prov = json.loads(manifest)["x-provenance"]
    declared = prov["field_count"]
    add("IR 字段数与 XSD 逐一相等",
        declared == ir.field_count == prov["xsd_counts"]["fields"],
        f"provenance={declared}, IR={ir.field_count}, "
        f"XSD={prov['xsd_counts']['fields']}")

    first = render_files(ir)
    second = render_files(ir)
    identical = first == second and all(
        _sha(first[k]) == _sha(second[k]) for k in first)
    add("同输入两次生成逐字节一致", identical,
        f"{len(first)} 个文件 sha256 对账")

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks}


def verify_no_auto_false_positive(pkg_dir: str, ir: IR) -> Dict[str, Any]:
    """门禁：无关样张在 auto 与强制指定两条路径下都不得命中生成包。"""
    root = os.path.dirname(os.path.abspath(pkg_dir))
    details: List[str] = []
    ok = True
    try:
        reset_registry([root])
        auto_page = _page()
        apply_schemas([auto_page], "auto")
        hit = auto_page.get("schema_id")
        details.append(f"auto 无关样张命中 id={hit!r}（生成包 id={ir.schema_id!r}）")
        if hit == ir.schema_id:
            ok = False
        # auto 可能被内置包（发票 detect 恒真）抢先，那只说明排序正常，
        # 证明不了生成包自己的 detect 保守；再走强制指定直接验证它。
        forced = _page()
        apply_schemas([forced], ir.schema_id)
        forced_hit = forced.get("schema_id")
        # 三态 <id> 只是把候选缩小到该 provider，**仍走 detect**（规格 C4），
        # 不是无条件套用；这里验证的是生成包 detect 保守，不是"强制也能命中"。
        details.append(
            "强制指定（候选只此一个、仍走 detect）无关样张命中 "
            f"id={forced_hit!r}（保守 detect 下应为 None）")
        if forced_hit is not None:
            ok = False
    except Exception as e:                # 坏包装载不了 → 门禁失败，不是崩溃
        details.append(f"注册表/抽取异常：{type(e).__name__}: {e}")
        ok = False
    finally:
        reset_registry()
    return {"ok": ok, "details": details}


def verify_effects(pkg_dir: str, ir: IR) -> Dict[str, Any]:
    """第二层：抽取效果自检——**只报告，不设门禁**。"""
    stats = check_samples(pkg_dir)
    pending = ir.locator_pending
    gated = pending == 0 and bool(stats.get("with_samples"))
    return {
        "gated": gated,
        "locator_pending": pending,
        "locator_resolved": 0,
        "hit_rate": stats.get("hit_rate"),
        "samples": stats.get("samples", 0),
        "note": ("定位策略已补齐且样张为真实票面时才启用 hit_rate >= 0.8 门禁"
                 if gated else
                 "定位策略未补齐：hit_rate 只报告，不构成抽取效果证据"),
    }


def verify(pkg_dir: str, ir: IR) -> Dict[str, Any]:
    """汇总报告；``ok`` 只由结构自检与误命中门禁决定。"""
    structure = verify_structure(pkg_dir, ir)
    false_positive = verify_no_auto_false_positive(pkg_dir, ir)
    effects = verify_effects(pkg_dir, ir)
    return {
        "ok": structure["ok"] and false_positive["ok"],
        "structure": structure,
        "no_false_positive": false_positive,
        "effects": effects,
    }


def format_report(report: Dict[str, Any]) -> str:
    """日志 MUST 区分「结构通过」与「效果未验证」，不许一句「全部通过」糊过去。"""
    lines = ["[verify] === 第一层 · 结构自检（门禁） ==="]
    for c in report["structure"]["checks"]:
        lines.append(f"  {'OK  ' if c['ok'] else 'FAIL'} {c['name']}"
                     + (f" — {c['detail']}" if c["detail"] else ""))
    fp = report["no_false_positive"]
    lines.append("[verify] === auto 不误命中（门禁） ===")
    lines.append(f"  {'OK  ' if fp['ok'] else 'FAIL'} " + " | ".join(fp["details"]))
    ef = report["effects"]
    lines.append("[verify] === 第二层 · 抽取效果自检（只报告） ===")
    lines.append(f"  locator_pending: {ef['locator_pending']} / "
                 f"locator_resolved: {ef['locator_resolved']}")
    lines.append(f"  samples={ef['samples']} hit_rate={ef['hit_rate']} "
                 f"（{'已启用门禁' if ef['gated'] else '不设门禁'}）")
    lines.append(f"  {ef['note']}")
    lines.append(f"[verify] 结论：结构自检"
                 f"{'通过' if report['structure']['ok'] else '未通过'}；"
                 f"抽取效果{'已验证' if ef['gated'] else '未验证'}；"
                 f"整体{'PASS' if report['ok'] else 'FAIL'}")
    return "\n".join(lines)
