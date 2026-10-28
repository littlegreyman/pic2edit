#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯逻辑单测：验证 assemble_markdown 的段落重组，不依赖 RapidOCR/GPU。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backends.discriminative_backend import assemble_markdown

# 模拟用户反馈的三类问题：
# 1) “第二章” 与 “党建工作” 应同行、两个空格
# 2) “第X条” 之后应两个空格而非换行
# 3) 长句“本会工作应公开透明…”中间不应有换行
lines = [
    {"text": "第二章",        "y0": 100, "y1": 130, "x0": 200, "x1": 320, "score": 0.9},
    {"text": "党建工作",      "y0": 135, "y1": 160, "x0": 200, "x1": 320, "score": 0.9},
    {"text": "第六条",        "y0": 200, "y1": 228, "x0": 80,  "x1": 160, "score": 0.9},
    {"text": "本会工作应公开透明，理事会，监督委员会的决议、决定及协会财务收支报告，", "y0": 235, "y1": 263, "x0": 110, "x1": 800, "score": 0.9},
    {"text": "审计报告等须向全体会员公开。", "y0": 268, "y1": 296, "x0": 110, "x1": 600, "score": 0.9},
    {"text": "第七条",        "y0": 360, "y1": 388, "x0": 80,  "x1": 160, "score": 0.9},
    {"text": "本章程是深圳市注册会计师行业自律性组织。", "y0": 395, "y1": 423, "x0": 110, "x1": 800, "score": 0.9},
]

md = assemble_markdown(lines, H=1000)
print("===== OUTPUT =====")
print(md)
print("===== END =====")

checks = []
checks.append(("第二章与党建工作同行且两个空格",
               "第二章  党建工作" in md))
checks.append(("第六条与正文同行且两个空格",
               md.count("第六条  本会工作应公开透明") == 1 and "第六条\n本会" not in md))
checks.append(("长句中间无换行",
               "本会工作应公开透明，理事会，监督委员会的决议、决定及协会财务收支报告，审计报告等须向全体会员公开。" in md))
checks.append(("第七条另起段落（段间空行）",
               "\n\n第七条" in md))

ok = True
for name, passed in checks:
    print(("PASS" if passed else "FAIL"), "-", name)
    ok = ok and passed

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
