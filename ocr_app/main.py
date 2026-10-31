"""程序入口。"""
import os
import sys
import faulthandler
import traceback

from PySide6.QtWidgets import QApplication

from gui import MainWindow
from utils.logging_setup import setup_logging


def _install_crash_log():
    """把未捕获异常与 C 层崩溃（段错误 / 访问违规）写入磁盘，便于事后排查。

    之前 GUI 崩溃时只闪退、没有任何落盘记录（用户反馈“报错来不及看”）。这里用
    faulthandler 捕获 C 级崩溃（segfault / access violation），用 sys.excepthook
    捕获 Python 未处理异常，都追加写到 ~/.pic2edit/pic2edit_crash.log。
    """
    try:
        log_dir = os.path.join(os.path.expanduser("~"), ".pic2edit")
        os.makedirs(log_dir, exist_ok=True)
        crash_log = os.path.join(log_dir, "pic2edit_crash.log")
        fh = open(crash_log, "a", encoding="utf-8")
        faulthandler.enable(file=fh, all_threads=True)
    except Exception:
        return

    def _hook(etype, evalue, etb):
        try:
            fh.write("\n=== uncaught exception ===\n")
            fh.write("".join(traceback.format_exception(etype, evalue, etb)))
            fh.flush()
        except Exception:
            pass
        sys.__excepthook__(etype, evalue, etb)

    sys.excepthook = _hook


def main():
    _install_crash_log()
    setup_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
