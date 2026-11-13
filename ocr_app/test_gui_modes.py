# -*- coding: utf-8 -*-
"""P1 门禁：GUI 模式白名单、默认原文视图、残留坐标清理。

决策依据见 ``docs/schema-package-v2-plan.md`` §7：
- html_table 从 GUI 下拉删除，但后端字典 / CLI / skill 保留；
- 编辑器默认原文，字段化视图为显式勾选且只读。

Qt 窗口验证放在子进程并用 ``os._exit(0)`` 跳过析构，理由同
``test_gui_export_entry.py``：PySide6 offscreen 在 pytest 进程退出阶段会随机 SIGABRT。
"""
from __future__ import annotations

import os
import subprocess
import sys

from config import GUI_MODE_KEYS, PROMPT_LABELS
from utils.input_prep import InputItem

_OCR_DIR = os.path.dirname(os.path.abspath(__file__))


def test_gui_mode_whitelist_is_narrower_than_backend():
    assert "html_table" in PROMPT_LABELS, "CLI/skill 仍应支持 html_table"
    assert "html_table" not in GUI_MODE_KEYS, "html_table 不得出现在 GUI 下拉"
    assert set(GUI_MODE_KEYS) <= set(PROMPT_LABELS)
    with open(os.path.join(_OCR_DIR, "gui.py"), encoding="utf-8") as f:
        src = f.read()
    assert "for key in GUI_MODE_KEYS:" in src
    assert "for key, label in PROMPT_LABELS.items():" not in src
    assert "pt in GUI_MODE_KEYS" in src, "恢复上次参数也要走白名单，避免状态不一致"
    assert "字段化预览" in src and "setChecked(False)" in src
    assert "item_text(it, \"md\")" in src


_GUI_PROBE = r'''
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, __OCR_DIR__)

from PySide6.QtWidgets import QApplication
from backend_api import MODE_AUTO
from config import GUI_MODE_KEYS
from gui import MainWindow
from schemas import apply_schemas
from testdata import invoice_sample
from utils.input_prep import InputItem

app = QApplication.instance() or QApplication([])
w = MainWindow()

# ① 下拉只含白名单
datas = [w.template_combo.itemData(i) for i in range(w.template_combo.count())]
assert tuple(datas) == GUI_MODE_KEYS, datas
assert "html_table" not in datas

# ② 配置里残留不支持的模式时，显式回落且下拉与 cfg 一致
w.cfg.prompt_template = "html_table"
w._sync_template_combo()
assert w.cfg.prompt_template == "markdown", w.cfg.prompt_template
assert w.template_combo.currentData() == "markdown"

# ③ 默认原文；勾选后是字段化视图且只读
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
w.controller.items = [it]
w.current = 0
assert not w.schema_view.isChecked()
w._refresh_current_view()
assert w.editor.toPlainText() == it.result
assert not w.editor.isReadOnly()

w.schema_view.setChecked(True)
assert w.editor.toPlainText() != it.result, "勾选后未切到字段化视图"
assert w.editor.isReadOnly(), "字段化视图必须只读，否则会回写污染原文"
w.schema_view.setChecked(False)
assert not w.editor.isReadOnly()
assert w.editor.toPlainText() == it.result

os.write(1, b"OK\n")  # os._exit 不刷缓冲，必须用无缓冲写
os._exit(0)
'''


def test_gui_dropdown_and_editor_defaults():
    script = _GUI_PROBE.replace("__OCR_DIR__", repr(_OCR_DIR))
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=90
    )
    assert proc.returncode == 0 and "OK" in proc.stdout, (
        f"GUI 探针失败:\n{proc.stdout}\n{proc.stderr}"
    )


class _DummyBackend:
    name = "dummy"
    base_url = "http://localhost:0"

    def available(self):
        return True


def test_worker_clears_stale_lines_before_conversion(monkeypatch):
    """同一个 item 反复转换时，上一轮的行框不得泄漏到本轮。"""
    import worker as worker_mod

    it = InputItem("p", "/tmp/a.png", "/tmp/a.png")
    it.lines = [{"text": "stale"}]
    it.img_w, it.img_h = 100, 100
    seen = {}

    def fake_pipeline(items, **_kw):
        seen["lines"] = items[0].lines
        items[0].result = "fresh"
        return items

    monkeypatch.setattr(worker_mod, "pipeline_run", fake_pipeline)
    w = worker_mod.OcrWorker(
        _DummyBackend(), [it], "", [0], schema_mode="none"
    )
    monkeypatch.setattr(w, "_apply_schema", lambda *_a: None)
    w.run()

    assert seen.get("lines") is None, "pipeline 启动前必须清掉残留坐标"
    assert it.result == "fresh"
