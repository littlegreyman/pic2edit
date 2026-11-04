"""skill 入口的 md/docx 表格渲染回归测试。

背景：GUI 的表格能力来自 ``utils/table_layout.build_table_from_lines``（gutter
优先切两栏 + `| --- |` 分隔行）。skill 此前只有 ``html`` 走坐标重建，md/txt/docx
直接吐原始 OCR 文本，导致同一份两栏对照 PDF「GUI 出表、skill 不出表」。

本测试锁定修复后的行为：
  1. 两栏版式 + 坐标 → skill 的 md 输出必须是**合法** Markdown 表（含分隔行）；
  2. 单栏正文 + 坐标 → 不得误判成表，必须保留识别原文；
  3. docx 走同一条 md 通道 → 必须落成真实 Word 表格。

skill 脚本不在仓库内（项目级/用户级两份副本），找不到时跳过，避免 CI 假失败。
"""
import importlib.util
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.join(_HERE, "..", ".workbuddy", "skills", "pic2edit-ocr", "scripts",
                 "pic2edit_convert.py"),
    os.path.expanduser("~/.workbuddy/skills/pic2edit-ocr/scripts/pic2edit_convert.py"),
]


def _load_skill():
    for p in _CANDIDATES:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("_p2e_skill_under_test", p)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            return mod
    pytest.skip("未找到 pic2edit skill 脚本，跳过 skill 渲染测试")


@pytest.fixture(scope="module")
def skill():
    return _load_skill()


def _two_col_lines(n=8, img_w=1000, img_h=1400):
    """模拟 RapidOCR 的**行级**框：左右两栏各 n 行，中间留出 gutter。"""
    lines = []
    for i in range(n):
        y = 100 + i * 30
        lines.append({"text": f"左{i}", "x0": 60, "x1": 460, "y0": y, "y1": y + 18})
        lines.append({"text": f"右{i}", "x0": 540, "x1": 940, "y0": y, "y1": y + 18})
    return lines, img_w, img_h


def _prose_lines(n=8, img_w=1000, img_h=1400):
    """单栏正文：每行一个长条框，跨过页面中部 → 不应被判为表格。"""
    return ([{"text": f"这是第{i}行普通正文内容，用来验证不会被误判成表格",
              "x0": 60, "x1": 900, "y0": 100 + i * 30, "y1": 118 + i * 30}
             for i in range(n)], img_w, img_h)


def test_md_renders_two_column_table(skill, tmp_path):
    lines, w, h = _two_col_lines()
    data = {"pages": [{"page": 1, "raw_text": "原始 OCR 文本", "lines": lines,
                       "img_w": w, "img_h": h}]}
    out = skill._render(data, "md", str(tmp_path / "t.md"), "in.pdf", False, "markdown")
    text = open(out, encoding="utf-8").read()

    assert "| --- | --- |" in text, "md 表格必须有表头分隔行，否则渲染器不成表"
    assert "| 左0 | 右0 |" in text
    assert text.count("\n") >= 8, "应输出多行数据，而不是退回单段原文"


def test_md_keeps_prose_as_text(skill, tmp_path):
    lines, w, h = _prose_lines()
    data = {"pages": [{"page": 1, "raw_text": "这是正文第一行\n第二行",
                       "lines": lines, "img_w": w, "img_h": h}]}
    out = skill._render(data, "md", str(tmp_path / "t.md"), "in.pdf", False, "markdown")
    text = open(out, encoding="utf-8").read()

    assert "| --- |" not in text, "单栏正文不得被误塞进表格"
    assert "这是正文第一行" in text, "非表格页必须回退为识别原文"


def test_docx_contains_real_word_table(skill, tmp_path):
    docx = pytest.importorskip("docx")
    lines, w, h = _two_col_lines()
    data = {"pages": [{"page": 1, "raw_text": "原始 OCR 文本", "lines": lines,
                       "img_w": w, "img_h": h}]}
    out = skill._render(data, "docx", str(tmp_path / "t.docx"), "in.pdf", False, "markdown")
    doc = docx.Document(out)

    assert len(doc.tables) == 1, "md 表格应被 md_to_docx 转成真实 Word 表格"
    tb = doc.tables[0]
    assert len(tb.columns) == 2
    assert tb.rows[0].cells[0].text.strip() == "左0"
