"""非 Qt 的 OCR 引擎拉起器：统一 POSIX / Windows 的进程组管理。

解决孤儿进程隐患：直接 ``subprocess.Popen`` 只持有直接子进程 pid，引擎（如
llama-server）常派生 worker 子进程，仅 ``terminate()`` 直接子进程会留下孤儿持续占显存。
本模块用「进程组」拉起与回收——POSIX 用 ``start_new_session`` + ``os.killpg(SIGTERM)``，
Windows 用 ``CREATE_NEW_PROCESS_GROUP`` + ``CTRL_BREAK_EVENT`` 打到进程组。

``ServerStarter``（GUI）与 skill 的 ``ensure_server()`` 都应基于它，避免两套
进程生命周期、两套孤儿进程隐患。
"""
from __future__ import annotations

import os
import signal
import subprocess

_IS_WINDOWS = os.name == "nt"


class EngineLauncher:
    """跨平台、基于进程组的引擎拉起与回收。"""

    def __init__(self) -> None:
        self._proc: "subprocess.Popen | None" = None

    def start(
        self,
        cmd,
        *,
        cwd=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **extra,
    ) -> "subprocess.Popen":
        """拉起引擎子进程（置于独立进程组），返回 Popen。"""
        kwargs = dict(
            cwd=cwd,
            stdout=stdout,
            stderr=stderr,
            text=text,
            bufsize=bufsize,
        )
        kwargs.update(extra)
        if _IS_WINDOWS:
            # 新进程组：便于用 CTRL_BREAK_EVENT 一次性打到整组
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 不弹额外控制台窗口
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | flags
        else:
            # 新会话 -> 独立进程组，便于 os.killpg 整组回收
            kwargs["start_new_session"] = True
        self._proc = subprocess.Popen(cmd, **kwargs)
        return self._proc

    def stop(self, proc: "subprocess.Popen | None" = None) -> None:
        """优雅停止：把进程组整体 SIGTERM（Windows 为 CTRL_BREAK_EVENT）。"""
        proc = proc or self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if _IS_WINDOWS:
                # CTRL_BREAK_EVENT 打到进程组（需 CREATE_NEW_PROCESS_GROUP）
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                # 整组回收，避免留有孤儿占显存
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def kill(self, proc: "subprocess.Popen | None" = None) -> None:
        """强制停止：进程组整体 SIGKILL。"""
        proc = proc or self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if _IS_WINDOWS:
                proc.kill()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
