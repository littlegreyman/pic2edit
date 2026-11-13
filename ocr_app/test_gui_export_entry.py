# -*- coding: utf-8 -*-
"""P0 门禁：GUI 导出只有一个入口，且 5 种格式都消费 schema。

背景见 ``docs/schema-package-v2-plan.md`` B1：此前 md/txt/docx 是手写旁路
（``texts=[it.result]`` -> ``assemble_document``），绕过 ``export_formats``，
导致凭证类型在 GUI 主线与导出产物里都不可见。

Qt 行为验证放在**独立子进程**：PySide6 offscreen 窗口在 pytest 进程的解释器
退出阶段会随机触发 ``corrupted double-linked list`` / SIGABRT（既有 GUI 测试
单独跑也会复现）。子进程用 ``os._exit(0)`` 跳过 Qt 析构，主进程只做纯 Python
产物对账，全量套件的退出码因此保持稳定。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile

from backend_api import MODE_AUTO
from testdata import invoice_sample
from utils.input_prep import InputItem

_OCR_DIR = os.path.dirname(os.path.abspath(__file__))
_EXTS = ("md", "txt", "docx", "html", "json")


def _item_with_invoice() -> InputItem:
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
    it.schema_version = page.get("schema_version")
    return it


def _gui_source() -> str:
    with open(os.path.join(_OCR_DIR, "gui.py"), encoding="utf-8") as f:
        return f.read()


def test_gui_export_has_no_bypass():
    """静态守卫：GUI 不再自己拼装导出，只调 export_by_ext。"""
    src = _gui_source()
    assert "export_by_ext(" in src
    for legacy in (
        "assemble_document(",
        "md_pages_to_docx(",
        "save_text(",
        "export_html(",
        "export_json(",
    ):
        assert legacy not in src, f"GUI 又出现了导出旁路: {legacy}"
    body = src.split("def export_selected(", 1)[1].split("\n    def ", 1)[0]
    assert "export_by_ext(" in body
    assert "texts = [it.result" not in body


# 子进程脚本：真调 MainWindow.export_selected 导出 5 种格式，然后立即 _exit。
# 占位符用 replace 注入，避免 format/f-string 与脚本内花括号冲突。
_GUI_PROBE = r'''
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, __OCR_DIR__)

from PySide6.QtWidgets import QApplication, QMessageBox
from backend_api import MODE_AUTO
from gui import MainWindow
from schemas import apply_schemas
from testdata import invoice_sample
from utils.input_prep import InputItem

it = InputItem("发票页", "/tmp/inv.png", "/tmp/inv.png")
page = invoice_sample.page()
apply_schemas([page], MODE_AUTO)
it.result = page["raw_text"]
it.lines = page["lines"]
it.img_w = page["img_w"]
it.img_h = page["img_h"]
it.schema = page["schema"]
it.schema_id = page["schema_id"]
it.schema_version = page.get("schema_version")

app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)

w = MainWindow()
doc = SimpleNamespace(source_path=os.path.join(__OUT_DIR__, "发票.png"), name="发票")
w.controller.items = [it]
w._gather_selection = lambda: ([doc], [])
w._build_units = lambda t, c: [(doc, [0])]

for ext in __EXTS__:
    out = os.path.join(__OUT_DIR__, "gui_out." + ext)
    w._ask_export_path = lambda base, out_dir, _o=out, _e=ext: (_o, _e)
    w.export_selected()
    if not os.path.getsize(out):
        sys.stderr.write("empty export: " + ext + "\n")
        os._exit(2)

os._exit(0)
'''


def test_gui_export_selected_routes_through_single_entry(tmp_path):
    """真调 GUI 导出，5 种格式产物与统一入口逐项对账。

    json 的 ``exported_at`` 与 docx 的 zip 时间戳随调用时刻变化，
    因此这两类按内容对账而非整体字节对账。
    """
    from utils.export_formats import export_by_ext

    script = (
        _GUI_PROBE.replace("__OCR_DIR__", repr(_OCR_DIR))
        .replace("__OUT_DIR__", repr(str(tmp_path)))
        .replace("__EXTS__", repr(_EXTS))
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=90
    )
    assert proc.returncode == 0, f"GUI 导出子进程失败:\n{proc.stdout}\n{proc.stderr}"

    it = _item_with_invoice()
    source = str(tmp_path / "发票.png")
    for ext in _EXTS:
        out = tmp_path / f"gui_out.{ext}"
        expected = tmp_path / f"direct_out.{ext}"
        export_by_ext([it], str(expected), ext, source)

        assert out.stat().st_size > 0
        if ext == "json":
            a = json.loads(out.read_text(encoding="utf-8"))
            b = json.loads(expected.read_text(encoding="utf-8"))
            a.pop("exported_at", None)
            b.pop("exported_at", None)
            assert a == b, "json: GUI 产物与统一入口不一致"
            assert a["pages"][0].get("schema_id") == "invoice"
        elif ext == "docx":
            with zipfile.ZipFile(out) as x, zipfile.ZipFile(expected) as y:
                assert x.read("word/document.xml") == y.read("word/document.xml")
        else:
            assert out.read_bytes() == expected.read_bytes(), (
                f"{ext}: GUI 产物与统一入口不一致"
            )
            if ext in ("md", "html"):
                assert "invoice" in out.read_text(encoding="utf-8"), (
                    f"{ext}: 产物缺 schema 溯源"
                )
