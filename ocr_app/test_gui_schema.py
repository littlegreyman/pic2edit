# -*- coding: utf-8 -*-
"""Phase 3.2.4：GUI / Worker / 导出链路对 schema 的接入测试（headless）。

守三件事：
1. 下拉里必然有「自动判定 / 不使用 / 已发现的每个 schema」——GUI 不写死任何票种；
2. Worker 按三态抽字段，且 ``none`` 时**一次都不抽**；
3. 导出时命中 schema 的页走 schema 版式（md / txt / html / json 四条路）。
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


from PySide6.QtWidgets import QApplication

from backend_api import MODE_AUTO, MODE_NONE
from config import Config
from testdata import invoice_sample
from utils.export_formats import (
    export_by_ext,
    export_html,
    export_json,
    export_markdown,
    export_text,
    item_text,
)
from utils.input_prep import InputItem


def _item_with_invoice() -> InputItem:
    """构造一个「已经识别完」的发票页：文本 + 坐标 + 抽好的 schema。"""
    from schemas import apply_schemas

    it = InputItem("发票页", "/tmp/inv.png", "/tmp/inv.png")
    page = invoice_sample.page()
    apply_schemas([page], MODE_AUTO)
    it.result = page["raw_text"]
    it.lines = page["lines"]
    it.img_w = page["img_w"]
    it.img_h = page["img_h"]
    it.schema = page["schema"]
    it.schema_id = page["schema_id"]
    return it


# ------------------------------------------------------------------ 配置
def test_config_defaults_to_auto():
    assert Config().schema_mode == MODE_AUTO


# ------------------------------------------------------------------ GUI 下拉
def test_schema_combo_lists_modes_and_discovered_schemas():
    app = QApplication.instance() or QApplication([])
    from gui import MainWindow

    w = MainWindow()
    combo = w.schema_combo
    datas = [combo.itemData(i) for i in range(combo.count())]
    assert MODE_AUTO in datas and MODE_NONE in datas
    assert "invoice" in datas, f"已发现的 schema 应出现在下拉里，实际: {datas}"
    # 默认选中项必须与配置一致（默认 auto），否则用户一开界面就改变了行为
    assert combo.currentData() == Config().schema_mode
    w.close()
    app.processEvents()


# ------------------------------------------------------------------ Worker
def _worker(mode):
    from worker import OcrWorker

    class _DummyBackend:
        name = "dummy"
        base_url = "http://localhost:0"

        def available(self):
            return True

    return OcrWorker(_DummyBackend(), [], "", [], schema_mode=mode)


def test_worker_extracts_schema_in_auto_mode():
    it = InputItem("发票页", "/tmp/inv.png", "/tmp/inv.png")
    it.lines, it.img_w, it.img_h = invoice_sample.lines(), invoice_sample.W, invoice_sample.H
    it.result = invoice_sample.text()

    _worker(MODE_AUTO)._apply_schema(it, it.result)
    assert it.schema_id == "invoice"
    assert it.schema["header"]["invoice_no"] == "12345678"


def test_worker_none_mode_never_extracts():
    it = InputItem("发票页", "/tmp/inv.png", "/tmp/inv.png")
    it.result = invoice_sample.text()

    _worker(MODE_NONE)._apply_schema(it, it.result)
    assert it.schema is None and it.schema_id is None


def test_worker_degrades_on_unknown_schema_id():
    """配置里留了一个已删除的 schema id：应降级为不启用，而不是让整次转换失败。"""
    it = InputItem("x", "/tmp/x.png", "/tmp/x.png")
    it.result = "发票"
    wk = _worker("ghost_schema")
    wk._apply_schema(it, it.result)
    assert it.schema is None
    assert wk.schema_mode == MODE_NONE      # 后续页不再反复尝试


# ------------------------------------------------------------------ 导出
def test_item_text_prefers_schema():
    it = _item_with_invoice()
    md = item_text(it, "md")
    assert "北京星辰科技有限公司" in md
    assert md != it.result               # 不再是 OCR 原文


def test_export_markdown_uses_schema(tmp_path):
    out = tmp_path / "a.md"
    export_markdown([_item_with_invoice()], str(out))
    text = out.read_text(encoding="utf-8")
    assert "壹万零陆佰元整" in text


def test_export_text_uses_schema_tab_layout(tmp_path):
    out = tmp_path / "a.txt"
    export_text([_item_with_invoice()], str(out))
    text = out.read_text(encoding="utf-8")
    assert "[购买方]" in text


def test_export_html_embeds_schema_fragment_and_css(tmp_path):
    out = tmp_path / "a.html"
    export_html([_item_with_invoice()], str(out))
    html = out.read_text(encoding="utf-8")
    assert "inv-wrap" in html                 # 票面 CSS
    assert "北京星辰科技有限公司" in html     # 票面片段
    assert "<table" in html


def test_export_json_carries_structured_schema(tmp_path):
    out = tmp_path / "a.json"
    export_json([_item_with_invoice()], "src.pdf", str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_pages"] == 1
    page = data["pages"][0]
    assert page["schema_id"] == "invoice"
    assert page["schema"]["total_incl_tax"]["figures"] == "10600.00"


def test_export_by_ext_dispatches_schema_paths(tmp_path):
    it = _item_with_invoice()
    for ext in ("md", "txt", "docx", "html", "json"):
        out = tmp_path / f"x.{ext}"
        export_by_ext([it], str(out), ext, "src.pdf")
        assert out.exists() and out.stat().st_size > 0, ext


def test_export_without_schema_stays_plain(tmp_path):
    it = InputItem("普通页", "/tmp/p.png", "/tmp/p.png")
    it.result = "这是一段普通正文。"
    out = tmp_path / "p.md"
    export_markdown([it], str(out))
    text = out.read_text(encoding="utf-8")
    assert "这是一段普通正文。" in text
    assert "[购买方]" not in text
