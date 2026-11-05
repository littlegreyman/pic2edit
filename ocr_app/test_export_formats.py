"""导出格式单测（headless，无 Qt）：覆盖 5 种交付类型的内容与后缀解析。"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from utils.export_formats import (
    ext_from_filter, export_markdown, export_text, export_docx,
    export_html, export_json, export_by_ext,
)


class FakeItem:
    """最小 InputItem 替身：仅需 name / result 两个字段。"""
    def __init__(self, name, result):
        self.name = name
        self.result = result


def make_pages():
    return [
        FakeItem("doc.pdf - 第1页", "# 标题一\n\n正文第一行\n\n| 列A | 列B |\n| --- | --- |\n| 1 | 2 |"),
        FakeItem("doc.pdf - 第2页", "## 小标题\n\n- 项一\n- 项二\n"),
        FakeItem("doc.pdf - 第3页", ""),  # 空白页
    ]


def test_ext_from_filter():
    assert ext_from_filter("Word 文档 (*.docx)") == "docx"
    assert ext_from_filter("Markdown (*.md)") == "md"
    assert ext_from_filter("文本 (*.txt)") == "txt"
    assert ext_from_filter("HTML (*.html)") == "html"
    assert ext_from_filter("JSON (*.json)") == "json"
    assert ext_from_filter("") == ""
    assert ext_from_filter("无扩展名过滤器") == ""
    print("PASS - ext_from_filter")


def test_export_markdown_and_text():
    pages = make_pages()
    with tempfile.TemporaryDirectory() as d:
        p_md = os.path.join(d, "o.md")
        p_txt = os.path.join(d, "o.txt")
        export_markdown(pages, p_md)
        export_text(pages, p_txt)
        md = open(p_md, encoding="utf-8").read()
        txt = open(p_txt, encoding="utf-8").read()
        assert "标题一" in md and "项一" in md
        assert "第 1 页" in md and "第 3 页（无内容）" in md
        # md 与 txt 同源（均来自 assemble_document）
        assert md == txt
    print("PASS - export_markdown / export_text")


def test_export_docx():
    pages = make_pages()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "o.docx")
        export_docx(pages, p)
        assert os.path.isfile(p) and p.endswith(".docx")
        # 用 python-docx 回读验证确实是有效的 docx 且含页面内容
        from docx import Document
        doc = Document(p)
        full = "\n".join(par.text for par in doc.paragraphs)
        assert "标题一" in full and "项一" in full
    print("PASS - export_docx")


def test_export_html():
    pages = make_pages()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "o.html")
        export_html(pages, p)
        html = open(p, encoding="utf-8").read()
        assert html.startswith("<!DOCTYPE html>")
        assert "<table>" in html, "表格应被还原为 HTML 表格"
        assert "<h1" in html, "标题应被还原为 h1"
        assert "<ul>" in html, "列表应被还原为 ul"
        assert "第 1 页" in html and "第 2 页" in html
    print("PASS - export_html")


def test_export_json():
    pages = make_pages()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "o.json")
        export_json(pages, "/tmp/doc.pdf", p)
        data = json.load(open(p, encoding="utf-8"))
        assert data["format"] == "json"
        assert data["source"] == "/tmp/doc.pdf"
        assert data["page_count"] == 3
        assert data["pages"][0]["index"] == 1
        assert data["pages"][0]["name"] == "doc.pdf - 第1页"
        assert "标题一" in data["pages"][0]["text"]
        # JSON 存的是每页真实文本：空白页为 ""（占位符是 md 装配产物，不进结构化 JSON）
        assert data["pages"][2]["text"] == ""
    print("PASS - export_json")


def test_export_by_ext_dispatch():
    pages = make_pages()
    with tempfile.TemporaryDirectory() as d:
        for ext in ("md", "txt", "docx", "html", "json"):
            p = os.path.join(d, f"o.{ext}")
            export_by_ext(pages, p, ext, source="x.pdf")
            assert os.path.isfile(p), f"{ext} 应产出文件"
        # 未知扩展名回退到 markdown
        p = os.path.join(d, "o.unknown")
        export_by_ext(pages, p, "unknown")
        assert open(p, encoding="utf-8").read().startswith("<!-- 第 1 页 -->")
    print("PASS - export_by_ext 分派（含未知类型回退 md）")


if __name__ == "__main__":
    test_ext_from_filter()
    test_export_markdown_and_text()
    test_export_docx()
    test_export_html()
    test_export_json()
    test_export_by_ext_dispatch()
    print("ALL PASS: test_export_formats")
