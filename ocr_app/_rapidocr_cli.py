#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RapidOCR 判别式 OCR 的 CLI 包装。

输入:  argv[1] = 图像路径
       argv[2] = 可选输出 JSON 路径（不传则 JSON 打到 stdout）
输出:  {"lines":[{"text","y0","y1","x0","x1","score"}], "img_w","img_h"} 或 {"error":"..."}
说明:  只做判别式识别（det+rec+cls），不调用任何生成式模型，因此零幻觉。

依赖:  rapidocr>=3.10（支持 Python 3.14），由调用方所在 venv（默认 ocr_app/.venv）提供。
       首次运行会自动下载 PP-OCR onnx 模型到包内 models 目录（需联网一次）。
"""
import sys
import os
import json

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _emit(data, out_path):
    s = json.dumps(data, ensure_ascii=False)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(s)
    else:
        print(s)


def main():
    if len(sys.argv) < 2:
        _emit({"error": "missing image path"}, sys.argv[2] if len(sys.argv) > 2 else None)
        return 2
    img = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        from rapidocr import RapidOCR
    except Exception as e:
        _emit({"error": "rapidocr import failed: %s。请在本 venv 安装 rapidocr（pip install rapidocr onnxruntime）" % e},
              out_path)
        return 3
    try:
        engine = RapidOCR()
        result = engine(img)
    except Exception as e:
        _emit({"error": "ocr failed: %s" % e}, out_path)
        return 4
    # result 为 RapidOCROutput；空结果时 txts=None / len==0
    if result is None or getattr(result, "txts", None) is None or len(result) == 0:
        _emit({"lines": [], "img_w": 0, "img_h": 0}, out_path)
        return 0
    try:
        from PIL import Image
        with Image.open(img) as im:
            W, H = im.size
    except Exception:
        W = H = 0
    boxes = np.asarray(result.boxes)
    lines = []
    for i, (text, score) in enumerate(zip(result.txts, result.scores)):
        box = boxes[i]
        ys = box[:, 1]
        xs = box[:, 0]
        lines.append({
            "text": text,
            "y0": float(min(ys)), "y1": float(max(ys)),
            "x0": float(min(xs)), "x1": float(max(xs)),
            "score": round(float(score), 3),
        })
    _emit({"lines": lines, "img_w": W, "img_h": H}, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
