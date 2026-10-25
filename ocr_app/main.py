"""程序入口。"""
import sys

from PyQt6.QtWidgets import QApplication

from gui import MainWindow
from utils.logging_setup import setup_logging


def main():
    setup_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
