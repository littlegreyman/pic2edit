#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引擎拉起的诊断单测（headless / offscreen）：

覆盖三类修复：
1. 平台化失败文案 —— Linux/macOS 不再出现 Windows 专属的 .exe / Smart App Control 措辞；
2. 子进程输出落盘 —— _stream 的每行引擎日志同时写进 pic2edit.log（经 _logger）；
3. 拉起前显存预检 —— free_vram_mib / estimate_vram_mib 估算，显存不足时直接给出友好提示，
   不再让 ggml 直接 SIGABRT（-6）崩溃。

注意：1 与 3 的实现已下沉到 ``backends/engine_boot.py``（非 Qt），由 GUI 的
``ServerStarter`` 与 skill 的 ``ensure_server`` **共用**；测试因此指向 engine_boot，
只对 GUI 自己的线程行为（_stream / _fail / _start_llamacpp）用 ServerStarter。
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
from backends import engine_boot as B
import run_llamacpp_server  # 确保 discover / build_command 可用


def _app():
    return QApplication.instance() or QApplication(sys.argv + ["-platform", "offscreen"])


# ---------------- 1. 平台化失败文案 ----------------
def test_platform_msgs_linux():
    with mock.patch.object(B, "_IS_WINDOWS", False):
        msg = B.server_not_found_msg()
        assert "download_models.sh" in msg
        assert ".exe" not in msg
        assert "Smart App Control" not in B.launch_fail_hint()
        assert "Smart App Control" not in B.probe_exit_hint()
        assert "nvidia-smi" in B.probe_exit_hint()


def test_platform_msgs_windows():
    with mock.patch.object(B, "_IS_WINDOWS", True):
        msg = B.server_not_found_msg()
        assert "llama-server.exe" in msg and "download_models.bat" in msg
        assert "Smart App Control" in B.launch_fail_hint()
        assert "Smart App Control" in B.probe_exit_hint()


def test_server_starter_uses_shared_diagnostics():
    """GUI 的文案必须就是 engine_boot 的那一份，不能各写一套（否则又会漂移）。"""
    assert S.server_not_found_msg is B.server_not_found_msg
    assert S.probe_exit_hint is B.probe_exit_hint
    assert S.free_vram_mib is B.free_vram_mib


# ---------------- 3. 显存预检 helpers ----------------
def test_free_vram_mib_no_smi():
    with mock.patch.object(B.shutil, "which", return_value=None), \
         mock.patch.object(B.os.path, "exists", return_value=False):
        assert B.free_vram_mib() is None


def test_free_vram_mib_parses():
    fake = mock.Mock()
    fake.returncode = 0
    fake.stdout = "5037\n"  # 多 GPU 时取最大空闲
    with mock.patch.object(B.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
         mock.patch.object(B.os.path, "exists", return_value=True), \
         mock.patch.object(B.subprocess, "run", return_value=fake):
        assert B.free_vram_mib() == 5037


def test_estimate_vram_mib():
    # 用 truncate 造稀疏文件，不要 ``f.write(b"0" * 1GiB)``：后者会一次性申请
    # 1 GiB 连续内存，在本项目 4GB 虚拟内存上限、且前面已加载 cv2/PySide6/
    # PDFium 的全量套件里直接 MemoryError（单独跑却通过，极易误判为偶发）。
    # estimate_vram_mib 只读 os.path.getsize，文件内容根本不会被读。
    with tempfile.TemporaryDirectory() as d:
        m = os.path.join(d, "m.gguf")
        mm = os.path.join(d, "mm.gguf")
        with open(m, "wb") as f:
            f.truncate(1024 * 1024 * 1024)              # 1 GiB
        with open(mm, "wb") as f:
            f.truncate(512 * 1024 * 1024)               # 0.5 GiB
        est = B.estimate_vram_mib(m, mm)
        assert 1400 < est < 1800                        # ~1.725 GiB（含 15% 余量）
    assert B.estimate_vram_mib(None, None) == 0


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


def test_gui_starter_passes_url_port_to_engine(monkeypatch):
    """GUI 侧同样要把 url 的端口传给拉起命令（与 skill 共用同一份诊断逻辑）。"""
    _app()
    seen = {}

    monkeypatch.setattr(run_llamacpp_server, "discover",
                        lambda: ("/bin/true", "/x/m.gguf", "/x/mm.gguf"))
    monkeypatch.setattr(S, "free_vram_mib", lambda: None)
    monkeypatch.setattr(S, "estimate_vram_mib", lambda *a, **k: 0)
    real_build = run_llamacpp_server.build_command

    def _spy(*a, **kw):
        seen.update(kw)
        return real_build(*a, **kw)

    monkeypatch.setattr(run_llamacpp_server, "build_command", _spy)

    def _boom(*a, **kw):
        raise RuntimeError("到此为止")

    monkeypatch.setattr(S.EngineLauncher, "start", _boom)

    st = S.ServerStarter("llama_cpp", "http://127.0.0.1:8282", cfg=type("C", (), {})())
    st._start_llamacpp()

    assert seen.get("port") == "8282", f"GUI 拉起端口必须来自 url，实得 {seen.get('port')}"


# ---------------- 4. 引擎必须听在调用方指定的端口 ----------------
def test_start_transformers_passes_mode_and_port(monkeypatch):
    """``--mode``（分辨率）与 ``--port`` 必须真的传给 transformers 服务。

    审查报告 §5-2：GUI 的“分辨率模式”下拉只写进状态栏文字，从不传给引擎，是伪能力。
    接上之后这条测试盯住"确实进了启动命令"。
    """
    _app()
    seen = {}

    monkeypatch.setattr(S.os.path, "isfile", lambda p: True)   # 假装 .venv_tf 已就绪
    monkeypatch.setattr(S, "port_of", lambda url: "8009")

    class _FakeLauncher:
        def start(self, cmd, **kw):
            seen["cmd"] = list(cmd)
            raise RuntimeError("到此为止")

    monkeypatch.setattr(S, "EngineLauncher", _FakeLauncher)

    cfg = type("C", (), {"resolution": "Small"})()
    st = S.ServerStarter("transformers", "http://localhost:8009", cfg=cfg)
    st._start_transformers()

    cmd = seen["cmd"]
    assert cmd[cmd.index("--mode") + 1] == "Small", f"分辨率没传给服务：{cmd}"
    assert cmd[cmd.index("--port") + 1] == "8009", f"端口没传给服务：{cmd}"


def test_transformers_launcher_builds_cmd_with_mode():
    """``run_transformers_server.build_cmd`` 是 --mode / --port 的唯一拼装点。"""
    import run_transformers_server as R

    cmd = R.build_cmd("py", "/m", "8bit", port="8010", mode="Base")
    assert cmd[cmd.index("--mode") + 1] == "Base"
    assert cmd[cmd.index("--port") + 1] == "8010"
    # 缺省时退回 Gundam / 默认端口，不能抛
    cmd2 = R.build_cmd("py", "/m", "8bit")
    assert cmd2[cmd2.index("--mode") + 1] == R.DEFAULT_MODE


def test_port_of_reads_url():
    assert B.port_of("http://127.0.0.1:8081") == "8081"
    assert B.port_of("http://localhost:9999/") == "9999"
    # 没写端口时退回默认，不能抛
    assert B.port_of("http://127.0.0.1") == B._engine_cli.PORT
    assert B.port_of("http://127.0.0.1:notaport") == B._engine_cli.PORT


def test_build_command_honours_port():
    cmd = run_llamacpp_server.build_command("exe", "m.gguf", "mp.gguf", port="8181")
    assert cmd[cmd.index("--port") + 1] == "8181"
    cmd2 = run_llamacpp_server.build_command("exe", "m.gguf", "mp.gguf")
    assert cmd2[cmd2.index("--port") + 1] == run_llamacpp_server.PORT


def test_ensure_engine_launches_on_requested_port(monkeypatch):
    """``ensure_engine`` 必须把 url 里的端口传给拉起命令。

    实测踩到过：指定 8081 时服务仍起在默认 8080，然后去 8081 等就绪 → 必然超时，
    同时留下一个没人回收的 8080 进程。
    """
    seen = {}
    monkeypatch.setattr(B, "probe", lambda base: False)
    monkeypatch.setattr(B._engine_cli, "discover", lambda: ("exe", "m.gguf", "mp.gguf"))
    monkeypatch.setattr(B, "estimate_vram_mib", lambda *a, **k: 0)
    monkeypatch.setattr(B, "free_vram_mib", lambda: None)

    real_build = B._engine_cli.build_command

    def _spy(*a, **kw):
        seen.update(kw)
        return real_build(*a, **kw)

    monkeypatch.setattr(B._engine_cli, "build_command", _spy)

    def _boom(*a, **kw):
        raise RuntimeError("到此为止，不真的拉起进程")

    monkeypatch.setattr(B.EngineLauncher, "start", _boom)

    try:
        B.ensure_engine("http://127.0.0.1:8181", gpu_layers=0, timeout=1)
    except B.EngineBootError:
        pass
    else:
        raise AssertionError("启动失败必须抛 EngineBootError")
    assert seen.get("port") == "8181", f"拉起端口必须是 url 里的端口，实得 {seen.get('port')}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
