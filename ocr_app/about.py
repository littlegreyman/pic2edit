"""关于本程序：项目名 / 版本 / 开发者 / 发布时间 / 一句话简介。

集中在这一处，便于 GUI 欢迎页、关于框、打包元数据统一引用。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDialogButtonBox,
)
from PySide6.QtGui import QFont, QIcon

APP_NAME = "pic2edit"
APP_VERSION = "alpha 0.18"
APP_AUTHOR = "littlegreyman"
# 最新版本（Git tag）对应的发布日期
APP_RELEASE_DATE = "2026-10-31"
# 主要功能简介：控制在 20 个中文字符以内
APP_TAGLINE = "本地离线OCR·图片转可编辑文档"


class WelcomeDialog(QDialog):
    """启动欢迎页：展示项目名 / 版本 / 开发者 / 发布时间 / 简介 + 确认键。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用 pic2edit")
        self.setModal(True)
        self.setFixedSize(420, 280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)

        title = QLabel(APP_NAME)
        title.setFont(QFont("Microsoft YaHei", 26, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        tagline = QLabel(APP_TAGLINE)
        tagline.setFont(QFont("Microsoft YaHei", 13))
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setStyleSheet("color:#1565c0;")
        layout.addWidget(tagline)

        layout.addSpacing(16)

        info = QLabel(
            f"版本：{APP_VERSION}\n"
            f"开发者：{APP_AUTHOR}\n"
            f"最新版本时间：{APP_RELEASE_DATE}"
        )
        info.setFont(QFont("Microsoft YaHei", 11))
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        layout.addSpacing(10)

        hint = QLabel("本工具完全本地离线运行，图片与文档不会上传任何服务器。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666; font-size:10px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        layout.addStretch(1)

        btn_box = QDialogButtonBox()
        self.btn_ok = QPushButton("确认开始")
        self.btn_ok.setDefault(True)
        btn_quit = QPushButton("退出")
        btn_box.addButton(self.btn_ok, QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.addButton(btn_quit, QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)
