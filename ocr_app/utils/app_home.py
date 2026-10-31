"""可重定位路径：让程序在冻结（PyInstaller 单文件夹）后，运行时数据仍跟随
用户选择的安装位置（包括非系统盘），而不是写死 C 盘用户目录。

设计要点
--------
程序可被安装到任意盘（含非系统盘）。因此运行时数据绝不写死 ``C:\\`` 或
``%USERPROFILE%``，而是：

- 开发态（``sys.frozen`` 未设置）：沿用历史布局
    * 运行时数据（``bin/`` 引擎、``models/`` GGUF）放在 ``ocr_app/``
    * 用户数据（``config.json`` / 日志 / 崩溃日志）放在 ``~/.pic2edit``
- 冻结态（PyInstaller 单文件夹）：
    * 运行时数据放在 **安装根目录**（``dirname(sys.executable)``），
      这样引擎 + 模型跟随用户选择的盘符持久保存。
    * 用户数据同样放在安装根目录（跟随所选盘）。
    * 打包进 exe 的数据文件（logo、RapidOCR 的 onnx 模型等）位于
      ``sys._MEIPASS``（PyInstaller 解压目录），用 ``resource_path`` 定位。

注意：PyInstaller 单文件夹模式下，Python 代码实际运行在 ``sys._MEIPASS``
（临时目录，重启会被清掉），所以 **绝不能** 用 ``dirname(__file__)`` 去找
应该持久化的模型/引擎——那会指向临时目录。必须用 ``data_root()``。
"""
import os
import sys


def _frozen():
    return getattr(sys, "frozen", False)


def data_root():
    """运行时数据根目录：bin/（llama.cpp 引擎）和 models/（GGUF）。

    冻结态 -> 安装目录（exe 同级），使引擎 + 模型跟随用户选择的盘符。
    开发态 -> ocr_app/（与冻结前布局一致）。
    """
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    # app_home.py 位于 ocr_app/utils/，向上两级即 ocr_app/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_home():
    """用户数据目录：config.json / 日志 / 崩溃日志。

    冻结态 -> 安装目录（跟随所选盘）。开发态 -> ~/.pic2edit。
    """
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.join(os.path.expanduser("~"), ".pic2edit")


def writable_app_home():
    """保证可写的用户数据目录；若 app_home() 只读（例如被装进受保护的
    Program Files）则回退到 %LOCALAPPDATA%/pic2edit 或临时目录。
    """
    import tempfile

    candidate = app_home()
    try:
        os.makedirs(candidate, exist_ok=True)
        probe = os.path.join(candidate, ".writable_test")
        with open(probe, "w") as f:
            f.write("1")
        os.remove(probe)
        return candidate
    except Exception:
        fallback = os.path.join(
            os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "pic2edit"
        )
        try:
            os.makedirs(fallback, exist_ok=True)
        except Exception:
            fallback = tempfile.mkdtemp(prefix="pic2edit_")
        return fallback


def resource_path(rel):
    """定位打包进 exe 的数据文件（logo、RapidOCR 的 onnx 模型等）。

    冻结态 -> PyInstaller 解压目录 ``sys._MEIPASS``；若未随包收集则回退到
    exe 同级目录。开发态 -> 项目根目录。
    """
    if _frozen():
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
            os.path.abspath(sys.executable)
        )
        return os.path.join(base, rel)
    # app_home.py 在 ocr_app/utils/，向上三级即项目根
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        rel,
    )
