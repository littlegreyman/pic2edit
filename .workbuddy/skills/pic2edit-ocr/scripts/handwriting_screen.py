#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""handwriting_screen.py — 手写 / 印刷 二分类预筛（numpy + OpenCV，无模型、无权重文件）。

⚠️ 状态：**实验性，默认关闭（独立盲测未通过）**
==============================================
> **2026-11-01 补记（重要，先读）**：样本清理后（79/80 → 57/57）重测，本模块**当前实现的
> SELF/CC 判据**依旧不可用；但另两套特征（行内块高 CV、上沿线/下底夹角）在加上
> 「ngrp≥5」门控后表现显著变好：融合判据 R3 在生产口径下能拦下 99% 手写页，
> 代价是 42% 印刷页白跑 VLM。**R3 目前只存在于离线评估脚本，尚未并入本模块。**
> 完整数据见 skill 外报告 handwriting_reevaluation.md；要落地 R3 需把两套特征搬进来
> （约 130 行），且需你确认默认开关策略。

独立盲测集（均未参与标定）：印刷 20 张（PrintType：图书内页 / 试卷 / 红头文件 /
会计凭证 / 打印稿 / 英文书页 / 打字机稿）+ 手写 19 张（handwrite，去重后）。
结果：
    误报（印刷→手写）13/20 = **65%**
    漏报（手写→印刷） 0/19 = 0%（19 样本零漏报的 95% 置信上界约 15.8%）
    准确率 66.7%  精确率 59.4%  召回率 100%
标定集上误报只有 5/25=20%，盲测恶化到 65% → 高分主要来自「在同一样本上选阈值」。

判定规则与在标定集上的结果：
      SELF 可用 → SELF < 0.20 判手写
      SELF 不可用（字形块不足）→ CC < 0.46 判手写

**为什么不是调阈值就能救**（这三条是关键证据，别再重复试）
----------------------------------------------------------
SELF 类间区间完全重叠：
      手写 0.000 ×13, 0.104, 0.190, NaN ×4
      印刷 0.000 ×3, 0.100, 0.109, 0.113, 0.133, 0.170, 0.190,
           0.250, 0.267, 0.333, 0.358, 0.425, 0.433, NaN ×5
      手写最大值 0.190 == 印刷第 9 小的 0.190（同一数值）
      印刷最小值 0.000 == 手写众数
对 (SELF_t, CC_t) 做全网格搜索、约束「漏报=0」：OR 最优 65% 误报，
AND 最优仍有 50% 误报（且为样本内估计，真实泛化更差）。

**根本原因：字符切分是坏的。** 按 x 投影的「空白列」切汉字根本无效——汉字偏旁之间
天然有空白列（荷、塘、是…），必被切碎；相邻字粘连时又被合并。切出来是半个字、
偏旁、粘连块。改用定距网格切字（自相关估步距）后，印刷书页 SELF 仍为 0.000，
与手写无法分离。**SELF 实际只测到了「墨迹模糊度」，从未测到「字形守恒」。**
干净书页字形清晰但切分错位 → SELF 反而低于模糊手写件，这就是 cn_15/en_01/en_02
（印刷书页）SELF=0.000 被判手写的原因。

**替代信号也试过，同样不可用**：RapidOCR 逐行 score（免费，因为 `_quality_ok`
只查字数，每页必跑）提取 11 个特征，约束「漏报=0」：单特征最好 65% 误报
（平均行长 / 低置信行占比），二维 AND（平均置信度 <0.817 且 低置信行占比 <0.294）
最好 45% 误报。与像素特征同水平。

结论与用法
----------
默认关闭；确认某批是手写件时用 PIC2EDIT_SKIP_DISCRIMINATIVE=1 强制走 VLM
（100% 准确、零误报、零性能损失），或用 PIC2EDIT_HW_FORCE=1 显式启用本预筛。
详见 skill 外的手写预筛盲测报告 handwriting_screen_risk_report.md。

为什么它曾经很关键、以及现在的缺口
----------------------------------
上层是「判别式优先、VLM 兜底」：RapidOCR 纯 CPU、零幻觉、快；DeepSeek-OCR 要吃 GPU。
判别式对手写几乎必出**看似成词实则全错的噪声**，而下游质量闸
（pic2edit 的 `ocr_pipeline._quality_ok`）只要求「≥5 个汉字/字母」——垃圾照样放行。
所以「初筛漏报」在本流水线里是**致命**的（VLM 永远不会被调用），不是浪费。
本模块是 skill 层唯一能控的路由闸：它决定 `skip_discriminative`，代价结构不对称：
    漏报（手写→印刷）= 输出垃圾且无兜底  → 必须压到 ~0
    误报（印刷→手写）= 多跑一次 VLM      → 可接受
但盲测证明这层闸门的分辨力不足以承担这个责任：65% 误报意味着批量印刷件
2/3 页面白跑 VLM（实测 RapidOCR 2.54s/页 vs VLM ≤10s/页；1000 页多花约 2.1 小时）。
注：早期按「VLM 20~40s/页、约 8 倍减速」估算，该数字已作废，勿再引用。
因此改为「显式指定 + 流程兜底」，而不是靠猜测路由。

算法沿革（重要：三个版本的特征选择都被实测否掉了，勿回退）
--------------------------------------------------------
v1 「笔画宽 CV + 字符高度 CV」
    实测在整页票据上完全失效：真手写的 sw_cv_comp=0.051、ch_cv=0.415 反而是全样本
    **最低**。这两个量测的是「版面异质度」——票据上的红章/logo/密集小字把印刷件的
    方差抬得比手写还高。方向性错误，调阈值救不了。

v2 「整页梯度方向轴对齐度 AXIS」
    能把真手写与真票据分开，但（a）整页聚合被噪声块污染；（b）分辨率敏感，低 DPI
    小字印刷件会掉进手写区，阈值余量仅 0.06；（c）横线纸的格线会把它顶到印刷区。

v3（本版，即被盲测否掉的一版）：剔除格线 + 块级采样 + SELF 主判据 / CC 兜底
    1. **剔除长横/长竖格线**——横线纸与表格线是最大的干扰源（实测手写件里常占
       20%~40% 的墨量）。这一层是有价值的，可复用。
    2. **块级采样**而非整页统计：以「文本行」为采样单元；行切不出时退回「滑窗」。
       这一层也是有价值的，可复用。
    3. 判据取两族：SELF（字形自相似度）+ CC（轮廓直度，加权链码）。
       **这两个判据是失效点，不可复用。**

已证伪的其他尝试：笔画宽归一化到 2.75px（LOO 0.886→0.795）；四方向游程直方图
LONG / 交叉计数 XVAR（类间区间严重重叠）。

已知局限（诚实声明）
--------------------
1. 欲达 95%+，文献中所有系统都用训练出来的分类器（Zheng 噪声文档 6 组特征 140 维
   选 31 维、Zagoris BoVW+SVM、许蔚然等 12158 张支票贝叶斯评判子 99.4%、
   Srinivasan & Srihari CRF）。起步门槛每类 ≥300~500 个标注块 + HOG/LBP + 逻辑回归
   或小 CNN（CPU 约 1 秒/页）。现有样本量（清理后 57+57 页）离这个门槛还差两个数量级，
   走不通。不过清理后的复评给出了一条更有价值的路：**「证据量门控」比「换判据」重要**——
   要求本页至少切出 5 个合格长行组（ngrp≥5）再判、否则弃权送 VLM，能让块高 CV 与
   夹角两套特征的 AUC 同时跳到 0.92~0.96（留出集复现），融合后生产口径拦下 99% 手写页。
   详见 skill 外报告 handwriting_reevaluation.md。
2. 印刷表格 + 少量手写填空的混排页：只要手写占比不高就判印刷。本模块回答的是
   「整页以哪种为主」，不是「页内是否含手写」。
3. 拍照严重倾斜、强阴影、极低分辨率（字高 <8px）时字形块切分不可靠，会退回 CC 判据。
4. 不处理 PDF 多页影像（上层已跳过）；不判断「是否为文档」（风景照也可能被判手写）。

环境变量
--------
  PIC2EDIT_HW_DISABLE=1     完全跳过预筛，一律按印刷（逃生口）
  PIC2EDIT_HW_FORCE=1       强制启用预筛（默认关闭，见上）
  PIC2EDIT_HW_SELF_T        SELF 阈值（默认 0.20）
  PIC2EDIT_HW_CC_T          CC 兜底阈值（默认 0.46）
  PIC2EDIT_HW_DEBUG=1       打印中间量，便于拿新数据复标

依赖：numpy、opencv-python。无任何模型权重，MIT 发布无许可负担。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

try:
    import cv2
except Exception:  # 环境缺 cv2 → 保守按印刷
    cv2 = None


# ---- 判定阈值（标定集见模块 docstring）----
_T_SELF = float(os.environ.get("PIC2EDIT_HW_SELF_T", "0.20"))
_T_CC = float(os.environ.get("PIC2EDIT_HW_CC_T", "0.46"))

# ---- 采样与归一化参数 ----
_MAX_SIDE = 1200        # 统一短边；过大既慢又受 JPEG 块效应影响
_GLYPH_PX = 20          # 字形归一化边长
_MAX_LINES = 24         # 文本行采样上限（中位数足够稳健，无需全采）
_MAX_WINDOWS = 24       # 滑窗采样上限
_MIN_GLYPHS = 12        # 少于该数认为 SELF 不可用
_DUPE_DIST = 0.12       # 归一化汉明距离阈值以内视为「近似重复字形」


# ---------------------------------------------------------------- 预处理
def _decode(image):
    """路径 / ndarray → 灰度图；不可解码（PDF、多页 TIF）返回 None。"""
    if isinstance(image, str):
        if not os.path.isfile(image):
            return None
        raw = np.fromfile(image, dtype=np.uint8)   # 兼容中文路径
        bgr = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    elif isinstance(image, np.ndarray):
        bgr = image
    else:
        return None
    if bgr is None or bgr.size == 0:
        return None
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr


def _binarize(grey: np.ndarray) -> np.ndarray:
    block = max(15, (min(grey.shape) // 30) | 1)
    return cv2.adaptiveThreshold(
        grey, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, block, 10)


def _strip_rules(ink: np.ndarray) -> np.ndarray:
    """剔除长横/长竖线（横线纸格线、表格框）。

    这是横线纸场景能跑通的前提：格线是又长又直的水平线，会同时污染
    「字形块切分」和「笔画方向」两类判据。用长核开运算只留长直线再减掉。
    """
    h, w = ink.shape
    hori = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(24, int(w * 0.06)), 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(24, int(h * 0.06)))))
    return cv2.subtract(ink, cv2.bitwise_or(hori, vert))


def _char_h(ink: np.ndarray) -> int:
    """字符高度中位数（连通域高度，剔除极端值）。"""
    n, _l, st, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)
    H = ink.shape[0]
    hs = [int(st[i, cv2.CC_STAT_HEIGHT]) for i in range(1, n)
          if 4 <= int(st[i, cv2.CC_STAT_HEIGHT]) < 0.15 * H
          and int(st[i, cv2.CC_STAT_AREA]) >= 10]
    return int(np.median(hs)) if len(hs) >= 8 else 0


# ---------------------------------------------------------------- 采样
def _sample_lines(ink: np.ndarray, ch: int):
    """水平膨胀把字符合并成文本行，返回行框列表。"""
    if ch <= 0:
        return []
    dil = cv2.dilate(ink, cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(3, ch), 1)), iterations=1)
    n, _l, st, _c = cv2.connectedComponentsWithStats(dil, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, bw, bh = (int(st[i, cv2.CC_STAT_LEFT]), int(st[i, cv2.CC_STAT_TOP]),
                        int(st[i, cv2.CC_STAT_WIDTH]), int(st[i, cv2.CC_STAT_HEIGHT]))
        if bh < 0.45 * ch or bh > 3.5 * ch:
            continue
        if bw < 3.0 * ch or bw / max(1, bh) > 30:
            continue
        crop = ink[y:y + bh, x:x + bw]
        ink_n = int((crop > 0).sum())
        if ink_n < 4 * ch:
            continue
        if not (0.03 <= ink_n / float(bw * bh) <= 0.60):
            continue
        out.append((x, y, bw, bh))
    if len(out) > _MAX_LINES:
        idx = np.linspace(0, len(out) - 1, _MAX_LINES).astype(int)
        out = [out[i] for i in idx]
    return out


def _sample_windows(ink: np.ndarray, ch: int):
    """滑窗采样：不依赖切行，对密排/行间粘连的手写更鲁棒。"""
    if ch <= 0:
        return []
    H, W = ink.shape[:2]
    ww, wh = int(8 * ch), int(3 * ch)
    sx, sy = max(2, ww // 2), max(2, wh // 2)
    out = []
    for y in range(0, max(1, H - wh + 1), sy):
        for x in range(0, max(1, W - ww + 1), sx):
            crop = ink[y:y + wh, x:x + ww]
            if crop.shape[0] < wh or crop.shape[1] < ww:
                continue
            d = int((crop > 0).sum()) / float(ww * wh)
            if 0.03 <= d <= 0.55:
                out.append((x, y, ww, wh))
    if len(out) > _MAX_WINDOWS:
        idx = np.linspace(0, len(out) - 1, _MAX_WINDOWS).astype(int)
        out = [out[i] for i in idx]
    return out


# ---------------------------------------------------------------- 判据
def _glyphs(ink: np.ndarray, ch: int):
    """行内按 x 投影切字形块，归一化到固定尺寸后返回二值向量。"""
    prof = (ink > 0).sum(axis=0)
    on = prof > 0
    idx = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(np.int8), [0]))))
    starts, ends = idx[0::2], idx[1::2]
    H = ink.shape[0]
    out = []
    for s, e in zip(starts, ends):
        w = e - s
        if w < 0.3 * ch or w > 1.8 * ch:
            continue
        sub = ink[:, s:e]
        rows = np.flatnonzero((sub > 0).sum(axis=1) > 0)
        if rows.size < 3:
            continue
        y0, y1 = int(rows[0]), int(rows[-1]) + 1
        if (y1 - y0) < 0.35 * ch or (y1 - y0) > 1.4 * ch or (y1 - y0) > 0.9 * H:
            continue
        g = cv2.resize(sub[y0:y1].astype(np.float32), (_GLYPH_PX, _GLYPH_PX),
                       interpolation=cv2.INTER_AREA)
        out.append((g > 0.45 * max(1.0, float(g.max()))).astype(np.float32).ravel())
    return out


def _self_similarity(glyphs) -> float:
    """字符自相似度：存在「近似重复字形」的字符占比。

    印刷体同一字每次渲染像素级一致 → 近邻距离≈0 → 占比高（→ 印刷）；
    手写体同一字两次写法不同 → 近邻距离大 → 占比低（→ 手写）。
    字形块不足时返回 NaN，由调用方退回 CC 判据。
    """
    if len(glyphs) < _MIN_GLYPHS:
        return float("nan")
    G = np.array(glyphs, dtype=np.float32)
    norms = G.sum(axis=1)
    dist = norms[:, None] + norms[None, :] - 2.0 * (G @ G.T)
    denom = norms[:, None] + norms[None, :]
    dist = np.where(denom > 0, dist / np.maximum(denom, 1e-6), 1.0)
    np.fill_diagonal(dist, 9.9)
    return float((dist.min(axis=1) <= _DUPE_DIST).mean())


def _cc_straight(ink: np.ndarray, ch: int) -> float:
    """加权链码：轮廓点中「连续同向游程 ≥ 0.25×字高」的占比。

    印刷体笔画平直 → 轮廓是一长串同向链码；手写体轮廓被局部形变不断打断。
    """
    cnts, _ = cv2.findContours(ink, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return float("nan")
    thr = max(2.0, 0.25 * ch)
    good = bad = 0
    for c in cnts:
        p = c.reshape(-1, 2).astype(np.int32)
        if len(p) < 12:
            continue
        step = np.clip(p[1:] - p[:-1], -1, 1)
        q = (np.round(np.arctan2(step[:, 1], step[:, 0]) / (np.pi / 4))
             .astype(np.int16) + 8) % 8
        bounds = np.concatenate(([0], np.flatnonzero(np.diff(q) != 0) + 1,
                                 [len(q)]))
        runs = np.diff(bounds)
        good += int(runs[runs >= thr].sum())
        bad += int(runs[runs < thr].sum())
    tot = good + bad
    return good / tot if tot else float("nan")


def _median_cc(ink: np.ndarray, boxes, ch: int) -> float:
    vals = []
    for (x, y, w, h) in boxes:
        v = _cc_straight(ink[y:y + h, x:x + w], ch)
        if v == v:
            vals.append(v)
    return float(np.median(vals)) if vals else float("nan")


# ---------------------------------------------------------------- 主入口
def detect_handwriting(image):
    """判断图像（路径或数组）主体是否为手写体。

    Args:
        image: 图像文件路径（str）或灰度/BGR 的 numpy 数组。

    Returns:
        (is_handwritten, confidence)。confidence 为**对本次判定的置信度**
        （0.5≈恰在阈值上），不是「手写的概率」。

    Raises:
        不抛异常。任何失败（缺 cv2 / 无法解码 / 无文字）返回 (False, 0.0)，
        即保守按印刷处理。
    """
    if os.environ.get("PIC2EDIT_HW_DISABLE", "").lower() in ("1", "true", "yes"):
        return False, 0.0
    if cv2 is None:
        return False, 0.0

    grey = _decode(image)
    if grey is None:
        return False, 0.0
    h, w = grey.shape[:2]
    if h < 32 or w < 32:
        return False, 0.0
    s = _MAX_SIDE / max(h, w)
    if s < 1.0:
        grey = cv2.resize(grey, (int(w * s), int(h * s)),
                          interpolation=cv2.INTER_AREA)

    ink = _binarize(grey)
    if float((ink > 0).sum()) / ink.size < 0.002:   # 近乎空白页
        return False, 0.0
    text = _strip_rules(ink)
    ch = _char_h(text)
    if ch <= 0:
        return False, 0.0

    # ---- 主判据：字符自相似度（沿文本行采样）----
    glyphs = []
    for (x, y, bw, bh) in _sample_lines(text, ch):
        glyphs.extend(_glyphs(text[y:y + bh, x:x + bw], ch))
    if len(glyphs) > 400:                     # 控制 O(n²) 规模
        idx = np.linspace(0, len(glyphs) - 1, 400).astype(int)
        glyphs = [glyphs[i] for i in idx]
    self_sim = _self_similarity(glyphs)

    if self_sim == self_sim:                  # SELF 可用
        is_hw = self_sim < _T_SELF
        delta = (_T_SELF - self_sim) if is_hw else (self_sim - _T_SELF)
        conf = 1.0 / (1.0 + math.exp(-25.0 * delta))
        if os.environ.get("PIC2EDIT_HW_DEBUG"):
            sys.stderr.write(
                f"[hw-debug] SELF={self_sim:.3f} glyphs={len(glyphs)} "
                f"T={_T_SELF:.2f} ch={ch} -> {'手写' if is_hw else '印刷'}\n")
        return is_hw, round(float(conf), 3)

    # ---- 兜底判据：字形块不足时用轮廓直度（沿滑窗采样）----
    cc = _median_cc(text, _sample_windows(text, ch), ch)
    if cc != cc:
        if os.environ.get("PIC2EDIT_HW_DEBUG"):
            sys.stderr.write("[hw-debug] SELF/CC 均不可用 → 按印刷\n")
        return False, 0.0
    is_hw = cc < _T_CC
    delta = (_T_CC - cc) if is_hw else (cc - _T_CC)
    conf = 1.0 / (1.0 + math.exp(-25.0 * delta))
    if os.environ.get("PIC2EDIT_HW_DEBUG"):
        sys.stderr.write(
            f"[hw-debug] SELF=NaN glyphs={len(glyphs)} → 兜底 CC={cc:.3f} "
            f"T={_T_CC:.2f} ch={ch} -> {'手写' if is_hw else '印刷'}\n")
    return is_hw, round(float(conf), 3)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: handwriting_screen.py <image>")
        raise SystemExit(1)
    flag, conf = detect_handwriting(sys.argv[1])
    print(f"handwritten={flag} confidence={conf}")
