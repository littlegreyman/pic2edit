# -*- coding: utf-8 -*-
"""Phase 3.3：能力下沉后的"两侧能力对等"测试。

这三项能力此前只存在于 skill 私有脚本里，GUI 完全没有：
  ① 手写/印刷预筛（GUI 硬编码 hw_detected=False，手写页两侧路由可分歧）；
  ② 按显存选模型（GUI 只会用 discover() 的默认权重）；
  ③ 密码区掩膜（GUI 侧有 bug 时根本无法掩膜）。

下沉后它们的**实现**在内核（utils/ + backends/），两个前端经门面调用。
本测试守三件事：门面确实可用、行为与原先一致、skill 里不再有重复实现。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backend_api as api


# ------------------------------------------------------------------ 门面
def test_facade_exposes_sunk_capabilities():
    for name in ("detect_handwriting", "choose_vlm", "describe_vlm_choice",
                 "locate_password_region", "apply_password_mask",
                 "parse_mask_rect", "DEFAULT_VAT_RECT"):
        assert hasattr(api, name), f"门面缺少 {name}"


# ------------------------------------------------------------------ 掩膜
def test_parse_mask_rect_validation():
    assert api.parse_mask_rect("0.1,0.2,0.3,0.4") == (0.1, 0.2, 0.3, 0.4)
    assert api.parse_mask_rect("") is None
    assert api.parse_mask_rect("0.3,0.2,0.1,0.4") is None      # 反向矩形
    assert api.parse_mask_rect("0.1,0.2") is None              # 个数不对
    assert api.parse_mask_rect("a,b,c,d") is None


def test_mask_whitens_requested_region(tmp_path):
    pil = pytest.importorskip("PIL.Image")
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
