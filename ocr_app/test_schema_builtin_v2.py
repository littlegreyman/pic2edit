# -*- coding: utf-8 -*-
"""内置凭证包 v2 + 数电票独立 id 的可执行门禁（草案 P3 / 决策 1）。

为什么单独一份测试
------------------
P2 建立的是**工具链契约**，P3 要证明的是**存量内置包真的被纳管了**，以及
「数电票独立 schema id」不会破坏 auto 路由。这里全部走真实注册表与真实样张，
不 mock detect —— 草案 §7 点名要求「两个 detect 必须互斥」，只有真跑
`apply_schemas(..., "auto")` 才能证明。

顺带锁住 P3 真跑发现的两个缺陷（此前从未被走到，因为 invoice 原来没有 samples）：
1. `check_samples` 直接调 `runtime.build_provider`（只认 schema.yaml），
   对插件式包会抛 FileNotFoundError；
2. 它恒传 `width=height=0`，插件式发票的几何抽取拿不到 seller / items。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from schemas import apply_schemas, list_schemas, reset_registry  # noqa: E402
from schemas import cli as schema_cli  # noqa: E402
from schemas.lint import (  # noqa: E402
    CONTRACT_VERSION,
    README_SECTIONS,
    check_samples,
    lint_package,
)
from schemas.registry import BUILTIN_ROOT  # noqa: E402
from testdata import invoice_sample  # noqa: E402

BUILTIN_IDS = ("asset_card", "invoice_digital", "invoice")


@pytest.fixture(autouse=True)
def _fresh():
    reset_registry()
    yield
    reset_registry()


def _pkg(sid):
    return os.path.join(BUILTIN_ROOT, sid)


def _text(sid, name):
    return open(os.path.join(_pkg(sid), "samples", name + ".txt"),
                encoding="utf-8").read()


# ---------------------------------------------------------------- 1. 五件套与纳管
@pytest.mark.parametrize("sid", BUILTIN_IDS)
def test_builtin_package_declares_and_satisfies_v2(sid):
    pkg = _pkg(sid)
    mf = json.load(open(os.path.join(pkg, "manifest.json"), encoding="utf-8"))
    assert mf["contract_version"] == CONTRACT_VERSION, f"{sid} 未声明 v2"
    assert mf["form"] in ("declarative", "plugin")
    assert mf["layout_source"] in ("package", "generic", "none")
    assert mf.get("license")
    for name in ("README.md", "standard.json"):
        assert os.path.isfile(os.path.join(pkg, name)), f"{sid} 缺 {name}"
    readme = open(os.path.join(pkg, "README.md"), encoding="utf-8").read()
    for sec in README_SECTIONS:
        assert sec in readme, f"{sid} README 缺章节: {sec}"
    std = json.load(open(os.path.join(pkg, "standard.json"), encoding="utf-8"))
    assert isinstance(std.get("fields"), dict) and std["fields"]
    assert std["standard"]["code"] and std["standard"]["license"]

    res = lint_package(pkg)
    assert res["ok"], f"{sid} v2 校验不通过: {res['issues']}"
    assert not [i for i in res["issues"] if i["level"] == "error"]


def test_builtin_samples_have_expected_golden():
    for sid in BUILTIN_IDS:
        samp = os.path.join(_pkg(sid), "samples")
        txts = sorted(f for f in os.listdir(samp) if f.endswith(".txt"))
        assert txts, f"{sid} 没有输入样张"
        for t in txts:
            assert os.path.isfile(os.path.join(samp, t[:-4] + ".expected.json")), \
                f"{sid}/{t} 缺期望输出"


# ---------------------------------------------------------------- 2. 优先级与互斥
def test_schema_ids_unique_and_priority_order():
    infos = {i["id"]: i for i in list_schemas()}
    assert set(BUILTIN_IDS) <= set(infos), "内置包未被注册表发现"
    assert len(infos) == len(list_schemas()), "出现重复 schema id"
    assert (infos["asset_card"]["priority"] < infos["invoice_digital"]["priority"]
            < infos["invoice"]["priority"]), "priority 序必须 20 < 50 < 100"


@pytest.mark.parametrize("label, sid, name, expected", [
    ("纸电票", None, None, "invoice"),
    ("数电票", "invoice_digital", "digital_sample", "invoice_digital"),
    ("资产卡片", "asset_card", "basic", "asset_card"),
])
def test_auto_routes_to_mutually_exclusive_schema(label, sid, name, expected):
    text = _text(sid, name) if sid else invoice_sample.text()
    pg = {"page": 1, "raw_text": text, "text": text,
          "lines": [], "img_w": 0, "img_h": 0}
    apply_schemas([pg], "auto")
    assert pg.get("schema_id") == expected, f"{label} 被路由到了错误的包"


def test_detect_rules_are_mutually_exclusive():
    """直接对两个 provider 的 detect 取反例，防止只靠 priority 侥幸正确。"""
    reg = reset_registry()
    inv = reg.get("invoice")
    dig = reg.get("invoice_digital")
    digital_text = _text("invoice_digital", "digital_sample")
    paper_text = invoice_sample.text()
    assert inv.detect(paper_text, [], 0, 0) is True
    assert inv.detect(digital_text, [], 0, 0) is False, "invoice 必须排除数电票"
    assert dig.detect(digital_text, [], 0, 0) is True
    assert dig.detect(paper_text, [], 0, 0) is False, "invoice_digital 必须排除纸电票"


def test_forced_id_still_obeys_detect():
    """三态 <id> 仍走 detect（规格 C4）：强制指定 invoice 时数电票不应命中。"""
    digital_text = _text("invoice_digital", "digital_sample")
    pg = {"page": 1, "raw_text": digital_text, "text": digital_text,
          "lines": [], "img_w": 0, "img_h": 0}
    st = apply_schemas([pg], "invoice")
    assert st["hits"] == 0 and pg.get("schema_id") is None


# ---------------------------------------------------------------- 3. golden 与样张真跑
def test_all_builtin_goldens_match():
    for sid in BUILTIN_IDS:
        s = check_samples(_pkg(sid))
        assert s.get("error") is None, f"{sid} 样张加载失败: {s.get('error')}"
        assert s["samples"] >= 1 and s["hit_rate"] == 1.0, f"{sid} 命中率异常"
        for r in s["results"]:
            g = r.get("golden") or {}
            assert g.get("expected", 0) > 0, f"{sid}/{r['sample']} golden 是空的"
            assert not g.get("mismatched"), f"{sid}/{r['sample']}: {g['mismatched']}"
            assert not g.get("missing"), f"{sid}/{r['sample']}: {g['missing']}"
            assert r["fields"] > 0, f"{sid}/{r['sample']} 命中却报 0 字段（日志说假话）"


def test_sample_field_counts_match_doc_shape():
    """字段数口径必须随 doc 形状走：插件式展平、声明式 flat，不能恒取 flat。"""
    inv = check_samples(_pkg("invoice"))["results"][0]
    assert inv["fields_kind"] == "flattened" and inv["fields"] > 0
    for sid in ("asset_card", "invoice_digital"):
        for r in check_samples(_pkg(sid))["results"]:
            assert r["fields_kind"] == "flat" and r["fields"] > 0


def test_invoice_golden_locks_p1_fixes():
    """两条 null 期望锁住「密码区当地址」「合行入明细」回归。"""
    ep = os.path.join(_pkg("invoice"), "samples", "shanghai.expected.json")
    golden = json.load(open(ep, encoding="utf-8"))
    assert golden["fields"]["buyer.address"] is None
    assert golden["fields"]["items[1].name"] is None

    # 插件式包能跑 samples（_provider_for 修复）+ lines.json 带尺寸被消费
    lp = os.path.join(_pkg("invoice"), "samples", "shanghai.lines.json")
    raw = json.load(open(lp, encoding="utf-8"))
    assert isinstance(raw, dict) and raw["img_w"] > 0 and raw["img_h"] > 0
    s = check_samples(_pkg("invoice"))
    assert s["samples"] == 1 and s["hit_rate"] == 1.0
    from schemas.lint import _provider_for

    mf = json.load(open(os.path.join(_pkg("invoice"), "manifest.json"),
                        encoding="utf-8"))
    text = _text("invoice", "shanghai")
    doc = _provider_for(_pkg("invoice"), mf).extract(
        text, raw["lines"], raw["img_w"], raw["img_h"])
    assert doc["seller"]["name"], "带尺寸时几何抽取应拿到销售方"
    assert doc["items"], "带尺寸时几何抽取应拿到明细"
    assert "address" not in doc["buyer"], "P1 修复：不得再抽到密码区当地址"
    assert all(it.get("name") != "合" for it in doc["items"])


@pytest.mark.parametrize("sid", BUILTIN_IDS)
def test_cli_test_reports_golden_without_failing(sid, capsys):
    assert schema_cli.main(["test", sid]) == 0
    out = capsys.readouterr().out
    assert "只报告，不设门禁" in out
    assert "[不符]" not in out and "[缺失]" not in out
    assert "字段 0 个" not in out, f"{sid} 打印了与 golden 自相矛盾的字段数"
    if sid == "invoice":
        assert "叶字段（doc 展平）" in out, "插件式包必须标注字段数口径"
