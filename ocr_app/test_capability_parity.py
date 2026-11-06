# -*- coding: utf-8 -*-
"""能力对等测试：两个前端不能一边有一边没有。

第一批（Phase 3.3）是 skill → GUI 的三项：手写预筛、按显存选模型、密码区掩膜。
第二批（2026-11-06 审查）是 GUI → skill 的三项，当时**一项都没补**：
  ④ `html_table` 模式（合并单元格表格）在 CLI 不可达；
  ⑤ 采样参数（temperature / repeat-penalty / repeat-last-n / max-tokens）不可调；
  ⑥ 引擎失败诊断（显存预检 + 引擎输出落盘 + 平台化提示）缺失，
     同一台机器同一次 exit -6，GUI 给出"需 2.6GB、空闲 1.0GB"，skill 只给退出码。

下沉后实现全在内核（utils/ + backends/），两个前端经门面调用。
本测试守三件事：门面确实可用、行为一致、skill 里不再有重复实现。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backend_api as api

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.join(_HERE, "..", ".workbuddy", "skills", "pic2edit-ocr",
                      "scripts", "pic2edit_convert.py")


def _skill_source() -> str:
    if not os.path.isfile(_SKILL):
        pytest.skip("skill 脚本不在当前检出中")
    return open(_SKILL, encoding="utf-8").read()


# ------------------------------------------------------------------ 门面
def test_facade_exposes_sunk_capabilities():
    for name in ("detect_handwriting", "choose_vlm", "describe_vlm_choice",
                 "locate_password_region", "apply_password_mask",
                 "parse_mask_rect", "DEFAULT_VAT_RECT"):
        assert hasattr(api, name), f"门面缺少 {name}"


def test_facade_exposes_engine_diagnostics():
    """引擎诊断必须能从门面拿到——skill 只被允许 import 门面。"""
    for name in ("ensure_engine", "EngineHandle", "EngineBootError",
                 "free_vram_mib", "estimate_vram_mib"):
        assert hasattr(api, name), f"门面缺少 {name}"


def test_facade_exposes_prompt_and_params():
    """提示词与采样参数属于识别策略，应由内核提供，前端不各写一份。"""
    assert "html_table" in api.list_modes()
    assert api.prompt_for_mode("html_table") != api.prompt_for_mode("markdown")
    assert hasattr(api.prompt_for_mode("nope"), "lower"), "未知 mode 应回退默认而不是抛错"

    mp = api.make_model_params(temperature=0.3)
    assert mp.temperature == 0.3
    assert mp.repeat_penalty == 1.10 and mp.repeat_last_n == 4096
    assert api.make_model_params(max_tokens=None).max_tokens == 16384


# ------------------------------------------------------------------ ④ 合并单元格表格
def test_skill_accepts_html_table_mode():
    src = _skill_source()
    assert "list_modes()" in src, "点 --mode 的 choices 应来自门面，避免再漏掉新格式"
    assert api.prompt_for_mode("html_table") in src or "prompt_for_mode" in src, \
        "必须把 mode 对应的提示词真正传给流水线，否则 html_table 只是改了个路由"


def test_skill_passes_prompt_to_pipeline():
    """--mode 必须改变提示词：此前恒用默认 ZH_PROMPT，html_table 的实际要求没传下去。"""
    src = _skill_source()
    assert "prompt=prompt_for_mode(mode)" in src


# ------------------------------------------------------------------ ⑤ 采样参数
def test_skill_exposes_sampling_flags():
    src = _skill_source()
    for flag in ("--temperature", "--repeat-penalty", "--repeat-last-n", "--max-tokens"):
        assert flag in src, f"skill 缺少 {flag}"
    assert "make_model_params(" in src


def test_skill_passes_model_params_to_vlm():
    src = _skill_source()
    assert "_VlmProbe(url, \"deepseek-ocr\", model_params)" in src, \
        "采样参数必须同时给到 VLM 后端（请求 payload），而不只是启动命令行"
    assert "model_params=model_params" in src, "启动命令行也要带上重复惩罚等参数"


# ------------------------------------------------------------------ ⑥ 引擎诊断
def test_skill_uses_shared_engine_boot():
    """不得再自己 Popen 并把引擎输出 DEVNULL 掉。"""
    src = _skill_source()
    assert "ensure_engine(" in src
    assert "stdout=subprocess.DEVNULL" not in src, "引擎输出被丢弃就永远看不到 CUDA 报错"
    assert "EngineBootError" in src


def test_ensure_engine_reports_vram_shortage_before_launch(monkeypatch):
    """显存不足要在拉起前就给出可操作的说明，而不是等 ggml SIGABRT。"""
    from backends import engine_boot as B

    monkeypatch.setattr(B._engine_cli, "discover",
                        lambda: ("/bin/true", "/x/m.gguf", "/x/mm.gguf"))
    monkeypatch.setattr(B, "estimate_vram_mib", lambda m, mm: 2600)
    monkeypatch.setattr(B, "free_vram_mib", lambda: 1000)
    monkeypatch.setattr(B, "probe", lambda url, timeout=2.0: False)

    with pytest.raises(B.EngineBootError) as ei:
        B.ensure_engine("http://127.0.0.1:9", timeout=1)
    msg = str(ei.value)
    assert "显存" in msg and "Ollama" in msg


# ------------------------------------------------------------------ 掩膜
def test_parse_mask_rect_validation():
    assert api.parse_mask_rect("0.1,0.2,0.3,0.4") == (0.1, 0.2, 0.3, 0.4)
    assert api.parse_mask_rect("") is None
    assert api.parse_mask_rect("0.3,0.2,0.1,0.4") is None      # 反向矩形
    assert api.parse_mask_rect("0.1,0.2") is None              # 个数不对
    assert api.parse_mask_rect("a,b,c,d") is None


def test_mask_whitens_requested_region(tmp_path):
    pytest.importorskip("PIL.Image")
    from PIL import Image

    src = tmp_path / "in.png"
    Image.new("RGB", (100, 100), (0, 0, 0)).save(src)
    dst = tmp_path / "out.png"
    api.apply_password_mask(str(src), str(dst), rect=(0.5, 0.5, 1.0, 1.0))

    im = Image.open(dst).convert("RGB")
    assert im.getpixel((75, 75)) == (255, 255, 255), "指定区域应被涂白"
    assert im.getpixel((10, 10)) == (0, 0, 0), "区域外不得改动"


def test_locate_falls_back_to_default_without_ocr(monkeypatch):
    """取不到行框时必须回退通用预设，而不是"随便涂一块"。"""
    monkeypatch.setattr("backends.mask._ocr_lines", lambda p: (None, 0, 0))
    assert api.locate_password_region("/tmp/whatever.png") == api.DEFAULT_VAT_RECT


def test_find_password_label_handles_stacked_chars():
    from backends.mask import find_password_label

    stacked = [{"text": c, "x0": 880, "x1": 900, "y0": 200 + i * 20, "y1": 218 + i * 20}
               for i, c in enumerate("密码区")]
    got = find_password_label(stacked)
    assert got is not None
    assert got["y0"] == 200 and got["y1"] == 258
    # 整词识别优先
    assert find_password_label([{"text": "密码区", "x0": 1, "x1": 2, "y0": 3, "y1": 4}])["x0"] == 1


# ------------------------------------------------------------------ 手写预筛
def test_detect_handwriting_returns_decision(tmp_path):
    pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    img = np.full((200, 300, 3), 255, np.uint8)
    for i in range(4):                       # 几条直线：典型的"印刷"倾向
        cv2.line(img, (20, 40 + i * 30), (280, 40 + i * 30), (0, 0, 0), 2)
    p = tmp_path / "print.png"
    cv2.imwrite(str(p), img)

    hw, conf = api.detect_handwriting(str(p))
    assert isinstance(hw, bool)
    assert 0.0 <= float(conf) <= 1.0


# ------------------------------------------------------------------ 选模型
def test_choose_vlm_returns_contract(tmp_path):
    sel = api.choose_vlm(str(tmp_path), pref="cpu")
    for k in ("tier", "model", "mmproj", "gpu_layers", "reason"):
        assert k in sel, f"选模型结果缺少 {k}"
    # 空目录里没有可用权重：应明确返回 None，让调用方回退内核默认
    assert sel["model"] is None
    assert "cpu" in sel["tier"]
    assert api.describe_vlm_choice(sel).startswith("[model]")


# ------------------------------------------------------------------ 静态守卫
def test_skill_has_no_duplicated_implementations():
    """skill 里不得再有这三项能力的第二份实现——两份迟早会漂移。"""
    here = os.path.dirname(os.path.abspath(__file__))
    skill = os.path.join(here, "..", ".workbuddy", "skills", "pic2edit-ocr",
                         "scripts", "pic2edit_convert.py")
    if not os.path.isfile(skill):
        pytest.skip("skill 脚本不在当前检出中")
    src = open(skill, encoding="utf-8").read()
    leaked = [s for s in ("from handwriting_screen", "import model_select",
                          "def _mask_copy", "def _locate_password_region",
                          "def _parse_mask_rect", "def _is_gibberish",
                          "def detect_handwriting")
              if s in src]
    assert not leaked, f"skill 里仍有重复实现: {leaked}"


def test_kernel_modules_do_not_import_skill_scripts():
    """下沉方向必须是 skill -> 内核，不能反向。"""
    here = os.path.dirname(os.path.abspath(__file__))
    code = ("import sys; sys.path.insert(0, %r); import backend_api; "
            "bad=[m for m in sys.modules if m.startswith(('handwriting_screen',"
            "'model_select'))]; assert not bad, bad" % here)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_cv2_thread_pool_is_bounded_for_qt():
    """OpenCV 线程池必须钉为 1，否则 GUI 预筛之后 PySide6 起不来。

    真跑全量测试时定位到：OpenCV 5 默认并行后端在 4GB 虚拟内存上限下
    "Can't spawn new thread: res = 11"，随后 QtWidgets.abi3.so 无法 mmap，
    QApplication([]) 挂起（表现是整套件卡住而不是失败）。修在
    ``utils/handwriting_screen.py`` 的导入处；这条守卫防止有人删掉那行。
    """
    cv2 = pytest.importorskip("cv2")
    assert cv2.getNumThreads() == 1, (
        "handwriting_screen 必须调用 cv2.setNumThreads(1)：Qt 与 OpenCV "
        "并行线程池同时存在会让 QApplication 挂起")
