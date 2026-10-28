#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯逻辑单测：验证 assemble_markdown 的段落重组，不依赖 RapidOCR/GPU。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backends.discriminative_backend import assemble_markdown


def L(text, y0, y1, x0=100, x1=800):
    return {"text": text, "y0": y0, "y1": y1, "x0": x0, "x1": x1, "score": 0.9}


# 用户反馈场景：第一章/总则 同行、顺序应为“第一章 总则”，且章标题后应有换行
lines = [
    L("第一章", 100, 128, x0=300),          # 章号（左）
    L("总则",   100, 128, x0=430),          # 章名（右，同 y 波段）
    L("第一条", 200, 228, x0=80),
    L("为规范深圳市注册会计师行业自律管理行为，促进注册会计师行业健康有序发展，", 235, 263, x0=110),
    L("发挥注册会计师在社会经济活动中的鉴证和服务作用。", 268, 296, x0=110),
    L("第二条", 360, 388, x0=80),
    L("本章程是深圳市注册会计师行业自律性组织。", 395, 423, x0=110),
]

md = assemble_markdown(lines, H=1000)
print("===== OUTPUT =====")
print(md)
print("===== END =====")

checks = []
checks.append(("第一章在总则之前（顺序正确）", md.index("第一章") < md.index("总则")))
checks.append(("不出现“总则第一章”逆序", "总则第一章" not in md))
checks.append(("章标题与章名同行且两个空格", "第一章  总则" in md))
checks.append(("章标题后另起段落（与第一条之间有空行）", "\n\n第一条" in md))
checks.append(("第一条与正文同行且两个空格", md.count("第一条  为规范深圳市") == 1 and "第一条\n为规范" not in md))
checks.append(("第一条正文中间无换行", "为规范深圳市注册会计师行业自律管理行为，促进注册会计师行业健康有序发展，发挥注册会计师在社会经济活动中的鉴证和服务作用。" in md))
checks.append(("第二条另起段落", "\n\n第二条" in md))

ok = True
for name, passed in checks:
    print(("PASS" if passed else "FAIL"), "-", name)
    ok = ok and passed

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
