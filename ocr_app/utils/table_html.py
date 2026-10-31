"""解析 HTML <table>（含 rowspan / colspan）为规整网格，供 Word 合并单元格渲染。

用于发票等“合并单元格”复杂表格：VLM（DeepSeek-OCR）在合适的提示词下可输出 HTML 表格，
本模块把 HTML 解析成「逻辑网格」，再由 export_docx 用 python-docx 的 cell.merge()
还原合并单元格。**不依赖 pix2text 等重依赖**，纯 HTML 解析。
"""
import re
from html.parser import HTMLParser
from typing import List, Dict, Tuple, Any


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[Dict[str, Any]]]] = []
        self._cur_table = None
        self._cur_row = None
        self._in_cell = False
        self._buf: List[str] = []
        self._attrs: Dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._cur_table = []
        elif tag == "tr":
            self._cur_row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._buf = []
            self._attrs = a

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_cell = False
            text = "".join(self._buf).strip()
            text = re.sub(r"\s+", " ", text)
            cell = {
                "text": text,
                "rowspan": int(self._attrs.get("rowspan", 1) or 1),
                "colspan": int(self._attrs.get("colspan", 1) or 1),
            }
            if self._cur_row is not None:
                self._cur_row.append(cell)
        elif tag == "tr":
            if self._cur_table is not None and self._cur_row is not None:
                self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag == "table":
            if self._cur_table is not None:
                self.tables.append(self._cur_table)
            self._cur_table = None

    def handle_data(self, data):
        if self._in_cell:
            self._buf.append(data)


def parse_html_tables(html: str) -> List[List[List[Dict[str, Any]]]]:
    """解析 HTML 文本中所有 <table>。

    返回 list[table]，每个 table = list[row]，每行 = list[cell]，
    cell = {"text","rowspan","colspan"}。
    """
    p = _TableParser()
    p.feed(html or "")
    return p.tables


def layout_table(raw_rows: List[List[Dict[str, Any]]]) -> Tuple[List[List[int]], int, int, List[Tuple[int, int, int, int, str]]]:
    """把原始行（含跨行/跨列）布局为规整网格。

    返回 (grid, nrows, ncols, anchors)：
    - grid[r][c] = anchor 索引（>=0）或 -1（被更早的跨行/列单元格覆盖）；
    - anchors: list[(r, c, rowspan, colspan, text)]，每个跨行/列单元格的锚点。
    渲染时对每个 anchor 调用 cell(r,c).merge(cell(r+rs-1, c+cs-1))。
    """
    anchors: List[Tuple[int, int, int, int, str]] = []
    grid: List[List[int]] = []

    def occupied(r, c):
        if r >= len(grid):
            return False
        row = grid[r]
        if c >= len(row):
            return False
        return row[c] != -1

    for r, row in enumerate(raw_rows):
        # 确保第 r 行存在（前一行 rowspan 可能已创建它，不能重复 append 否则错位）
        while len(grid) <= r:
            grid.append([])
        c = 0
        for cell in row:
            rs = max(1, int(cell.get("rowspan", 1) or 1))
            cs = max(1, int(cell.get("colspan", 1) or 1))
            while occupied(r, c):
                c += 1
            idx = len(anchors)
            anchors.append((r, c, rs, cs, cell.get("text", "")))
            for dr in range(rs):
                for dc in range(cs):
                    rr, cc = r + dr, c + dc
                    while len(grid) <= rr:
                        grid.append([])
                    while len(grid[rr]) <= cc:
                        grid[rr].append(-1)
                    grid[rr][cc] = idx
            c += cs

    nrows = len(grid)
    ncols = max((len(row) for row in grid), default=0)
    for row in grid:
        while len(row) < ncols:
            row.append(-1)
    return grid, nrows, ncols, anchors
