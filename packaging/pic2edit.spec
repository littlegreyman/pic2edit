# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包规格（单文件夹 / --onedir，--windowed）。

生成 dist/pic2edit/pic2edit.exe：双击即开 GUI，无需 Python 环境。
模型（~2GB GGUF）与官方 llama.cpp 引擎（bin/）**不**随包，由程序首次运行时
自动下载到安装目录（跟随用户选择的盘符）。

资源：
  - pic2edit-logo.png：窗口图标（resource_path 定位）
  - rapidocr_models/ ：RapidOCR 的 onnx 模型（若已预热下载，则随包，离线可用）

关键：运行时持久数据（bin/、models/、config.json、日志）一律用 utils.app_home
的 data_root()/app_home()，在冻结态取 dirname(sys.executable)（安装根），
绝不写死 C 盘或 %USERPROFILE%。
"""
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# PyInstaller 在运行 spec 时会注入全局 SPECPATH（spec 所在目录）。
try:
    SPECPATH_DIR = SPECPATH
except NameError:
    SPECPATH_DIR = os.path.dirname(os.path.abspath(__file__))

PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH_DIR, ".."))
OCR_APP = os.path.join(PROJECT_ROOT, "ocr_app")

# 收集 onnxruntime / rapidocr_onnxruntime 的全部二进制与数据（含 DLL / 模型占位）
ocr_binaries, ocr_datas, ocr_hiddenimports = [], [], []
for pkg in ("onnxruntime", "rapidocr_onnxruntime"):
    try:
        b, d, h = collect_all(pkg)
        ocr_binaries += b
        ocr_datas += d
        ocr_hiddenimports += h
    except Exception as e:  # 未安装则不收集（冻结态会优雅降级到 VLM 兜底）
        print("[warn] collect_all(%s) failed: %s" % (pkg, e))

added_datas = [
    (os.path.join(PROJECT_ROOT, "pic2edit-logo.png"), "."),
]
added_datas += ocr_datas

# RapidOCR 模型：若构建前已预热下载到 ocr_app/rapidocr_models，则随包（离线可用）
rapid_models = os.path.join(OCR_APP, "rapidocr_models")
if os.path.isdir(rapid_models):
    added_datas.append((rapid_models, "rapidocr_models"))
    print("[info] bundling RapidOCR models from", rapid_models)
else:
    print("[info] no rapidocr_models/ found -> RapidOCR 将在首次使用时按需下载（需联网）")

a = Analysis(
    [os.path.join(OCR_APP, "main.py")],
    pathex=[OCR_APP],
    binaries=ocr_binaries,
    datas=added_datas,
    hiddenimports=[
        "huggingface_hub",
        "backends",
        "backends.base",
        "backends.ollama_backend",
        "backends.transformers_backend",
        "backends.llamacpp_backend",
        "backends.discriminative_backend",
        "backends.table_backend",
        "backends.ocr_pipeline",
        "utils",
        "utils.input_prep",
        "utils.export_docx",
        "utils.logging_setup",
        "utils.table_utils",
        "utils.text_utils",
        "utils.app_home",
        "download_models",
        "run_llamacpp_server",
        "rapidocr_onnxruntime",
        "onnxruntime",
        "onnxruntime.capi",
        "markdown",
        "docx",
    ] + ocr_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 这些一般不会用到，剔除可减小体积（不剔除 PySide6 子模块，避免破坏传递导入）
        "tkinter", "unittest", "pydoc", "doctest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,   # 单文件夹模式：二进制放文件夹，不放进 exe
    name="pic2edit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # 关闭 UPX：避免触发杀软启发式误报
    runtime_tmpdir=None,
    console=False,           # --windowed：不弹控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(PROJECT_ROOT, "packaging", "pic2edit-logo.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pic2edit",
)
