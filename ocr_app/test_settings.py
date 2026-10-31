#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置页单测（headless）：顶部下拉切换后端时 QStackedWidget 仅显示匹配子页面；
get_config 按当前后端读取 backend 与字段。

不依赖真实后端/网络：纯 UI 行为 + 配置读写验证。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from PySide6.QtWidgets import QApplication

import config
from gui import SettingsDialog


def make_dlg(backend):
    cfg = config.Config()
    cfg.backend = backend
    return SettingsDialog(cfg)


def test_backend_switch_shows_only_matching_page():
    app = QApplication.instance() or QApplication([])
    dlg = make_dlg("llama_cpp")
    # QStackedWidget 保证任意时刻只“显示”一个子页：切到某后端时 currentIndex 必须对应。
    for backend, expected_idx in (("ollama", 0), ("transformers", 1), ("llama_cpp", 2)):
        dlg.backend.setCurrentText(backend)
        assert dlg.stack.currentIndex() == expected_idx, (
            backend, dlg.stack.currentIndex())
    # 其余子页对应当前不可见（QStackedWidget 语义：仅 currentIndex 那一页可见）
    for i in range(dlg.stack.count()):
        if i == dlg.stack.currentIndex():
            continue
        assert not dlg.stack.widget(i).isVisible(), ("子页应隐藏", i)
    dlg.close()
    app.processEvents()
    print("PASS - 下拉切换后端 -> 仅显示匹配子页面（stack index 正确 + 其它隐藏）")


def test_get_config_backend_and_fields():
    app = QApplication.instance() or QApplication([])
    dlg = make_dlg("ollama")
    cfg = dlg.get_config()
    assert cfg.backend == "ollama"
    assert cfg.ollama_url == dlg.ollama_url.text().strip()
    # 切到 transformers：其字段应被正确读取
    dlg.backend.setCurrentText("transformers")
    dlg.tf_url.setText("http://example:9999")
    cfg2 = dlg.get_config()
    assert cfg2.backend == "transformers"
    assert cfg2.tf_url == "http://example:9999"
    # 模型参数来自当前页控件（默认即已验证组合）
    assert isinstance(cfg2.model_params, config.ModelParams)
    assert cfg2.model_params.temperature == 0.0
    # 切换后端不改变选中的 backend 字段读取
    dlg.backend.setCurrentText("llama_cpp")
    cfg3 = dlg.get_config()
    assert cfg3.backend == "llama_cpp"
    assert cfg3.llama_cpp_url == dlg.lcp_url.text().strip()
    dlg.close()
    app.processEvents()
    print("PASS - get_config 按当前后端读取 backend 与字段（逻辑不变）")


def main():
    test_backend_switch_shows_only_matching_page()
    test_get_config_backend_and_fields()
    print("ALL PASS: test_settings")


if __name__ == "__main__":
    main()
