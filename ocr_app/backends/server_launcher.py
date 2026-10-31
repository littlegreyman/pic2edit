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
import urllib.request

from PySide6.QtCore import QThread, Signal

from utils.app_home import data_root

# 应用根目录：冻结态 = 安装根（引擎/模型/脚本都随之），开发态 = ocr_app/。
OCR_APP_DIR = data_root()


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
        self._done = False

    def stop(self):
        """终止正在启动的引擎（用户取消时调用）。"""
        try:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
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
        if self._done:
            return
        self._done = True
        self.failed.emit(msg)

    def _popen_kwargs(self):
        kwargs = dict(
            cwd=OCR_APP_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if os.name == "nt":  # 不弹额外控制台窗口，且避免被 cmd 包裹
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return kwargs

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
            self._fail(
                "未找到 bin/llama-server.exe。请先运行 download_models.bat 下载官方预编译服务（bin/）。"
            )
            return
        if not model:
            self._fail("未找到 models/*.gguf 模型。请先运行 download_models.bat 下载模型。")
            return
        cmd = rls.build_command(exe, model, mmproj)
        self.log.emit("[launch] " + " ".join(cmd))
        self.log.emit("[wait] 正在把模型加载进显存（首次约 1–3 分钟），请稍候…")
        try:
            self._proc = subprocess.Popen(cmd, **self._popen_kwargs())
        except Exception as e:
            self._fail(
                "启动 llama-server 失败：%r\n"
                "若是 Windows Smart App Control / SmartScreen 拦截了未签名 exe，"
                "请右键 bin\\llama-server.exe → 属性 → 解除阻止（Unblock），再重试。" % e
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
        self.log.emit("[wait] 正在加载 transformers 模型（首次约 1–3 分钟），请稍候…")
        try:
            self._proc = subprocess.Popen(cmd, **self._popen_kwargs())
        except Exception as e:
            self._fail("启动 transformers 服务失败：%r" % e)
            return
        threading.Thread(target=self._stream, daemon=True).start()
        self._await_ready(600)

    def _await_ready(self, timeout):
        ok = self._probe(timeout)
        if ok:
            if self._done:
                return
            self._done = True
            self.server_started.emit()
            return
        if self._done:  # _probe 已因进程退出而 _fail
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
                self.log.emit(line.rstrip("\n"))
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
            if self._done:
                return False
            if self._proc is not None and self._proc.poll() is not None:
                code = self._proc.poll()
                self._fail(
                    "OCR 服务进程启动后立即退出（退出码 %s）。\n"
                    "常见原因：① Windows Smart App Control 拦截未签名 exe"
                    "（右键 bin\\llama-server.exe → 属性 → 解除阻止）；"
                    "② 启动参数 / 模型路径错误。详见上方日志。" % code
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
