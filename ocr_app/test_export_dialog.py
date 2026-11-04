#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出对话框单测（不真正 exec）：格式在下拉最前、文件名随格式改后缀、可缩放非全屏。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from PySide6.QtWidgets import QApplication

from gui import ExportDialog


def _app():
    return QApplication.instance() or QApplication([])


def test_default_name_and_format_drives_ext():
    _app()
    d = ExportDialog("report", "/tmp")
    assert d.selected_ext() == "docx"
    assert d.name_edit.text() == "report.docx"
    assert d.file_path() == os.path.join("/tmp", "report.docx")

    # 切到 Markdown：文件名后缀随之改变
    d.format_combo.setCurrentIndex(1)
    assert d.selected_ext() == "md"
    assert d.name_edit.text() == "report.md"
    assert d.file_path().endswith("report.md")

    # 切到 JSON
    d.format_combo.setCurrentIndex(4)
    assert d.name_edit.text() == "report.json"
    d.deleteLater()


def test_user_rename_preserves_stem_on_format_change():
    _app()
    d = ExportDialog("report", "/tmp")
    d.name_edit.setText("我的文档")
    d.format_combo.setCurrentIndex(2)  # txt
    assert d.name_edit.text() == "我的文档.txt"
    assert d.file_path().endswith("我的文档.txt")
    d.deleteLater()


def test_resizable_not_fullscreen():
    _app()
    d = ExportDialog("x", "/tmp")
    assert not d.isFullScreen()
    assert d.isModal()
    assert d.minimumWidth() == 460 and d.minimumHeight() == 240
    d.deleteLater()
