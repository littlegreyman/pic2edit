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
    """定位 pic2edit 的 ocr_app 目录（含 run_llamacpp_server.py）。

    解析优先级（跨 Windows / Linux / macOS）：
      1) 环境变量 PIC2EDIT_OCR_APP / PIC2EDIT_ROOT（显式指路，最可靠）
      2) 从本脚本位置向上查找（技能随项目放置时命中）
      3) 常见用户目录候选（~ 展开后尝试多种相对布局，含中英文“文档”）
      4) 兼容历史布局的相对推算
    """
    env = os.environ.get("PIC2EDIT_OCR_APP")
    if not env:
        root = os.environ.get("PIC2EDIT_ROOT")
        if root:
            env = os.path.join(root, "ocr_app")
    if env and os.path.isfile(os.path.join(env, "run_llamacpp_server.py")):
        return env

    # 2) 向上查找（技能随项目放在 <repo>/.workbuddy/skills/... 时命中）
    cur = HERE
    for _ in range(6):
        if os.path.isfile(os.path.join(cur, "run_llamacpp_server.py")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    # 3) 常见用户目录候选（跨平台，含本地化“文档”目录）
    home = os.path.expanduser("~")
    xdg_docs = (os.environ.get("XDG_DOCUMENTS_DIR") or "").rstrip("/")
    doc_dirs = [os.path.join(home, "Documents"),
                os.path.join(home, "文档")]
    if xdg_docs:
        doc_dirs.append(xdg_docs)
    rel_roots = [
        "pic2edit/ocr_app",
        "pic2edit/pic2edit/ocr_app",   # 嵌套 clone 形态
        "projects/pic2edit/ocr_app",
        "ocr_app",
    ]
    for doc in doc_dirs:
        for rel in rel_roots:
            cand = os.path.join(doc, rel)
            if os.path.isfile(os.path.join(cand, "run_llamacpp_server.py")):
                return cand
    # 也允许项目直接放在 home 下
    for rel in rel_roots:
        cand = os.path.join(home, rel)
        if os.path.isfile(os.path.join(cand, "run_llamacpp_server.py")):
            return cand

    # 4) 兼容历史布局的相对推算（技能装为用户级且无 env/已知路径时）
    return os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "ocr_app"))


OCR_APP = _find_ocr_app()
if OCR_APP not in sys.path:
    sys.path.insert(0, OCR_APP)

from utils.input_prep import prepare_input                      # noqa: E402
from utils.export_docx import save_text, md_to_docx              # noqa: E402
from utils.table_utils import kv_text_to_markdown_table         # noqa: E402
from backends.llamacpp_backend import LlamaCppBackend           # noqa: E402
from backends.discriminative_backend import DiscriminativeBackend  # noqa: E402
from backends.ocr_pipeline import run as pipeline_run               # noqa: E402
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

    # 自动规避沙箱内存限制：若本进程虚拟内存软限制受限（如 WorkBuddy 沙箱 4GB），
    # RapidOCR/onnxruntime 加载模型会被 OOM killer 连父进程一起 SIGKILL(137)，表现为
    # 无任何输出的“死机”。此时跳过判别式，直接走 VLM 兜底（依赖本机 :8080 的 llama-server）。
    # 也可用环境变量 PIC2EDIT_SKIP_DISCRIMINATIVE=1 强制跳过。
    def _vmem_limited_gb():
        # `resource` 是 Unix 专属模块；Windows 上不可用，按「无虚拟内存限制」处理
        # （即不自动跳过判别式，由后续逻辑按可用性决定）。
        try:
            import resource as _rs
            soft, _ = _rs.getrlimit(_rs.RLIMIT_AS)
        except Exception:
            return 0
        if soft == _rs.RLIM_INFINITY:
            return 0
        return soft / (1024 ** 3)

    _force = os.environ.get("PIC2EDIT_SKIP_DISCRIMINATIVE", "").lower() in ("1", "true", "yes")
    skip_disc = _force or (0 < _vmem_limited_gb() <= 6)
    if skip_disc:
        sys.stderr.write("[info] 内存受限环境：跳过判别式(RapidOCR)，改用 VLM 兜底(需本机 :8080 的 llama-server)\n")

    # 判别式后端可用则无需 GPU 服务（纯 CPU、零幻觉、可无头运行）；
    # 仅当判别式不可用且未跳过时，才拉起 VLM 服务兜底。
    disc_ok = False
    if not skip_disc:
        try:
            DiscriminativeBackend()
            disc_ok = True
        except Exception:
            disc_ok = False

    proc = None
    if not skip_disc and not disc_ok:
        backend, proc = ensure_server(url, gpu_layers)

    work = tempfile.mkdtemp(prefix="pic2edit_")
    try:
        items = prepare_input(input_path, work)
        # 统一流水线：判别式优先 + VLM 兜底 + 版面感知 + 表格结构化
        pipeline_run(items, server_url=url, model="deepseek-ocr",
                     use_vlm_fallback=True, mode=mode,
                     skip_discriminative=skip_disc)
        parts = [it.result for it in items if it.result and it.result.strip()]
        text = "\n\n".join(parts)
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
