# -*- coding: utf-8 -*-
"""非 Qt 的引擎拉起与失败诊断（GUI 与 Skill 共用的唯一实现）。

为什么单独成模块
----------------
引擎拉起这件事此前有两份实现：

- GUI 侧 ``ServerStarter``：显存预检 + 平台化失败提示 + 引擎输出逐行落盘；
- Skill 侧 ``ensure_server``：``stdout=DEVNULL, stderr=STDOUT``，**引擎输出直接丢弃**，
  失败只报一个 ``code=-6``。

同一台机器、同一次 CUDA 显存不足，GUI 会告诉你"需约 2.6GB，空闲 1.0GB，可能被
Ollama 占用"，而 skill 只给一个退出码。诊断质量差一个数量级——这正是 Phase 2.2
"生命周期统一了、诊断没统一"的遗留。

本模块把「预检 → 拉起 → 输出落盘 → 失败提示」抽出来，**不 import Qt**，
让两个前端共用同一套判断与同一套文案。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from engine_launcher import EngineLauncher
from utils.app_home import data_root
from utils.logging_setup import get_logger

import run_llamacpp_server as _engine_cli

#: 应用根目录：冻结态 = 安装根（引擎/模型都随之），开发态 = ocr_app/。
#: 引擎必须以它为 cwd 启动，否则 bin/ 与 models/ 的相对路径在冻结态会指错地方。
APP_DIR = data_root()

#: 与 run_llamacpp_server.READY_TIMEOUT 对齐（模型首次加载约 1–3 分钟）。
READY_TIMEOUT = 240.0

_IS_WINDOWS = os.name == "nt"
_log = get_logger("engine_boot")


class EngineBootError(RuntimeError):
    """引擎未能在预期时间内就绪。消息已是给用户看的完整中文说明。"""


def exe_display_name() -> str:
    return "bin\\llama-server.exe" if _IS_WINDOWS else "bin/llama-server"


def server_not_found_msg() -> str:
    if _IS_WINDOWS:
        return ("未找到 bin/llama-server.exe。请先运行 download_models.bat "
                "下载官方预编译服务（bin/）。")
    return ("未找到 bin/llama-server（或 bin/ 下递归找到的 llama-server 二进制）。"
            "请先运行 download_models.sh（或 download_models.bat）下载官方预编译服务。")


def launch_fail_hint() -> str:
    if _IS_WINDOWS:
        return ("\n若是 Windows Smart App Control / SmartScreen 拦截了未签名 exe，"
                "请右键 %s → 属性 → 解除阻止（Unblock），再重试。" % exe_display_name())
    return ("\n请查看 ~/.pic2edit/pic2edit.log 中的 CUDA / 显存 / 模型路径相关报错。")


def probe_exit_hint() -> str:
    if _IS_WINDOWS:
        return ("常见原因：① Windows Smart App Control 拦截未签名 exe"
                "（右键 %s → 属性 → 解除阻止）；② 启动参数 / 模型路径错误。"
                % exe_display_name())
    return ("常见原因：① 显存不足（CUDA 分配失败，可能被 Ollama 等程序占用显存，"
            "可用 `nvidia-smi` 查看空闲显存）；② 启动参数 / 模型路径错误；"
            "③ CUDA / 驱动不兼容。")


def free_vram_mib():
    """当前空闲显存（MiB）；无 GPU / 无 nvidia-smi / 调用失败均返回 None。

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


def estimate_vram_mib(model, mmproj) -> int:
    """估算加载指定模型 + mmproj 所需显存（MiB，含约 15% KV/上下文余量）。"""
    need = 0
    for p in (model, mmproj):
        if p and os.path.isfile(p):
            need += os.path.getsize(p)
    if need == 0:
        return 0
    return int(need * 1.15 / (1024 * 1024))


def probe(base_url: str, timeout: float = 2.0) -> bool:
    """单次探活：``/health`` 或 ``/v1/models`` 任一返回 200 即视为就绪。"""
    for path in ("/health", "/v1/models"):
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=timeout) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
    return False


def _candidate_bases(url: str):
    bases = [url.rstrip("/")]
    if "localhost" in url:
        # Windows 上 localhost 可能解析到 ::1，而服务只绑了 127.0.0.1
        bases.append(url.replace("localhost", "127.0.0.1").rstrip("/"))
    return bases


def port_of(url: str) -> str:
    """从 ``http://host:port`` 里取端口，取不到就用模块默认 PORT。

    引擎必须听在调用方指定的端口上：否则 ``ensure_engine("http://127.0.0.1:8081")``
    会在默认 8080 起服务、再去 8081 等就绪 —— 必然超时，还留下一个孤儿进程。
    """
    try:
        p = urlsplit(url).port
    except ValueError:      # 端口是非法字符串
        p = None
    return str(p) if p else str(_engine_cli.PORT)


class EngineHandle:
    """一次引擎拉起的结果。``proc`` 为 None 表示复用了已在运行的服务。"""

    def __init__(self, url, proc=None, launcher=None):
        self.url = url
        self.proc = proc
        self._launcher = launcher

    @property
    def started(self) -> bool:
        return self.proc is not None

    def stop(self, wait: float = 5.0) -> None:
        """回收本次拉起的进程组（不改动"别人已在跑"的服务）。"""
        if self.proc is None or self._launcher is None:
            return
        try:
            self._launcher.stop(self.proc)
            try:
                self.proc.wait(timeout=wait)
            except Exception:
                self._launcher.kill(self.proc)
        except Exception:
            pass


def ensure_engine(url: str, *, model_files=None, gpu_layers=None, model_params=None,
                  timeout: float = READY_TIMEOUT, on_log=None) -> EngineHandle:
    """确保本地 llama.cpp 服务可用；必要时拉起。

    Args:
        url: 服务地址（如 ``http://localhost:8080``）。
        model_files: ``(model_path, mmproj_path)``，按显存选定的权重；为空项时沿用
            ``discover()`` 的默认。
        gpu_layers: 强制 GPU 层数；``None`` 表示交给引擎自行决定。
        model_params: 采样参数（决定 ``--repeat-penalty`` / ``--repeat-last-n``）。
        timeout: 等待就绪的秒数。
        on_log: 引擎输出回调（逐行）。默认写 stderr 并落盘日志。

    Returns:
        :class:`EngineHandle`。``handle.proc`` 为 None 时表示沿用已在运行的服务，
        调用方不应关停它（那是别人的进程）。

    Raises:
        EngineBootError: 未找到引擎/模型、显存不足、进程异常退出或超时。
            消息可直接展示给用户。
    """
    base = url.rstrip("/")
    if probe(base):
        _log.info("检测到已有引擎在运行，沿用其已加载的模型（本次不切换）：%s", base)
        return EngineHandle(base)

    exe, model, mmproj = _engine_cli.discover()
    if model_files:
        _m, _mp = model_files
        if _m:
            model = _m
        if _mp:
            mmproj = _mp
    if not exe:
        raise EngineBootError(server_not_found_msg())
    if not model:
        raise EngineBootError("未找到 models/*.gguf 模型。请先运行 download_models.sh"
                              "（或 download_models.bat）下载模型。")

    need = estimate_vram_mib(model, mmproj)
    free = free_vram_mib()
    if need and free is not None and free < need:
        raise EngineBootError(
            "本地 OCR 引擎（DeepSeek-OCR）需要约 %.1f GB 显存，但当前空闲仅 %.1f GB。\n"
            "很可能是因为其他程序（如 Ollama、另一个 llama-server）正占用这张显卡。\n"
            "请先释放显存（例如 `ollama stop <模型名>`，或退出占用显存的程序）后再重试。"
            % (need / 1024.0, free / 1024.0))

    cmd = _engine_cli.build_command(exe, model, mmproj, model_params,
                                    port=port_of(url))
    if gpu_layers is not None:
        cmd += ["--n-gpu-layers", str(gpu_layers)]

    if on_log is None:
        def on_log(line):                     # noqa: E306 - 就地定义的默认回调
            sys.stderr.write(f"[engine] {line}\n")

    launcher = EngineLauncher()
    try:
        proc = launcher.start(cmd, cwd=APP_DIR,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as e:
        raise EngineBootError("启动 llama-server 失败：%r%s" % (e, launch_fail_hint()))

    def _pump():
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip("\n") if isinstance(raw, bytes) \
                    else str(raw).rstrip("\n")
                on_log(line)
                _log.info("[engine] %s", line)   # 落盘：事后能查 CUDA / 模型报错
        except Exception:
            pass

    threading.Thread(target=_pump, daemon=True).start()

    deadline = time.time() + timeout
    bases = _candidate_bases(url)
    while time.time() < deadline:
        code = proc.poll()
        if code is not None:
            launcher.stop(proc)
            raise EngineBootError(
                "OCR 服务进程启动后立即退出（退出码 %s）。\n%s\n"
                "详见 ~/.pic2edit/pic2edit.log。" % (code, probe_exit_hint()))
        if any(probe(b) for b in bases):
            _log.info("引擎就绪：%s", base)
            return EngineHandle(base, proc, launcher)
        time.sleep(2)

    launcher.stop(proc)
    raise EngineBootError(
        "本地 OCR 服务在 %d 秒内未在 %s 就绪（CUDA / mmproj / 模型路径 / 端口占用）。"
        % (int(timeout), base))
