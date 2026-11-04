#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ServerStarter 修复单测（headless / offscreen）：

覆盖三类修复：
1. 平台化失败文案 —— Linux/macOS 不再出现 Windows 专属的 .exe / Smart App Control 措辞；
2. 子进程输出落盘 —— _stream 的每行引擎日志同时写进 pic2edit.log（经 _logger）；
3. 拉起前显存预检 —— free_vram_mib / estimate_vram_mib 估算，显存不足时直接给出友好提示，
   不再让 ggml 直接 SIGABRT（-6）崩溃。
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy
from unittest import mock

from backends import server_launcher as S
import run_llamacpp_server  # 确保 discover / build_command 可用


def _app():
    return QApplication.instance() or QApplication(sys.argv + ["-platform", "offscreen"])


# ---------------- 1. 平台化失败文案 ----------------
def test_platform_msgs_linux():
    with mock.patch.object(S, "_IS_WINDOWS", False):
        msg = S._server_not_found_msg()
        assert "download_models.sh" in msg
        assert ".exe" not in msg
        assert "Smart App Control" not in S._launch_fail_hint()
        assert "Smart App Control" not in S._probe_exit_hint()
        assert "nvidia-smi" in S._probe_exit_hint()


def test_platform_msgs_windows():
    with mock.patch.object(S, "_IS_WINDOWS", True):
        msg = S._server_not_found_msg()
        assert "llama-server.exe" in msg and "download_models.bat" in msg
        assert "Smart App Control" in S._launch_fail_hint()
        assert "Smart App Control" in S._probe_exit_hint()


# ---------------- 3. 显存预检 helpers ----------------
def test_free_vram_mib_no_smi():
    with mock.patch.object(S.shutil, "which", return_value=None), \
         mock.patch.object(S.os.path, "exists", return_value=False):
        assert S.free_vram_mib() is None


def test_free_vram_mib_parses():
    fake = mock.Mock()
    fake.returncode = 0
    fake.stdout = "5037\n"  # 多 GPU 时取最大空闲
    with mock.patch.object(S.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
         mock.patch.object(S.os.path, "exists", return_value=True), \
         mock.patch.object(S.subprocess, "run", return_value=fake):
        assert S.free_vram_mib() == 5037


def test_estimate_vram_mib():
    with tempfile.TemporaryDirectory() as d:
        m = os.path.join(d, "m.gguf")
        mm = os.path.join(d, "mm.gguf")
        with open(m, "wb") as f:
            f.write(b"0" * (1024 * 1024 * 1024))        # 1 GiB
        with open(mm, "wb") as f:
            f.write(b"0" * (512 * 1024 * 1024))        # 0.5 GiB
        est = S.estimate_vram_mib(m, mm)
        assert 1400 < est < 1800                        # ~1.725 GiB（含 15% 余量）
    assert S.estimate_vram_mib(None, None) == 0


def test_vram_precheck_fails_before_launch():
    _app()
    st = S.ServerStarter("llama_cpp", "http://localhost:8080",
                         cfg=type("C", (), {})())
    spy = QSignalSpy(st.failed)
    with mock.patch.object(run_llamacpp_server, "discover",
                           return_value=("/bin/true", "/x/m.gguf", "/x/mm.gguf")), \
         mock.patch.object(S, "free_vram_mib", return_value=1000), \
         mock.patch.object(S, "estimate_vram_mib", return_value=2600):
        st._start_llamacpp()
    assert spy.count() == 1
    msg = spy.at(0)[0]
    assert "显存" in msg and "Ollama" in msg


# ---------------- 2. 子进程输出落盘 ----------------
def test_stream_emits_and_logs():
    _app()
    st = S.ServerStarter("llama_cpp", "http://x", cfg=type("C", (), {})())
    captured = []
    st.log.connect(lambda m: captured.append(m))
    fake_logger = mock.Mock()
    with mock.patch.object(S, "_logger", fake_logger):
        class FakeProc:
            stdout = iter(["line1\n", "CUDA error: out of memory\n"])
        st._proc = FakeProc()
        st._stream()
    assert captured == ["line1", "CUDA error: out of memory"]
    info_calls = [c.args[0] for c in fake_logger.info.call_args_list]
    assert any("[engine]" in c for c in info_calls)


def test_fail_sets_done_and_emits_once():
    _app()
    st = S.ServerStarter("llama_cpp", "http://x", cfg=type("C", (), {})())
    spy = QSignalSpy(st.failed)
    st._fail("boom")
    assert st._done.is_set()
    assert spy.count() == 1
    assert spy.at(0)[0] == "boom"
    st._fail("again")  # 已 done，应被忽略
    assert spy.count() == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
