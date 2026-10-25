"""导出：把 Markdown 结果保存为 .md / .txt，以及轻量转换为 .docx。

Markdown -> docx 转换器覆盖常见元素：
- #/##/### 标题
- - / * 无序列表
- 1. 有序列表
- | a | b | 表格
- **加粗** 行内格式
"""
import os
import re

from docx import Document
from docx.shared import Pt


def save_text(text: str, out_path: str):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


def _add_runs(paragraph, line: str):
    """处理 **加粗** 行内格式，逐段写入。"""
    parts = re.split(r"(\*\*.*?\*\*)", line)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        else:
            paragraph.add_run(part)


def md_to_docx(text: str, out_path: str):
    doc = Document()
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue

        # 表格：连续以 | 开头/分隔的行
        if line.strip().startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            # 去掉分隔行（---）
            rows = [r for r in table_lines if not re.fullmatch(r"\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|", r)]
            if rows:
                cells = [c.strip() for c in rows[0].strip("|").split("|")]
                ncol = len(cells)
                table = doc.add_table(rows=1, cols=ncol)
                table.style = "Table Grid"
                hdr = table.rows[0].cells
                for c, val in enumerate(cells):
                    hdr[c].text = val
                for row in rows[1:]:
                    vals = [c.strip() for c in row.strip("|").split("|")]
                    vals += [""] * (ncol - len(vals))
                    cells_row = table.add_row().cells
                    for c, val in enumerate(vals[:ncol]):
                        cells_row[c].text = val
                doc.add_paragraph()
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            p = doc.add_heading(level=min(level, 6))
            _add_runs(p, m.group(2))
            i += 1
            continue

        # 无序列表
        m = re.match(r"^[-*]\s+(.*)$", line)
        if m:
            p = doc.add_paragraph(style="List Bullet")
            _add_runs(p, m.group(1))
            i += 1
            continue

        # 有序列表
        m = re.match(r"^\d+\.\s+(.*)$", line)
        if m:
            p = doc.add_paragraph(style="List Number")
            _add_runs(p, m.group(1))
            i += 1
            continue

        # 普通段落
        p = doc.add_paragraph()
        _add_runs(p, line)
        i += 1

    doc.save(out_path)
