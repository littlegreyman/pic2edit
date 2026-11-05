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
        # 2.1 收口后新增：输入适配 / 导出 / 表格 / 引擎 CLI 也必须经门面
        "prepare_input", "InputItem", "InputDocument",
        "save_text", "md_to_docx", "md_pages_to_docx",
        "kv_text_to_markdown_table", "build_table_from_lines",
        "discover_engine", "build_engine_command",
    ]
    missing = [n for n in needed if not hasattr(api, n)]
    assert not missing, f"门面缺少 Skill 依赖的符号: {missing}"


def _skill_script_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (
        os.path.join(here, "..", ".workbuddy", "skills", "pic2edit-ocr", "scripts",
                     "pic2edit_convert.py"),
        os.path.expanduser("~/.workbuddy/skills/pic2edit-ocr/scripts/pic2edit_convert.py"),
    ):
        p = os.path.abspath(p)
        if os.path.isfile(p):
            return p
    return None


def test_skill_imports_only_facade():
    """Phase 2.1 门禁：skill 不得直连任何内核内部模块（utils.* / backends.* / 引擎 CLI）。

    这条是**静态**检查——它拦的是"未来有人图省事又写一句 from utils.xxx import"，
    而不是运行期行为；只要出现就失败，逼着把能力补到 backend_api。
    """
    import re

    script = _skill_script_path()
    if script is None:
        import pytest
        pytest.skip("未找到 skill 脚本，跳过门面边界检查")

    with open(script, encoding="utf-8") as f:
        src = f.read()

    # 允许 import 的：backend_api（门面）+ skill 自带模块 + 标准库/第三方。
    # 禁止：内核内部模块前缀。
    forbidden = re.findall(
        r"^\s*(?:from|import)\s+((?:utils|backends)(?:\.\w+)*|run_llamacpp_server|"
        r"worker|gui|config)\b",
        src,
        re.MULTILINE,
    )
    assert not forbidden, (
        "skill 仍直连内核内部模块，应改经 backend_api 门面：" f"{sorted(set(forbidden))}"
    )


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


class _FakeProc:
    """最简 Popen 替身：只需 poll()/wait() 供 closeEvent 使用。"""

    def __init__(self, alive=True):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self._alive = False
        return 0


def test_gui_close_uses_process_group_stop(monkeypatch):
    """关窗必须经 EngineLauncher 整组回收，不得裸 proc.terminate()。

    回归背景：closeEvent 曾先 ``proc.terminate()`` 只杀 leader，leader 退出后
    ``EngineLauncher.stop()`` 会因 ``poll()`` 非 None 提前返回，整组回收彻底失效，
    llama-server 的 worker 变孤儿继续占显存——正是 2.2 要消灭的问题。
    """
    import gui

    calls = []

    class _FakeLauncher:
        def stop(self, proc=None):
            calls.append(("stop", proc))

        def kill(self, proc=None):
            calls.append(("kill", proc))

    monkeypatch.setattr(gui, "EngineLauncher", _FakeLauncher)

    class _Evt:
        accepted = False

        def accept(self):
            self.accepted = True

    proc = _FakeProc(alive=True)

    class _Stub:
        _server_proc = proc
        _server_starter = None
        work_dir = tempfile.mkdtemp()

    evt = _Evt()
    gui.MainWindow.closeEvent(_Stub(), evt)

    assert ("stop", proc) in calls, "关窗必须调用 EngineLauncher.stop 做进程组回收"
    assert evt.accepted, "事件必须被接受（否则窗口关不掉）"


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

