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

import backend_api as api
from backend_api import RoutingPolicy


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
