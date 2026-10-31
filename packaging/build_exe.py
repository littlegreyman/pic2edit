#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pic2edit 的 PyInstaller 打包入口（单文件夹 / 窗口程序）。

用法（在 ocr_app 的 venv 中运行；推荐直接用 .venv）：
    python packaging/build_exe.py            # 完整：装依赖 + 预热模型 + 打包
    python packaging/build_exe.py --no-deps  # 已装好依赖，仅打包
    python packaging/build_exe.py --no-models# 不打包 RapidOCR 模型（首次使用需联网）

产出：dist/pic2edit/  （含 pic2edit.exe + 全部依赖）
      该目录即 Inno Setup (packaging/pic2edit.iss) 要打包的内容。

设计
----
- 模型（~2GB GGUF）与官方 llama.cpp 引擎（bin/）**不**随包；首次运行由程序
  自动下载到安装目录（跟随用户选择的盘符，见 utils/app_home.data_root）。
- 仅打包：GUI 代码、PySide6、RapidOCR 进程内引擎（可选模型）、huggingface_hub
  等。冻结态 DiscriminativeBackend 进程内调用 RapidOCR；若未打包模型，则首次
  使用时按需下载（或自动降级到 VLM 兜底）。
"""
import os
import sys
import shutil
import subprocess
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OCR_APP = os.path.join(PROJECT_ROOT, "ocr_app")
SPEC = os.path.join(os.path.dirname(__file__), "pic2edit.spec")


def _run(cmd):
    print(">>> " + " ".join(cmd))
    subprocess.check_call(cmd)


def install_deps():
    print("[build] installing build deps (pyinstaller, rapidocr-onnxruntime, onnxruntime) ...")
    _run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    _run([sys.executable, "-m", "pip", "install", "pyinstaller"])
    # rapidocr_onnxruntime 会拉入 onnxruntime；显式装 onnxruntime 也行
    _run([sys.executable, "-m", "pip", "install", "rapidocr-onnxruntime", "onnxruntime"])


def bundle_rapidocr_models():
    """预热下载 RapidOCR 的 onnx 模型到 ocr_app/rapidocr_models（离线可用）。

    返回是否成功打包。联网失败时返回 False（不影响打包，运行时再按需下载）。
    """
    dest = os.path.join(OCR_APP, "rapidocr_models")
    try:
        from rapidocr_onnxruntime import RapidOCR
        print("[build] warming up RapidOCR (downloads onnx models on first use) ...")
        RapidOCR()  # 触发下载
    except Exception as e:
        print("[build] RapidOCR warm-up failed (offline?): %s" % e)
        return False

    # 查找下载缓存并复制到打包目录
    candidates = [
        os.path.join(os.path.expanduser("~"), ".RapidOCR"),
        os.path.join(os.path.expanduser("~"), ".cache", "rapidocr"),
    ]
    found = []
    for base in candidates:
        if os.path.isdir(base):
            for f in os.listdir(base):
                if f.lower().endswith(".onnx"):
                    found.append(os.path.join(base, f))
    if not found:
        print("[build] no RapidOCR onnx models found in cache; skip bundling")
        return False
    os.makedirs(dest, exist_ok=True)
    for src in found:
        shutil.copy2(src, os.path.join(dest, os.path.basename(src)))
    print("[build] bundled RapidOCR models: %s" % ", ".join(os.listdir(dest)))
    return True


def build():
    print("[build] running PyInstaller on %s ..." % SPEC)
    _run([
        sys.executable, "-m", "PyInstaller",
        SPEC, "--noconfirm", "--clean",
        "--distpath", os.path.join(PROJECT_ROOT, "dist"),
        "--workpath", os.path.join(PROJECT_ROOT, "build"),
    ])


def main():
    ap = argparse.ArgumentParser(description="Build pic2edit Windows executable")
    ap.add_argument("--no-deps", action="store_true", help="skip pip install of build deps")
    ap.add_argument("--no-models", action="store_true", help="do not bundle RapidOCR onnx models")
    args = ap.parse_args()

    if not args.no_deps:
        install_deps()
    if not args.no_models:
        bundle_rapidocr_models()
    build()
    out = os.path.join(PROJECT_ROOT, "dist", "pic2edit", "pic2edit.exe")
    print("\n[build] DONE.")
    print("  exe: %s" % out)
    print("  把 dist/pic2edit/ 整个目录交给 packaging/pic2edit.iss 打包成安装程序。")


if __name__ == "__main__":
    main()
