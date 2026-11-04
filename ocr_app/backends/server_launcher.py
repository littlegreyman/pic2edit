"""按需自动拉起本地 OCR 引擎（llama.cpp 默认；Transformers 亦可）。

GUI 在「后端不可用」时调用本模块，避免用户每次手动起服务。
llama.cpp 复用 run_llamacpp_server 的 discover()/build_command()（参数已踩坑修正：
不传 --chat-template deepseek-ocr、repeat_penalty=1.10、repeat_last_n=4096）。
启动后轮询 /health 与 /v1/models 直到就绪（或超时 / 进程退出），通过信号通知 GUI。
"""
import os
import sys
import time
import threading
import subprocess
import shutil
import logging

import urllib.request

from PySide6.QtCore import QThread, Signal

from utils.app_home import data_root
from utils.logging_setup import get_logger
from engine_launcher import EngineLauncher

# 应用根目录：冻结态 = 安装根（引擎/模型/脚本都随之），开发态 = ocr_app/。
OCR_APP_DIR = data_root()
_logger = get_logger("server_starter")

# ---- 平台相关提示：避免在 Linux/macOS 上显示 Windows 专属的 .exe / SmartScreen 措辞 ----
_IS_WINDOWS = os.name == "nt"


def _exe_display_name():
    return "bin\\llama-server.exe" if _IS_WINDOWS else "bin/llama-server"


def _server_not_found_msg():
    if _IS_WINDOWS:
        return ("未找到 bin/llama-server.exe。请先运行 download_models.bat "
                "下载官方预编译服务（bin/）。")
    return ("未找到 bin/llama-server（或 bin/ 下递归找到的 llama-server 二进制）。"
            "请先运行 download_models.sh（或 download_models.bat）下载官方预编译服务。")


def _launch_fail_hint():
    if _IS_WINDOWS:
        return ("\n若是 Windows Smart App Control / SmartScreen 拦截了未签名 exe，"
                "请右键 %s → 属性 → 解除阻止（Unblock），再重试。"
                % _exe_display_name())
    return ("\n请查看上方“运行状态”日志及 ~/.pic2edit/pic2edit.log 中的 "
            "CUDA / 显存 / 模型路径相关报错。")


def _probe_exit_hint():
    if _IS_WINDOWS:
        return ("常见原因：① Windows Smart App Control 拦截未签名 exe"
                "（右键 %s → 属性 → 解除阻止）；② 启动参数 / 模型路径错误。"
                % _exe_display_name())
    return ("常见原因：① 显存不足（CUDA 分配失败，可能被 Ollama 等程序占用显存，"
            "可用 `nvidia-smi` 查看空闲显存）；② 启动参数 / 模型路径错误；"
            "③ CUDA / 驱动不兼容。")


def free_vram_mib():
    """返回当前空闲显存（MiB）；无 GPU / nvidia-smi 不可用 / 调用失败均返回 None。

    best-effort：多 GPU 取空闲最大者；仅用于拉起前的友好预检，不阻塞无 nvidia-smi 的环境。
    """
    try:
        exe = shutil.which("nvidia-smi") or "/usr/bin/nvidia-smi"
        if not os.path.exists(exe):
            return None
        out = subprocess.run(
            [exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        vals = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
        return max(vals) if vals else None
    except Exception:
        return None


def estimate_vram_mib(model, mmproj):
    """估算加载指定模型 + mmproj 所需显存（MiB，含约 15% KV/上下文余量）。"""
    need = 0
    for p in (model, mmproj):
        if p and os.path.isfile(p):
            need += os.path.getsize(p)
    if need == 0:
        return 0
    return int(need * 1.15 / (1024 * 1024))


class ServerStarter(QThread):
    log = Signal(str)
    server_started = Signal()
    failed = Signal(str)

    def __init__(self, backend_name, url, cfg):
        super().__init__()
        self.backend_name = backend_name
        self.url = url.rstrip("/")
        self.cfg = cfg
        self._proc = None
        self._launcher = None
        self._done = threading.Event()  # 跨线程读写语义正确、原子

    def stop(self):
        """终止正在启动的引擎（用户取消时调用）。"""
        try:
            if self._launcher is not None:
                self._launcher.stop(self._proc)
            elif self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass

    def run(self):
        try:
            if self.backend_name == "llama_cpp":
                self._start_llamacpp()
            elif self.backend_name == "transformers":
                self._start_transformers()
            else:
                self._fail(
                    "Ollama 暂不支持自动启动，请手动启动 Ollama 并执行 `ollama pull deepseek-ocr`。"
                )
        except Exception:
            import traceback as _tb
            self._fail("自动启动引擎异常：\n" + _tb.format_exc())

    def _fail(self, msg):
        if self._done.is_set():
            return
        self._done.set()
        _logger.error("server starter failed: %s", msg)
        self.failed.emit(msg)

    def _start_llamacpp(self):
        try:
            if OCR_APP_DIR not in sys.path:
                sys.path.insert(0, OCR_APP_DIR)
            import run_llamacpp_server as rls
        except Exception as e:
            self._fail("无法加载 run_llamacpp_server：%r" % e)
            return
        exe, model, mmproj = rls.discover()
        if not exe:
            self._fail(_server_not_found_msg())
            return
        if not model:
            self._fail("未找到 models/*.gguf 模型。请先运行 download_models.sh（或 download_models.bat）下载模型。")
            return
        # 拉起前显存预检（best-effort：仅 nvidia-smi 可用时生效，避免无谓的 CUDA 崩溃）
        need = estimate_vram_mib(model, mmproj)
        free = free_vram_mib()
        if need and free is not None and free < need:
            self._fail(
                "本地 OCR 引擎（DeepSeek-OCR）需要约 %.1f GB 显存，但当前空闲仅 %.1f GB。\n"
                "很可能是因为其他程序（如 Ollama、另一个 llama-server）正占用这张显卡。\n"
                "请先释放显存（例如 `ollama stop <模型名>`，或退出占用显存的程序）后再重试。"
                % (need / 1024.0, free / 1024.0)
            )
            return
        cmd = rls.build_command(exe, model, mmproj,
                                getattr(self.cfg, "model_params", None))
        self.log.emit("[launch] " + " ".join(cmd))
        _logger.info("launching engine: %s", " ".join(cmd))
        self.log.emit("[wait] 正在把模型加载进显存（首次约 1–3 分钟），请稍候…")
        _logger.info("loading model into VRAM, waiting up to %.0fs", rls.READY_TIMEOUT)
        try:
            self._launcher = EngineLauncher()
            self._proc = self._launcher.start(cmd, cwd=OCR_APP_DIR)
        except Exception as e:
            self._fail(
                "启动 llama-server 失败：%r%s" % (e, _launch_fail_hint())
            )
            return
        threading.Thread(target=self._stream, daemon=True).start()
        self._await_ready(rls.READY_TIMEOUT)

    def _start_transformers(self):
        script = os.path.join(OCR_APP_DIR, "run_transformers_server.py")
        if not os.path.isfile(script):
            self._fail("未找到 run_transformers_server.py。")
            return
        if os.name == "nt":
            venv_tf = os.path.join(OCR_APP_DIR, ".venv_tf", "Scripts", "python.exe")
        else:
            venv_tf = os.path.join(OCR_APP_DIR, ".venv_tf", "bin", "python3")
        if not os.path.isfile(venv_tf):
            self._fail("未找到 .venv_tf 解释器，请先运行 download_models_tf.bat 建好环境。")
            return
        cmd = [venv_tf, script]
        self.log.emit("[launch] " + " ".join(cmd))
        _logger.info("launching engine: %s", " ".join(cmd))
        self.log.emit("[wait] 正在加载 transformers 模型（首次约 1–3 分钟），请稍候…")
        try:
            self._launcher = EngineLauncher()
            self._proc = self._launcher.start(cmd, cwd=OCR_APP_DIR)
        except Exception as e:
            self._fail("启动 transformers 服务失败：%r%s" % (e, _launch_fail_hint()))
            return
        threading.Thread(target=self._stream, daemon=True).start()
        self._await_ready(600)

    def _await_ready(self, timeout):
        ok = self._probe(timeout)
        if ok:
            if self._done.is_set():
                return
            self._done.set()
            self.server_started.emit()
            return
        if self._done.is_set():  # _probe 已因进程退出而 _fail
            return
        code = self._proc.poll()
        if code is None:
            self._fail(
                "本地 OCR 服务在 %d 秒内未在 %s 就绪。请查看上方“运行状态”日志"
                "（CUDA / mmproj / 模型路径 / 端口占用）。" % (int(timeout), self.url)
            )

    def _stream(self):
        try:
            for line in self._proc.stdout:
                text = line.rstrip("\n")
                self.log.emit(text)
                _logger.info("[engine] %s", text)  # 同时落盘，便于事后查 CUDA / 模型报错
        except Exception:
            pass

    def _probe(self, timeout):
        # 候选地址：优先用配置地址（如 http://localhost:8080），并补充 127.0.0.1，
        # 避免 `localhost` 在 Windows 上解析到 IPv6(::1) 而服务只绑了 127.0.0.1 导致误判未就绪。
        bases = [self.url]
        if "localhost" in self.url:
            bases.append(self.url.replace("localhost", "127.0.0.1"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._done.is_set():
                return False
            if self._proc is not None and self._proc.poll() is not None:
                code = self._proc.poll()
                _logger.error("engine process exited immediately with code %s", code)
                self._fail(
                    "OCR 服务进程启动后立即退出（退出码 %s）。\n"
                    "%s\n"
                    "详见上方“运行状态”日志及 ~/.pic2edit/pic2edit.log。" % (code, _probe_exit_hint())
                )
                return False
            for base in bases:
                for path in ("/health", "/v1/models"):
                    try:
                        with urllib.request.urlopen(base + path, timeout=2) as r:
                            if r.status == 200:
                                return True
                    except Exception:
                        pass
            time.sleep(2)
        return False
