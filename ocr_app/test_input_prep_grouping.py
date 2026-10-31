#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单测：验证 prepare_* 返回 InputDocument 分组，结构正确（is_multi / pages / source）。

不依赖 pypdfium2 / GPU：用 Pillow 造多帧 TIF 与单图，纯逻辑验证分组模型。
"""
import os
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.input_prep import prepare_image, prepare_tif, InputDocument


def _work():
    return tempfile.mkdtemp(prefix="pic2edit_test_")


def test_single_image():
    work = _work()
    png = os.path.join(work, "single.png")
    Image.new("RGB", (40, 30), (255, 255, 255)).save(png)
    docs = prepare_image(png, work)
    assert len(docs) == 1
    d = docs[0]
    assert isinstance(d, InputDocument)
    assert d.name == "single.png"
    assert len(d.pages) == 1
    assert d.is_multi is False
    assert d.pages[0].source == png
    assert os.path.isfile(d.pages[0].image_path)
    print("PASS - 单图 -> 1 文档 1 页, is_multi=False")


def test_tif_three_frames():
    work = _work()
    tif = os.path.join(work, "multi.tif")
    frames = [Image.new("RGB", (60, 40), (i * 30, 100, 200)) for i in range(3)]
    frames[0].save(tif, save_all=True, append_images=frames[1:], format="TIFF")
    docs = prepare_tif(tif, work)
    assert len(docs) == 1
    d = docs[0]
    assert d.is_multi is True
    assert len(d.pages) == 3
    assert d.source_path == tif
    # 各页 source 都指向同一 TIF（分组依据）
    assert all(p.source == tif for p in d.pages)
    # 页名带帧号且按序
    assert "第1帧" in d.pages[0].name and "第3帧" in d.pages[2].name
    print("PASS - 3 帧 TIF -> 1 文档 3 页, is_multi=True")


if __name__ == "__main__":
    test_single_image()
    test_tif_three_frames()
    print("\nRESULT: ALL PASS (test_input_prep_grouping)")
    sys.exit(0)
