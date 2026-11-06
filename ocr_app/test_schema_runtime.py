# -*- coding: utf-8 -*-
"""Phase 4：声明式 schema（runtime / layout / lint / cli）的行为测试。

守的是"填一张表就能加一种凭证"这句承诺的可验证部分：
  · 一份 yaml 真的能抽出字段、真的能出表格；
  · 版式真的能把字段填进去、能重复行、能防住 HTML 注入；
  · 两个文件真的能被 lint 抓出不一致（本项目吃过双写漂移的亏）；
  · CLI 真的能脚手架 + 校验，不是摆设。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schemas import reset_registry  # noqa: E402
from schemas import runtime  # noqa: E402
from schemas.lint import check_samples, lint_package  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(HERE, "schemas", "asset_card")


def _manifest():
    with open(os.path.join(ASSET_DIR, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def provider():
    return runtime.build_provider(ASSET_DIR, _manifest())


@pytest.fixture(scope="module")
def sample_text():
    with open(os.path.join(ASSET_DIR, "samples", "basic.txt"), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- detect
def test_detect_hits_asset_card(provider, sample_text):
    assert provider.detect(sample_text, [], 0, 0) is True


def test_detect_rejects_invoice_like_text(provider):
    """发票里也会出现"资产"字样，靠 none 规则与 required 把它挡在外面。"""
    text = "增值税专用发票\n发票代码：011002000111\n价税合计（大写）：壹万元整"
    assert provider.detect(text, [], 0, 0) is False


def test_detect_without_rules_requires_text():
    p = runtime.DeclarativeProvider(ASSET_DIR, {}, {
        "id": "x", "sections": [{"key": "a", "mode": "text"}]})
    assert p.detect("正文", [], 0, 0) is True
    assert p.detect("   ", [], 0, 0) is False


# ---------------------------------------------------------------- extract
def test_extract_fields(provider, sample_text):
    doc = provider.extract(sample_text, [], 0, 0)
    assert doc is not None
    flat = doc["flat"]
    assert flat["card_no"] == "AC-2026-0001"
    assert flat["asset_no"] == "ZC-000123"
    assert flat["name"] == "数控车床"
    assert flat["original_cost"] == "286000.00"
    assert flat["remarks"].startswith("2026 年 1 月起计提折旧")


def test_extract_table_rows(provider, sample_text):
    doc = provider.extract(sample_text, [], 0, 0)
    rows = doc["tables"]["depreciation"]
    assert len(rows) == 2, rows
    assert rows[0]["period"] == "2026-01"
    assert rows[0]["amount"] == "2264.17"
    assert rows[1]["net_value"] == "236558.00"


def test_extract_table_keeps_surplus_cells_in_last_column():
    """OCR 把最后一格切成多格时，多出来的并入最后一列，不静默丢失。

    README 一直这么写，但实现里的 elif 分支不可达（i < len(cells) 恒真），
    修规格时才发现。这里钉住，防止再次"文档承诺了、代码没做到"。
    """
    p = runtime.DeclarativeProvider(ASSET_DIR, {}, {
        "id": "x", "strict": False,
        "sections": [{"key": "t", "mode": "table", "start": "明细", "stop": "合计",
                      "columns": [{"key": "a", "label": "期间"},
                                  {"key": "b", "label": "金额"}]}]})
    # 无坐标：按 2+ 空白切列，得到 3 格（最后一格被切成"880.00"与"含税"）
    text = "明细\n期间  金额\n2026-01  880.00  含税\n合计"
    doc = p.extract(text, [], 0, 0)
    rows = doc["tables"]["t"]
    assert len(rows) == 1, rows
    assert rows[0]["a"] == "2026-01"
    assert rows[0]["b"] == "880.00 含税"


def test_extract_table_respects_stop(provider, sample_text):
    """合计行之后不再收：收了就会多出一条"合计"假明细。"""
    doc = provider.extract(sample_text, [], 0, 0)
    joined = json.dumps(doc["tables"]["depreciation"], ensure_ascii=False)
    assert "合计" not in joined


def test_extract_requires_boundary(provider):
    """没有"折旧明细"这个 start 标题 → 表格段为空，而不是把上半页当表。"""
    text = "固定资产卡片\n卡片编号：A-1\n资产编号：Z-1\n资产名称：车床\n期间  月数  本期折旧\n2026-01  1  10"
    doc = provider.extract(text, [], 0, 0)
    assert doc is None or doc["tables"]["depreciation"] == []


def test_extract_missing_required_returns_none(provider):
    """required 缺一即整页未命中：宁可不出表，也不出半空的表。"""
    text = "固定资产卡片\n资产编号：ZC-1\n资产名称：车床\n备注：缺少卡片编号"
    assert provider.extract(text, [], 0, 0) is None


def test_strict_false_tolerates_missing_required():
    p = runtime.DeclarativeProvider(ASSET_DIR, {}, {
        "id": "x", "strict": False,
        "sections": [{"key": "h", "mode": "kv", "fields": [
            {"key": "a", "label": "甲", "required": True},
            {"key": "b", "label": "乙"},
        ]}]})
    doc = p.extract("乙：2", [], 0, 0)
    assert doc is not None
    assert doc["flat"] == {"b": "2"}
    assert doc["missing"]


# ---------------------------------------------------------------- 坐标锚点
def _ln(text, x0, y0, x1, y1):
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_anchor_right_uses_same_visual_row():
    p = runtime.DeclarativeProvider(ASSET_DIR, {}, {
        "id": "x", "sections": [{"key": "h", "mode": "kv", "fields": [
            {"key": "v", "label": "原值", "anchor": "right"}]}]})
    lines = [
        _ln("原值", 100, 100, 140, 120),
        _ln("286000.00", 400, 100, 480, 120),       # 同一行的右侧 → 命中
        _ln("净值", 100, 200, 140, 220),
        _ln("241150.00", 400, 200, 480, 220),
    ]
    assert p.extract("原值\n净值", lines, 800, 400)["flat"]["v"] == "286000.00"


def test_anchor_below_picks_nearest():
    p = runtime.DeclarativeProvider(ASSET_DIR, {}, {
        "id": "x", "sections": [{"key": "h", "mode": "kv", "fields": [
            {"key": "v", "label": "存放地点", "anchor": "below"}]}]})
    lines = [
        _ln("存放地点", 100, 100, 180, 120),
        _ln("二号库房", 100, 140, 200, 160),
        _ln("一号车间", 100, 200, 200, 220),
    ]
    assert p.extract("存放地点", lines, 800, 400)["flat"]["v"] == "二号库房"


def test_inline_label_wins_over_anchor():
    """同行有值就不该再去坐标里找——坐标可能给到隔壁字段的值。"""
    p = runtime.DeclarativeProvider(ASSET_DIR, {}, {
        "id": "x", "sections": [{"key": "h", "mode": "kv", "fields": [
            {"key": "v", "label": "原值", "anchor": "right"}]}]})
    lines = [_ln("原值", 100, 100, 140, 120), _ln("错的值", 400, 100, 480, 120)]
    assert p.extract("原值：286000.00", lines, 800, 400)["flat"]["v"] == "286000.00"


# ---------------------------------------------------------------- 渲染
def test_render_md_has_sections_and_table(provider, sample_text):
    md = provider.render_md(provider.extract(sample_text, [], 0, 0))
    assert "### 一、基本信息" in md
    assert "**卡片编号**：AC-2026-0001" in md
    assert "| --- |" in md
    assert "| 2026-01 |" in md


def test_render_txt_is_tab_separated(provider, sample_text):
    txt = provider.render_txt(provider.extract(sample_text, [], 0, 0))
    assert "[一、基本信息]" in txt
    assert "卡片编号\tAC-2026-0001" in txt


def test_render_html_uses_layout(provider, sample_text):
    html = provider.render_html(provider.extract(sample_text, [], 0, 0))
    assert 'class="asset-card"' in html
    assert "AC-2026-0001" in html
    assert "2026-01" in html and "236558.00" in html
    assert "{{" not in html, "不得残留未替换的占位"
    assert "<!--" not in html, "模板注释不得进入产物"


def test_layout_strips_comments_before_repeat():
    """注释里写着 data-repeat 的用法示例，不能被当成真区块。"""
    tpl = ('<!-- <tr data-repeat="items"><td>{{ a }}</td></tr> -->'
           '<p>{{ a }}</p>')
    out = runtime.render_layout(tpl, {"flat": {"a": "1"}, "tables": {}})
    assert out.strip() == "<p>1</p>"


def test_layout_escapes_html():
    """OCR 出来的字里可能带尖括号，直接拼进去就是注入。"""
    out = runtime.render_layout("<p>{{ a }}</p>", {"flat": {"a": "<script>x</script>"}})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_layout_repeat_skips_when_no_rows():
    tpl = '<table><tr data-repeat="items"><td>{{ a }}</td></tr></table>'
    out = runtime.render_layout(tpl, {"flat": {}, "tables": {"items": []}})
    assert out == "<table></table>"


def test_render_html_falls_back_when_layout_broken(monkeypatch):
    p = runtime.build_provider(ASSET_DIR, _manifest())
    monkeypatch.setattr(p, "_layout", lambda: "<style>.a{}</style><div>{{ x }}</div>")
    monkeypatch.setattr(runtime, "render_layout", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    doc = {"sections": [{"key": "h", "label": "一", "mode": "kv",
                         "fields": [{"key": "x", "label": "甲", "value": "1"}]}],
           "flat": {"x": "1"}, "tables": {}}
    html = p.render_html(doc)
    assert "doc-kv" in html and "1" in html


def test_css_extracted_from_layout(provider):
    css = provider.css()
    assert ".asset-card" in css
    assert "<style>" not in css


# ---------------------------------------------------------------- lint
def test_lint_asset_card_clean():
    res = lint_package(ASSET_DIR)
    assert res["ok"], res["issues"]


def test_lint_detects_unknown_placeholder(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "manifest.json").write_text(json.dumps(
        {"id": "demo", "title": "示例", "version": "1.0.0", "layout": "layout.html"}),
        encoding="utf-8")
    (pkg / "schema.yaml").write_text(
        "id: demo\nsections:\n  - key: h\n    mode: kv\n    fields:\n"
        "      - {key: a, label: 甲}\n", encoding="utf-8")
    (pkg / "layout.html").write_text("<p>{{ typo_field }}</p>", encoding="utf-8")
    res = lint_package(str(pkg))
    assert not res["ok"]
    assert any("typo_field" in i["message"] for i in res["issues"])


def test_lint_detects_bad_repeat(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "manifest.json").write_text(json.dumps(
        {"id": "demo", "title": "示例", "version": "1.0.0", "layout": "layout.html"}),
        encoding="utf-8")
    (pkg / "schema.yaml").write_text(
        "id: demo\nsections:\n  - key: h\n    mode: kv\n    fields:\n"
        "      - {key: a, label: 甲}\n", encoding="utf-8")
    (pkg / "layout.html").write_text(
        '<table><tr data-repeat="no_such_table"><td>{{ a }}</td></tr></table>', encoding="utf-8")
    res = lint_package(str(pkg))
    assert not res["ok"]
    assert any("no_such_table" in i["message"] for i in res["issues"])


def test_lint_warns_unused_field(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "manifest.json").write_text(json.dumps(
        {"id": "demo", "title": "示例", "version": "1.0.0", "layout": "layout.html"}),
        encoding="utf-8")
    (pkg / "schema.yaml").write_text(
        "id: demo\nsections:\n  - key: h\n    mode: kv\n    fields:\n"
        "      - {key: a, label: 甲}\n      - {key: b, label: 乙}\n", encoding="utf-8")
    (pkg / "layout.html").write_text("<p>{{ a }}</p>", encoding="utf-8")
    res = lint_package(str(pkg))
    assert res["ok"], "未使用的字段只是警告，不该让校验失败"
    assert any(i["level"] == "warning" and "b" in i["message"] for i in res["issues"])


def test_lint_detects_duplicate_key(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "manifest.json").write_text(json.dumps(
        {"id": "demo", "title": "示例", "version": "1.0.0"}), encoding="utf-8")
    (pkg / "schema.yaml").write_text(
        "id: demo\nsections:\n  - key: h\n    mode: kv\n    fields:\n"
        "      - {key: a, label: 甲}\n      - {key: a, label: 乙}\n", encoding="utf-8")
    res = lint_package(str(pkg))
    assert not res["ok"]
    assert any("重复" in i["message"] for i in res["issues"])


def test_lint_bad_regex(tmp_path):
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "manifest.json").write_text(json.dumps(
        {"id": "demo", "title": "示例", "version": "1.0.0"}), encoding="utf-8")
    (pkg / "schema.yaml").write_text(
        "id: demo\nsections:\n  - key: h\n    mode: kv\n    fields:\n"
        "      - {key: a, label: 甲, regex: '([unclosed'}\n", encoding="utf-8")
    res = lint_package(str(pkg))
    assert not res["ok"]


def test_samples_all_hit():
    res = check_samples(ASSET_DIR)
    assert res["samples"] == 2
    assert res["hit_rate"] == 1.0, res


# ---------------------------------------------------------------- 端到端（注册表）
def test_auto_prefers_asset_card_over_invoice(monkeypatch):
    """资产卡片的 priority 更小，先试；但绝不能把发票抢过去。"""
    from testdata import invoice_sample

    reset_registry()
    pages = [invoice_sample.page()]
    from schemas import apply_schemas

    apply_schemas(pages, "auto")
    assert pages[0]["schema_id"] == "invoice"


def test_auto_picks_asset_card(tmp_path):
    reset_registry()
    from schemas import apply_schemas, render_schema

    text = (ASSET_DIR and open(os.path.join(ASSET_DIR, "samples", "basic.txt"),
                               encoding="utf-8").read())
    pages = [{"page": 1, "raw_text": text, "lines": [], "img_w": 0, "img_h": 0}]
    apply_schemas(pages, "auto")
    assert pages[0]["schema_id"] == "asset_card"
    assert "asset-card" in render_schema(pages[0], "html")


def test_none_mode_does_not_touch_pages():
    from schemas import MODE_NONE, apply_schemas

    pages = [{"page": 1, "raw_text": "固定资产卡片", "lines": []}]
    apply_schemas(pages, MODE_NONE)
    assert "schema" not in pages[0]


# ---------------------------------------------------------------- CLI
def test_cli_list(capsys):
    from schemas import cli

    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "asset_card" in out and "invoice" in out


def test_cli_new_and_validate(tmp_path, capsys):
    from schemas import cli

    assert cli.main(["new", "demo_x", "--title", "示例", "--dir", str(tmp_path)]) == 0
    pkg = tmp_path / "demo_x"
    for name in ("manifest.json", "schema.yaml", "layout.html"):
        assert (pkg / name).is_file(), name
    assert cli.main(["validate", str(pkg)]) == 0
    out = capsys.readouterr().out
    assert "无问题" in out


def test_cli_new_refuses_existing(tmp_path):
    from schemas import cli

    pkg = tmp_path / "dup"
    pkg.mkdir()
    assert cli.main(["new", "dup", "--dir", str(tmp_path)]) == 2


def test_cli_test_reports_hit_rate(capsys):
    from schemas import cli

    assert cli.main(["test", ASSET_DIR]) == 0
    assert "命中率 100%" in capsys.readouterr().out
