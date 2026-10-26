#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pic2edit_convert.py — 把图片/PDF 转成可编辑文件（Markdown/Word/纯文本）。

设计目标：作为 pic2edit-ocr skill 的核心 helper，完全脱离 GUI 运行。
流程：
  1) 把项目 ocr_app 加入 sys.path，复用项目已有的 OCR / 导出能力；
  2) 确保本地 llama.cpp 服务可用（未起则自动拉起，已起则复用）；
  3) prepare_input 拆 PDF / 多页 TIF 为图像项；
  4) 逐项 LlamaCppBackend.ocr_image 拿 Markdown 并拼接；
  5) table 模式过 kv_text_to_markdown_table（键值→表格）；
  6) 按 --format 导出 md / txt / docx，落到输出路径并打印。

用法：
  python pic2edit_convert.py <输入图片/PDF> [--format md|docx|txt]
                              [--mode markdown|ocr|figure|table]
                              [--url http://localhost:8080]
                              [--gpu-layers N] [--out OUT_PATH] [--stop-server]

注意：
  - 引擎默认常驻（转换后不退出），便于后续调用秒开；传 --stop-server 可在本次拉起后退出。
  - 容器无 GPU 时传 --gpu-layers 0 走 CPU。
"""
import argparse
import os
import sys
import time
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_ocr_app():
    """从本脚本向上查找含 run_llamacpp_server.py 的目录（即 ocr_app）。"""
    cur = HERE
    for _ in range(6):
        if os.path.isfile(os.path.join(cur, "run_llamacpp_server.py")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    # 兜底：相对推算（scripts -> pic2edit-ocr -> skills -> .workbuddy -> 项目根 -> ocr_app）
    return os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "ocr_app"))


OCR_APP = _find_ocr_app()
if OCR_APP not in sys.path:
    sys.path.insert(0, OCR_APP)

from utils.input_prep import prepare_input                      # noqa: E402
from utils.export_docx import save_text, md_to_docx              # noqa: E402
from utils.table_utils import kv_text_to_markdown_table         # noqa: E402
from backends.llamacpp_backend import LlamaCppBackend           # noqa: E402
import run_llamacpp_server as launcher                           # noqa: E402

PORT = "8080"
READY_TIMEOUT = 240.0

# 与 ocr_app/config.py::PROMPT_TEMPLATES 对齐（不在此重复 <image> marker）
MODE_TO_PROMPT = {
    "markdown": "<|grounding|>Convert the document to markdown.",
    "ocr": "Free OCR.",
    "figure": "Parse the figure.",
    "table": "<|grounding|>Convert the document to markdown.",
}


def ensure_server(url, gpu_layers):
    """若服务未就绪则拉起 llama.cpp 服务；返回 (backend, proc_or_None)。"""
    base = url.rstrip("/")
    backend = LlamaCppBackend(base, "deepseek-ocr")
    if backend.available():
        return backend, None
    exe, model, mmproj = launcher.discover()
    if not exe:
        sys.stderr.write("[error] 未找到 llama-server，请先运行 download_models 部署引擎。\n")
        sys.exit(1)
    if not model:
        sys.stderr.write("[error] 未找到 GGUF 模型，请先下载模型权重。\n")
        sys.exit(1)
    cmd = launcher.build_command(exe, model, mmproj)
    if gpu_layers is not None:
        cmd += ["--n-gpu-layers", str(gpu_layers)]
    print(f"[launch] {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.Popen(cmd, cwd=OCR_APP,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.stderr.write(f"[error] 引擎进程异常退出，code={proc.poll()}\n")
            sys.exit(1)
        if backend.available():
            print("[ok] 引擎就绪", file=sys.stderr)
            return backend, proc
        time.sleep(2)
    sys.stderr.write("[error] 引擎在超时内未就绪（检查 CUDA / 模型路径）\n")
    sys.exit(1)


def _safe_path(p):
    """把传入路径规范化为绝对路径；同时兼容 WorkBuddy 附件的相对/带协议写法。"""
    p = p.strip()
    if p.startswith("file://"):
        p = p[len("file://"):]
    return os.path.abspath(p)


def convert(input_path, fmt, mode, url, gpu_layers, out_path, stop_server):
    input_path = _safe_path(input_path)
    if not os.path.isfile(input_path):
        sys.stderr.write(f"[error] 输入文件不存在: {input_path}\n")
        sys.exit(2)

    backend, proc = ensure_server(url, gpu_layers)
    work = tempfile.mkdtemp(prefix="pic2edit_")
    try:
        items = prepare_input(input_path, work)
        prompt = MODE_TO_PROMPT.get(mode, MODE_TO_PROMPT["markdown"])
        parts = []
        for it in items:
            txt = backend.ocr_image(it.image_path, prompt)
            parts.append(txt)
        text = "\n\n".join(p for p in parts if p and p.strip())
        if mode == "table":
            text = kv_text_to_markdown_table(text)
    finally:
        if proc is not None and stop_server:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    if not out_path:
        stem = os.path.splitext(input_path)[0]
        out_path = f"{stem}.{fmt}"
    out_path = os.path.abspath(out_path)
    if fmt == "docx":
        md_to_docx(text, out_path)
    else:
        save_text(text, out_path)
    # 唯一 stdout 输出：最终文件路径，供 skill 解析后 present_files
    print(out_path)


def main():
    ap = argparse.ArgumentParser(description="pic2edit: 图片/PDF -> 可编辑文件")
    ap.add_argument("input", help="输入图片或 PDF 路径")
    ap.add_argument("--format", choices=["md", "txt", "docx"], default="md")
    ap.add_argument("--mode",
                    choices=["markdown", "ocr", "figure", "table"], default="markdown")
    ap.add_argument("--url", default=f"http://localhost:{PORT}")
    ap.add_argument("--gpu-layers", type=int, default=None,
                    help="显存层数；容器无 GPU 时传 0 走 CPU。默认不传（自动用 GPU）")
    ap.add_argument("--out", default="", help="输出文件路径（默认与输入同名替换扩展名）")
    ap.add_argument("--stop-server", action="store_true",
                    help="若本次拉起了引擎，转换完成后退出引擎进程")
    args = ap.parse_args()
    convert(args.input, args.format, args.mode, args.url,
            args.gpu_layers, args.out, args.stop_server)


if __name__ == "__main__":
    main()
