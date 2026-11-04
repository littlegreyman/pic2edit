"""从带坐标的 OCR 行框重建 Markdown 表格（两栏/多栏对照、条目表）。

背景：GUI 共享流水线（ocr_pipeline）只保留 Markdown 文本、丢弃坐标，导致
“表格（键值→表）”只能处理“键：值”标签；对“两栏对照表”（如法律修正前后对照）
无能为力——真实表格必须靠坐标把**同一视觉行的左右单元格**分开。

做法（两级策略）：
1. **gutter 优先**：在页面中部（30%~70% 宽度）找最宽的“无文字竖向间隙”，
   若存在，则按该间隙把每行切成左右两栏。这正对“两栏对照表”（法律修正前后、
   中英对照等），列切分稳定，不受文字长短影响。
2. **端点聚类回退**：无明确 gutter 时，按文本块 x0/x1 端点聚类成列。

仅当页面呈现“多数行含 >=2 个单元格”的表格版式时才输出 Markdown 表格；
否则返回空串（调用方回退为普通文本），避免把单栏正文/双栏文章误判为表格。
"""
import re

_WS = re.compile(r"\s+")
_PAGE_NO = re.compile(r"^[\s—\-–·]*\d{1,4}[\s—\-–·]*$")


def _cell(s):
    """Markdown 单元格清洗：压空白、竖线换成全角丨（避免破坏表格列）。"""
    return _WS.sub(" ", (s or "").replace("|", "丨")).strip()


def _clean_lines(lines, W, H):
    """剔除空行、页眉页脚（<3% 或 >97% 高度）与纯页码行。"""
    out = []
    for ln in lines or []:
        t = (ln.get("text") or "").strip()
        if not t:
            continue
        try:
            y0 = float(ln.get("y0", 0))
            y1 = float(ln.get("y1", 0))
            x0 = float(ln.get("x0", 0))
            x1 = float(ln.get("x1", 0))
        except (TypeError, ValueError):
            continue
        cy = (y0 + y1) / 2.0
        if H and (cy < 0.03 * H or cy > 0.97 * H):
            continue
        if _PAGE_NO.match(t):
            continue
        out.append({"text": t, "x0": x0, "x1": x1, "y0": y0, "y1": y1})
    return out


def _cluster_rows(cells, H, tol_frac=0.02):
    """按 y 中心把文本块聚成视觉行（相邻中心差 <= 阈值归并）。"""
    if not cells:
        return []
    tol = max(6.0, tol_frac * (H or 1000))
    ordered = sorted(cells, key=lambda c: (c["y0"] + c["y1"]) / 2.0)
    rows = []
    for c in ordered:
        cy = (c["y0"] + c["y1"]) / 2.0
        if rows and abs(cy - rows[-1]["cy"]) <= tol:
            rows[-1]["cells"].append(c)
        else:
            rows.append({"cy": cy, "cells": [c]})
    return rows


def _find_gutter(cells, W):
    """在页面中部找最宽的无文字竖向间隙（像素坐标）；找不到返回 None。

    只用“窄块”统计覆盖：跨栏的标题/表头等宽块会把中部完全填满，若计入则
    永远找不到两栏之间的 gutter。OCR 行级框下，标题通常是一个宽块，排除后再
    统计即可稳定在页面中部找到两栏间隙。
    """
    if not cells or not W:
        return None
    narrow = [c for c in cells if (c["x1"] - c["x0"]) < 0.35 * W] or cells
    bins = max(200, int(W))          # ~1px 分辨率
    cov = [False] * bins
    for c in narrow:
        a = int(max(0.0, c["x0"]) / W * bins)
        b = int(min(W, c["x1"]) / W * bins)
        for k in range(max(0, a), min(bins - 1, b) + 1):
            cov[k] = True
    lo, hi = int(0.30 * bins), int(0.70 * bins)
    best_w, best_c, run = 0, None, None
    for k in range(lo, hi + 1):
        if not cov[k] and run is None:
            run = k
        elif cov[k] and run is not None:
            if k - run > best_w:
                best_w, best_c = k - run, (run + k) / 2.0
            run = None
    if run is not None and hi - run > best_w:
        best_w, best_c = hi - run, (run + hi) / 2.0
    if best_c is None:
        return None
    if best_w < max(3, int(0.006 * bins)):   # 间隙太窄，视为无 gutter
        return None
    return best_c / bins * W


def _x_slots(cells, W, gap_frac=0.02):
    """把文本块的 x0/x1 端点聚成列槽（间隔 <= 阈值视为同一列）。"""
    xs = sorted({round(v, 1) for c in cells for v in (c["x0"], c["x1"])})
    if not xs:
        return []
    gap = max(4.0, gap_frac * (W or 1000))
    slots, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] <= gap:
            cur.append(x)
        else:
            slots.append((cur[0], cur[-1]))
            cur = [x]
    slots.append((cur[0], cur[-1]))
    return slots


def _slot_of(px, slots):
    for i, (a, b) in enumerate(slots):
        if a - 2 <= px <= b + 2:
            return i
    return min(range(len(slots)), key=lambda i: abs((slots[i][0] + slots[i][1]) / 2 - px))


def _grid_by_gutter(rows, gutter):
    """按 gutter 把每行切成左右两栏。"""
    grid = []
    for row in rows:
        left, right = [], []
        for c in row["cells"]:
            cx = (c["x0"] + c["x1"]) / 2.0
            (left if cx < gutter else right).append(c)
        grid.append([
            " ".join(c["text"] for c in sorted(left, key=lambda c: c["x0"])),
            " ".join(c["text"] for c in sorted(right, key=lambda c: c["x0"])),
        ])
    return grid, 2


def _grid_by_slots(rows, cells, W):
    """无 gutter 时按端点聚类成列。"""
    slots = _x_slots(cells, W)
    if not slots:
        return [], 0
    grid = [["" for _ in range(len(slots))] for _ in rows]
    for ri, row in enumerate(rows):
        for c in sorted(row["cells"], key=lambda c: c["x0"]):
            ci = _slot_of(c["x0"], slots)
            grid[ri][ci] = ((grid[ri][ci] + " " + c["text"]).strip()
                            if grid[ri][ci] else c["text"])
    return grid, len(slots)


def build_table_from_lines(lines, W, H, min_cols=2, max_cols=6,
                           min_two_col_ratio=0.5, min_rows=3):
    """把带坐标行框重建为 Markdown 表格；不满足表格特征时返回 ``""``。

    判定条件：
    - 视觉行数 >= ``min_rows``；
    - 列数在 ``[min_cols, max_cols]`` 内（去掉全空列后）；
    - 至少 ``min_two_col_ratio`` 比例的视觉行含 >=2 个非空单元格
      （两栏对照/条目表的典型特征；单栏正文每行仅 1 格，会被排除）。
    """
    cells = _clean_lines(lines, W, H)
    if len(cells) < 2:
        return ""
    rows = _cluster_rows(cells, H)
    if len(rows) < min_rows:
        return ""

    gutter = _find_gutter(cells, W)
    if gutter is not None:
        grid, ncols = _grid_by_gutter(rows, gutter)
    else:
        grid, ncols = _grid_by_slots(rows, cells, W)
    if ncols < min_cols:
        return ""
    # 去掉全空列
    keep = [ci for ci in range(ncols) if any(r[ci] for r in grid)]
    if len(keep) < min_cols or len(keep) > max_cols:
        return ""
    grid = [[r[ci] for ci in keep] for r in grid]
    ncols = len(keep)
    # 去掉全空行
    grid = [r for r in grid if any(r)]
    if len(grid) < 2:
        return ""
    multi = sum(1 for r in grid if sum(1 for v in r if v) >= 2)
    if multi / len(grid) < min_two_col_ratio:
        return ""

    header, body = grid[0], grid[1:]
    sep = "| " + " | ".join("---" for _ in range(ncols)) + " |"

    def _line(r):
        return "| " + " | ".join(_cell(v) for v in r) + " |"

    out = [_line(header), sep]
    out.extend(_line(r) for r in body)
    return "\n".join(out)
