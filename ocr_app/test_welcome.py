"""启动欢迎页单测（headless）：about 常量 + WelcomeDialog 基本可用。

验证 Task #42 的不变量：
- APP_NAME / APP_VERSION / APP_AUTHOR(littlegreyman) / APP_RELEASE_DATE / APP_TAGLINE 存在；
- APP_TAGLINE 控制在 20 个中文字符以内；
- WelcomeDialog 可实例化，含“确认开始”按钮，accept 返回 Accepted。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from PySide6.QtWidgets import QApplication, QDialog

import about
from about import WelcomeDialog


def test_constants():
    assert about.APP_NAME == "pic2edit"
    assert about.APP_VERSION
    assert about.APP_AUTHOR == "littlegreyman"
    assert about.APP_RELEASE_DATE
    # 简介 ≤ 20 个字符（中文按字符计）
    assert len(about.APP_TAGLINE) <= 20, about.APP_TAGLINE
    print("TAGLINE:", about.APP_TAGLINE, "len=", len(about.APP_TAGLINE))


def test_welcome_dialog():
    app = QApplication.instance() or QApplication([])
    dlg = WelcomeDialog()
    assert hasattr(dlg, "btn_ok")
    assert dlg.btn_ok.text() == "确认开始"
    # 程序化 accept 应返回 Accepted
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted


def main():
    test_constants()
    test_welcome_dialog()
    print("ALL PASS: test_welcome")


if __name__ == "__main__":
    main()
