# -*- coding: utf-8 -*-
"""忠实版式（原图垫底 + 坐标文字层）的行为测试。

这条路的正确性标准只有两条：
  1. **位置来自 OCR，不是猜的**——所以坐标必须原样落到 span 的 left/top 上；
  2. **文字要能被选中复制**——所以是透明文字层，不是把字画进图片。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import faithful_html as fh  # noqa: E402


def _ln(text, x0, y0, x1, y1):
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_empty_inputs_return_empty():
    assert fh.build_overlay_html([], 800, 600) == ""
    assert fh.build_overlay_html([_ln("甲", 0, 0, 10, 10)], 0, 600) == ""
    assert fh.build_overlay_html([_ln("甲", 0, 0, 10, 10)], 800, 0) == ""


def test_positions_come_from_coordinates():
    html = fh.build_overlay_html([_ln("原值", 100, 50, 140, 70)], 800, 600, "x.png")
    assert "left:100.0px" in html
    assert "top:50.0px" in html
    assert "width:40.0px" in html
    assert "height:20.0px" in html
    assert ">原值</span>" in html
    # 文字层必须是透明的（否则会盖住底图，变成"重排"而不是"叠字"）
    assert "color: transparent" in fh.faithful_css()


def test_text_is_html_escaped():
    html = fh.build_overlay_html([_ln("<b>x</b>", 0, 0, 10, 10)], 100, 100, "x.png")
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;" in html


def test_blank_lines_skipped():
    html = fh.build_overlay_html([_ln("   ", 0, 0, 10, 10), _ln("甲", 0, 0, 10, 10)], 100, 100)
    assert html.count('class="t"') == 1


def test_toggle_is_wired_to_unique_id():
    html = fh.build_overlay_html([_ln("甲", 0, 0, 10, 10)], 100, 100, "x.png", uid="p7")
    assert 'id="fx-p7"' in html
    assert 'for="fx-p7"' in html


def test_embed_image_makes_data_uri(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    p = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (255, 0, 0)).save(p)
    uri = fh.embed_image(str(p))
    assert uri.startswith("data:image/png;base64,")
    assert len(uri) > 40


def test_embed_image_missing_or_too_big(tmp_path):
    assert fh.embed_image("") == ""
    assert fh.embed_image(str(tmp_path / "nope.png")) == ""
    Image = pytest.importorskip("PIL.Image")
    p = tmp_path / "b.png"
    Image.new("RGB", (64, 64), (0, 0, 0)).save(p)
    assert fh.embed_image(str(p), max_bytes=10) == ""


def test_page_with_image_uses_image_key(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    p = tmp_path / "c.png"
    Image.new("RGB", (40, 30), (255, 255, 255)).save(p)
    page = {"page": 1, "image": str(p), "lines": [_ln("甲", 1, 2, 11, 12)],
            "img_w": 40, "img_h": 30}
    html = fh.page_with_image(page, uid="p1")
    assert html and "data:image/png;base64," in html
    assert "width:40px;height:30px" in html


def test_page_with_image_without_image_falls_back_to_none():
    """没有原图就不该输出"白底看不见字"的页面，交给调用方重排。"""
    page = {"page": 1, "lines": [_ln("甲", 1, 2, 11, 12)], "img_w": 40, "img_h": 30}
    assert fh.page_with_image(page, uid="p1") is None
