#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pic2edit 一键启动 GUI（跨平台：Windows / Linux / macOS）。

用法：
  # 直接启动 GUI（最常用，双击或命令行均可）
  python launch_gui.py                # Windows（.py 已关联时双击即可）
  python3 launch_gui.py               # Linux / macOS

  # 仅检查环境，不启动 GUI（验证 ocr_app / venv 定位是否正确）
  python launch_gui.py --dry-run

设计：本脚本用“系统 python”运行，仅负责定位项目与虚拟环境，然后以
子进程方式调用 ocr_app/.venv 里的 python 启动真正的 GUI（main.py）。
GUI 启动后会按需自动拉起本地 OCR 引擎（llama.cpp / RapidOCR），无需手动起服务。

定位优先级：
  1) 环境变量 PIC2EDIT_OCR_APP / PIC2EDIT_ROOT（最可靠）
  2) 从本脚本位置向上查找 ocr_app
  3) 常见用户目录候选（~/Documents/pic2edit、~/文档/pic2edit、~/pic2edit、~/projects/pic2edit）
"""
import os
import sys
import subprocess


HERE = os.path.dirname(os.path.abspath(__file__))


def find_ocr_app():
    """定位包含 main.py 的 ocr_app 目录（跨平台）。"""
    env = os.environ.get("PIC2EDIT_OCR_APP")
    if env and os.path.isfile(os.path.join(env, "main.py")):
        return env
    root = os.environ.get("PIC2EDIT_ROOT")
    if root and os.path.isfile(os.path.join(root, "ocr_app", "main.py")):
        return os.path.join(root, "ocr_app")

    # 2) 从本脚本位置向上查找
    cur = HERE
    for _ in range(4):
        cand = os.path.join(cur, "ocr_app")
        if os.path.isfile(os.path.join(cand, "main.py")):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    # 3) 常见用户目录候选
    home = os.path.expanduser("~")
    bases = [
        os.path.join(home, "Documents", "pic2edit"),
        os.path.join(home, "文档", "pic2edit"),
        os.path.join(home, "pic2edit"),
        os.path.join(home, "projects", "pic2edit"),
    ]
    for base in bases:
        cand = os.path.join(base, "ocr_app")
        if os.path.isfile(os.path.join(cand, "main.py")):
            return cand
    return None


def venv_python(ocr_app):
    """按操作系统返回 venv 解释器路径。"""
    if os.name == "nt" or sys.platform.startswith("win"):
        cand = os.path.join(ocr_app, ".venv", "Scripts", "python.exe")
    else:
        cand = os.path.join(ocr_app, ".venv", "bin", "python3")
    return cand if os.path.isfile(cand) else None


def main(argv):
    dry_run = "--dry-run" in argv

    ocr_app = find_ocr_app()
    if not ocr_app:
        sys.stderr.write(
            "[error] 找不到 ocr_app 目录（需含 main.py）。\n"
            "        请确认项目位置，或设置环境变量 PIC2EDIT_OCR_APP 指向 ocr_app。\n"
        )
        return 1

    py = venv_python(ocr_app)
    if not py:
        sys.stderr.write(
            "[error] 找不到虚拟环境解释器：%s\n"
            "        请先按 README 安装依赖（setup_venv.bat / setup_venv.sh）。\n"
            % os.path.join(ocr_app, ".venv")
        )
        return 1

    main_py = os.path.join(ocr_app, "main.py")
    sys.stderr.write("[info] ocr_app = %s\n" % ocr_app)
    sys.stderr.write("[info] python  = %s\n" % py)

    if dry_run:
        sys.stderr.write("[info] --dry-run：环境检查通过，未启动 GUI。\n")
        return 0

    sys.stderr.write("[launch] 正在启动 pic2edit GUI ...\n")
    args = [py, main_py] + [a for a in argv if a != "--dry-run"]
    # cwd 设为 ocr_app，使 main.py 内的 `import gui` / `from utils...` 可解析
    return subprocess.call(args, cwd=ocr_app)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
