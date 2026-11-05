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
        # 只写文件名：完整路径会把目录结构与用户名一起外泄（票据是最敏感的一类）
        assert data["source"] == "doc.pdf"
        assert "/tmp" not in json.dumps(data, ensure_ascii=False), "产物里不得出现本机路径"
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


class RichItem(FakeItem):
    """带坐标/原图/schema 的替身：用于 html 版式与 schema 溯源测试。"""

    def __init__(self, name, result, *, lines=None, img_w=0, img_h=0,
                 image_path="", schema=None, schema_id=None, schema_version=None):
        super().__init__(name, result)
        self.lines = lines or []
        self.img_w = img_w
        self.img_h = img_h
        self.image_path = image_path
        self.schema = schema
        self.schema_id = schema_id
        self.schema_version = schema_version


def test_export_html_uses_image_overlay_when_no_schema(tmp_path=None):
    """案例②：只给数据格式时「复现尽量按来源版式」——GUI 侧必须与原图垫底同源。

    修复前 GUI 一律走 markdown 重排，而 skill 走垫底，于是同一份文件两个入口
    导出的 html 长得不一样（Phase 3 门禁「两侧同质量」未达成）。
    """
    import pytest

    Image = pytest.importorskip("PIL.Image")
    d = tempfile.mkdtemp()
    img = os.path.join(d, "p.png")
    Image.new("RGB", (200, 120), (255, 255, 255)).save(img)

    it = RichItem("p.png", "某行文字", lines=[
        {"text": "某行文字", "x0": 10, "y0": 20, "x1": 100, "y1": 40}],
        img_w=200, img_h=120, image_path=img)
    out = os.path.join(d, "o.html")
    export_html([it], out)
    html = open(out, encoding="utf-8").read()

    assert "data:image/png;base64," in html, "无 schema 时应把原图垫底内嵌"
    assert 'class="fx-stage"' in html
    assert "left:10.0px" in html and "top:20.0px" in html, "坐标必须原样落到文字层"


def test_export_html_sanitizes_untrusted_html():
    """识别文本里的 <script>/远程 <img> 不得进入交付物（HTML 注入链）。"""
    payload = ('正常文字<script>alert(1)</script>'
               '<img src="http://evil.example/x.png">'
               '<iframe src="http://evil.example/f"></iframe>'
               '<a href="javascript:alert(2)">点我</a>')
    it = FakeItem("x.png", payload)
    d = tempfile.mkdtemp()
    out = os.path.join(d, "s.html")
    export_html([it], out)
    html = open(out, encoding="utf-8").read()

    assert "<script" not in html and "alert(1)" not in html
    assert "<iframe" not in html
    assert "evil.example" not in html, "远程资源必须被剥掉，否则 GUI 预览会外连"
    assert "javascript:" not in html
    assert "正常文字" in html, "清洗只能去标签，不能把文字也删掉"
    assert "点我" in html


def test_export_json_records_schema_version():
    """产物必须记录 schema 版本：schema 升版后要能解释两次导出为何不同。"""
    it = RichItem("inv.pdf - 第1页", "发票正文", schema={"amount": "1.00"},
                  schema_id="invoice", schema_version="1.2.0")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "v.json")
    export_json([it], "/tmp/客户/发票.pdf", p)
    data = json.load(open(p, encoding="utf-8"))

    assert data["pages"][0]["schema_id"] == "invoice"
    assert data["pages"][0]["schema_version"] == "1.2.0"
    assert data["schema"] == {"invoice": "1.2.0"}, "顶层要有 id→version 汇总"


def test_export_markdown_stamps_schema_version():
    it = RichItem("inv.pdf - 第1页", "发票正文", schema={"amount": "1.00"},
                  schema_id="invoice", schema_version="1.2.0")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.md")
    export_markdown([it], p)
    text = open(p, encoding="utf-8").read()

    assert text.startswith("<!-- schema: invoice@1.2.0 -->")


def test_export_html_meta_stamp():
    it = RichItem("inv.pdf - 第1页", "发票正文", schema={"amount": "1.00"},
                  schema_id="invoice", schema_version="1.2.0")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.html")
    export_html([it], p)
    html = open(p, encoding="utf-8").read()
    assert '<meta name="pic2edit-schema" content="invoice@1.2.0">' in html


if __name__ == "__main__":
    test_ext_from_filter()
    test_export_markdown_and_text()
    test_export_docx()
    test_export_html()
    test_export_json()
    test_export_by_ext_dispatch()
    print("ALL PASS: test_export_formats")
