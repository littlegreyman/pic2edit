"""Phase 2 契约测试：把 Skill 钉在 backend_api 门面 API 上，防止内部签名一变静默断。

覆盖三类契约：
1. 门面对外接口面（Skill 依赖的所有名字都存在）——内部模块一旦改名/删除会立刻失败。
2. EngineLauncher 进程组回收契约——start 必须置于独立进程组、stop 必须整组回收，
   这是 2.2 修复「VLM 孤儿进程占显存」的核心，不能回退成裸 Popen.terminate。
3. GUI / Skill 路由一致性——相同运行环境下两侧 RoutingPolicy 必须完全一致。
"""
import os
import signal
import subprocess
import sys
import tempfile
import time
import logging
import io
from unittest import mock

import backend_api as api
from backend_api import RoutingPolicy
from backends import ocr_pipeline



def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return False


def test_facade_surface_for_skill():
    """Skill 经 backend_api 使用的所有名字都必须在门面上存在。"""
    needed = [
        "run", "pipeline_run", "RoutingPolicy", "EngineLauncher", "ServerStarter",
        "create_backend", "vmem_limited_gb", "env_force_skip",
        "LlamaCppBackend", "DiscriminativeBackend", "TableRecognizer",
    ]
    missing = [n for n in needed if not hasattr(api, n)]
    assert not missing, f"门面缺少 Skill 依赖的符号: {missing}"


def test_create_backend_factories():
    """后端工厂按名称返回正确的后端实例。"""
    assert isinstance(api.create_backend("ollama", url="http://x"), api.OllamaBackend)
    assert isinstance(api.create_backend("transformers", url="http://x"), api.TransformersBackend)
    assert isinstance(api.create_backend("llama_cpp", url="http://x"), api.LlamaCppBackend)


def test_engine_launcher_start_uses_new_session(monkeypatch):
    """start 必须把引擎置于独立进程组（start_new_session=True），才能整组回收。"""
    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            captured.update(kw)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    el = api.EngineLauncher()
    el.start(["echo", "hi"])
    assert captured.get("start_new_session") is True, "start 应置于独立进程组"


def test_engine_launcher_stop_kills_whole_group():
    """stop 必须整组回收 fork 出的 worker（模拟 llama-server worker），防孤儿占显存。

    用真实进程组验证：子进程再 fork 一个孙子(sleep)，stop 后孙子必须随之消失。
    """
    flag = tempfile.mktemp()
    code = (
        "import os, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        f"    open({flag!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(30)\n"
        "else:\n"
        "    time.sleep(30)\n"
    )
    el = api.EngineLauncher()
    proc = el.start(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(1.0)
        with open(flag) as f:
            gchild = int(f.read())
        before = _alive(gchild)
        el.stop(proc)
        time.sleep(1.0)
        after = _alive(gchild)
        assert before and not after, (
            "stop 必须整组回收 fork 出的 worker（防孤儿进程占显存）"
        )
    finally:
        try:
            os.unlink(flag)
        except OSError:
            pass
        # 兜底：若测试异常，确保进程组被清掉，不留孤儿
        try:
            el.kill(proc)
        except Exception:
            pass


def test_gui_skill_routing_parity_in_sandbox():
    """相同运行环境下，GUI(worker) 与 Skill 构造的 RoutingPolicy 必须一致。

    这正是 2.3 修复的目标：此前 GUI 仅传页面类型，沙箱受限(vmem<=6GB)下不跳过判别式，
    而 Skill 跳过——两侧行为不一致。现在两侧都注入 vmem_limited_gb / force_skip。
    """
    vmem = api.vmem_limited_gb()
    force = api.env_force_skip()
    for page_type in ("markdown", "table", "figure", "html_table"):
        gui = RoutingPolicy.for_page_type(
            page_type, force_skip=force, vmem_limited_gb=vmem, hw_detected=False,
        )
        skill = RoutingPolicy.for_page_type(
            page_type, force_skip=force, vmem_limited_gb=vmem, hw_detected=False,
        )
        assert gui == skill, f"{page_type}: GUI 与 Skill 路由不一致"


def test_routing_sandbox_skips_discriminative():
    """沙箱 vmem<=6GB 时，普通 markdown 页也应跳过判别式（与 Skill 行为对齐）。"""
    vmem = api.vmem_limited_gb()
    pol = RoutingPolicy.for_page_type(
        "markdown", force_skip=False, vmem_limited_gb=vmem, hw_detected=False,
    )
    if 0 < vmem <= 6:
        assert pol.skip_discriminative is True


class _FakeItem:
    def __init__(self, name, path=""):
        self.name = name
        self.image_path = path
        self.result = ""
        self.status = ""


class _FakeBackend:
    """可记录调用次数的假后端，ocr_image 返回固定文本。"""

    def __init__(self, text):
        self._text = text
        self.calls = 0

    def available(self):
        return True

    def ocr_image(self, path, prompt=None):
        self.calls += 1
        return self._text


def _capture_pipeline_logs():
    buf = io.StringIO()
    lg = logging.getLogger("pic2edit.ocr_pipeline")
    lg.setLevel(logging.INFO)
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.INFO)
    lg.addHandler(handler)
    lg.propagate = False
    return buf, handler


def test_pipeline_logs_source_disc_when_discriminative_ok():
    """修复可观测性缺口：干净中文页走判别式时，日志须记录 source=disc 且 VLM 不触发。

    此前成功走判别式时全程静默，无法确认 VLM 是否被调用。
    """
    buf, handler = _capture_pipeline_logs()
    disc = _FakeBackend("注册会计师协会自律监督工作意见全文。")
    vlm = _FakeBackend("VLM 兜底输出（不应出现）")
    try:
        items = [_FakeItem("第1页", "/tmp/p1.png"), _FakeItem("第2页", "/tmp/p2.png")]
        with mock.patch.object(ocr_pipeline, "DiscriminativeBackend", lambda: disc):
            ocr_pipeline.run(items, vlm_backend=vlm, skip_discriminative=False,
                            guard_hallucination=True, mode="markdown")
        log = buf.getvalue()
        assert "OCR pipeline start:" in log, "应记录任务级路由决策"
        assert log.count("source=disc") == 2, "每页都应记 source=disc"
        assert vlm.calls == 0, "干净中文页不应触发 VLM 兜底"
    finally:
        logging.getLogger("pic2edit.ocr_pipeline").removeHandler(handler)


def test_pipeline_logs_source_vlm_on_discriminative_failure():
    """判别式失败时须走 VLM 兜底，并记录 source=vlm。"""
    buf, handler = _capture_pipeline_logs()
    disc = _FakeBackend("")  # 判别式空结果
    vlm = _FakeBackend("VLM 识别出的中文内容。")
    try:
        items = [_FakeItem("扫描页", "/tmp/s.png")]
        with mock.patch.object(ocr_pipeline, "DiscriminativeBackend", lambda: disc):
            ocr_pipeline.run(items, vlm_backend=vlm, skip_discriminative=False,
                            guard_hallucination=True, mode="markdown")
        log = buf.getvalue()
        assert "source=vlm" in log, "判别式失败应记 source=vlm"
        assert vlm.calls == 1, "判别式空结果应触发 VLM 兜底一次"
    finally:
        logging.getLogger("pic2edit.ocr_pipeline").removeHandler(handler)

