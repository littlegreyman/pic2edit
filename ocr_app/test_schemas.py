# -*- coding: utf-8 -*-
"""Phase 3.2 测试：schema 子系统（发现 / 三态选择 / 抽取 / 渲染）。

这些测试守的是四条**结构性**约束，而不是某个函数的细节：

1. ``none`` 态必须"什么都不做"——连 page 里的 ``schema`` 键都不写。
   这是"没有 schema 的世界"的回归基线，破了就意味着 schema 又偷偷耦合回流程里了。
2. 目录 drop-in 必须真的生效——往临时目录丢一个 schema 包，不重启、不改代码就能用；
   同名 id 要能被覆盖。这是"制作 schema"与"转换文档"两条流程分离的技术保证。
3. 坏包只能降级，不能连坐——一个写错的包不该让别人的发票转换失效。
4. 转换脚本里不得再出现发票字段知识（静态扫描，符号级）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import backend_api as api
from schemas import (
    MODE_AUTO,
    MODE_NONE,
    apply_schemas,
    get_registry,
    list_schemas,
    render_schema,
    reset_registry,
    schema_css,
)
from testdata import invoice_sample


@pytest.fixture(autouse=True)
def _fresh_registry():
    """每个用例前后都重建注册表，避免目录注入互相污染。"""
    reset_registry()
    yield
    reset_registry()


# ---------------------------------------------------------------- 发现
def test_invoice_is_discovered():
    infos = {i["id"]: i for i in list_schemas()}
    assert "invoice" in infos
    assert infos["invoice"]["kind"] == "builtin"
    assert infos["invoice"]["version"]


def _write_pkg(root, pid, *, version="0.1.0", title="测试 schema", body=None):
    d = os.path.join(root, pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"id": pid, "title": title, "version": version,
                   "entry": "provider.py", "priority": 1}, f, ensure_ascii=False)
    with open(os.path.join(d, "provider.py"), "w", encoding="utf-8") as f:
        f.write(body or (
            "class P:\n"
            "    id='x'; title='x'; version='0'; kind='user'; priority=1; layout=None\n"
            "    def detect(self, t, l, w, h): return False\n"
            "    def extract(self, t, l, w, h): return None\n"
            "    def render_md(self, d): return ''\n"
            "    def render_txt(self, d): return ''\n"
            "    def render_html(self, d): return ''\n"
            "    def css(self): return ''\n"
            "def get_provider(pkg, manifest=None): return P()\n"
        ))
    return d


def test_user_dir_dropin_is_found(tmp_path):
    _write_pkg(str(tmp_path), "mock_doc")
    reset_registry([str(tmp_path)])
    assert "mock_doc" in {i["id"] for i in list_schemas()}


def test_user_dir_overrides_same_id(tmp_path):
    _write_pkg(str(tmp_path), "invoice", version="9.9.9", title="用户覆盖版")
    reset_registry([str(tmp_path)])
    info = next(i for i in list_schemas() if i["id"] == "invoice")
    assert info["version"] == "9.9.9"
    assert info["title"] == "用户覆盖版"


def test_broken_package_degrades_only_itself(tmp_path):
    bad = os.path.join(str(tmp_path), "broken")
    os.makedirs(bad)
    with open(os.path.join(bad, "manifest.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    reg = reset_registry([str(tmp_path)])
    # 坏包被跳过并记录，内置 invoice 仍然可用
    assert reg.errors and reg.errors[0]["path"].endswith("broken")
    assert "invoice" in {i["id"] for i in list_schemas()}


# ---------------------------------------------------------------- 三态
def test_none_mode_touches_nothing():
    page = invoice_sample.page()
    stats = apply_schemas([page], MODE_NONE)
    assert stats["hits"] == 0
    assert "schema" not in page and "schema_id" not in page


def test_auto_mode_hits_invoice_sample():
    page = invoice_sample.page()
    stats = apply_schemas([page], MODE_AUTO)
    assert stats["hits"] == 1
    assert page["schema_id"] == "invoice"
    assert stats["applied"] == {"invoice": 1}


def test_auto_mode_marks_non_invoice_page_as_miss():
    page = {"page": 1, "text": "中华人民共和国注册会计师法\n第一章 总则",
            "raw_text": "中华人民共和国注册会计师法\n第一章 总则",
            "lines": [], "img_w": 0, "img_h": 0}
    stats = apply_schemas([page], MODE_AUTO)
    assert stats["hits"] == 0
    # 关键：键要写进去（值是 None），下游据此区分「启用未命中」与「没启用」
    assert page["schema"] is None and page["schema_id"] is None


def test_forced_id_uses_only_that_schema():
    page = invoice_sample.page()
    stats = apply_schemas([page], "invoice")
    assert stats["hits"] == 1 and page["schema_id"] == "invoice"


def test_unknown_id_raises_with_helpful_message():
    with pytest.raises(KeyError) as ei:
        api.resolve_schema("no_such_schema")
    msg = str(ei.value)
    assert "no_such_schema" in msg and "invoice" in msg   # 报错要能自证有哪些可用


# ---------------------------------------------------------------- 抽取
def test_invoice_fields_extracted():
    doc = api.resolve_schema("invoice").extract(*invoice_sample.sample())
    assert doc["header"]["invoice_no"] == "12345678"
    assert doc["header"]["invoice_code"] == "1100153130"
    assert doc["buyer"]["name"] == "北京星辰科技有限公司"
    assert doc["seller"]["name"] == "上海云图信息有限公司"
    assert doc["totals"]["total_excl_tax"] == "10000.00"
    assert doc["total_incl_tax"]["figures"] == "10600.00"
    assert doc["signatures"]["issuer"] == "王五"
    # 明细行：单位列为空时不能错位（错误会表现为单价填进数量）
    assert doc["items"], "明细行未抽到"
    row = doc["items"][0]
    assert row["quantity"] == "1"
    assert row["unit_price"] == "10000.00"
    assert row["tax_rate"] == "6%"


# ---------------------------------------------------------------- 渲染
def test_render_three_formats():
    page = invoice_sample.page()
    apply_schemas([page], MODE_AUTO)
    md = render_schema(page, "md")
    txt = render_schema(page, "txt")
    html = render_schema(page, "html")
    assert "北京星辰科技有限公司" in md and "壹万零陆佰元整" in md
    assert "[购买方]" in txt
    assert "北京星辰科技有限公司" in html and "<table" in html
    assert md != txt


def test_render_returns_empty_when_no_schema():
    page = {"page": 1, "text": "随便一段文字", "lines": [], "img_w": 0, "img_h": 0,
            "schema": None, "schema_id": None}
    for fmt in ("md", "txt", "html"):
        assert render_schema(page, fmt) == ""


def test_schema_css_follows_hits():
    page = invoice_sample.page()
    assert schema_css([page]) == ""          # 未命中 → 不拼票面样式
    apply_schemas([page], MODE_AUTO)
    assert "inv-wrap" in schema_css([page])  # 命中 → 带上


# ---------------------------------------------------------------- 静态守卫
def test_skill_has_no_invoice_field_knowledge():
    """转换脚本里不得再出现任何发票字段符号——schema 必须能被整体替换掉。"""
    here = os.path.dirname(os.path.abspath(__file__))
    skill = os.path.join(here, "..", ".workbuddy", "skills", "pic2edit-ocr",
                         "scripts", "pic2edit_convert.py")
    if not os.path.isfile(skill):        # CI 里 skill 不在仓库内时跳过
        pytest.skip("skill 脚本不在当前检出中")
    src = open(skill, encoding="utf-8").read()
    leaked = [s for s in ("_extract_invoice", "_render_md_schema", "_render_txt_schema",
                          "_invoice_fragment", "invoice_render", "INVOICE_CSS",
                          "_extract_items_from_lines", "_extract_totals_geom")
              if s in src]
    assert not leaked, f"发票字段知识仍残留在转换流程里: {leaked}"


def test_schema_package_imports_without_qt():
    """schema 子系统必须能在无 Qt 的环境里导入（skill 侧没有 PySide6）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    code = ("import sys; sys.path.insert(0, %r); import schemas; "
            "assert not [m for m in sys.modules if m.startswith('PySide6')]" % here)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
