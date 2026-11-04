#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""坐标表格重建单测：两栏对照 -> Markdown 表格；单栏正文 -> 不误判。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from utils.table_layout import build_table_from_lines


def _two_col_lines(W=1000, H=1400, n=30):
    lines = []
    for i in range(n):
        y = 100 + i * 30
        lines.append({"text": f"左{i}", "x0": 60, "x1": 460, "y0": y, "y1": y + 18})
        lines.append({"text": f"右{i}", "x0": 540, "x1": 940, "y0": y, "y1": y + 18})
    return lines, W, H


def test_two_column_builds_table():
    lines, W, H = _two_col_lines()
    md = build_table_from_lines(lines, W, H)
    assert md, "两栏对照应生成表格"
    rows = [ln for ln in md.splitlines() if ln.startswith("|")]
    assert len(rows) >= 5
    assert "| --- | --- |" in md
    assert "左0" in md and "右0" in md


def test_single_column_returns_empty():
    lines = []
    W, H = 1000, 1400
    for i in range(30):
        y = 100 + i * 30
        lines.append({"text": f"正文第{i}行", "x0": 60, "x1": 900,
                      "y0": y, "y1": y + 18})
    assert build_table_from_lines(lines, W, H) == "", "单栏正文不应被误判为表格"


def test_empty_returns_empty():
    assert build_table_from_lines([], 0, 0) == ""


def test_page_number_noise_filtered():
    lines, W, H = _two_col_lines(n=5)
    # 页脚页码应被过滤，不影响表格
    lines.append({"text": "— 1 —", "x0": 480, "x1": 520, "y0": 0.985 * H, "y1": H})
    md = build_table_from_lines(lines, W, H)
    assert md and "— 1 —" not in md
