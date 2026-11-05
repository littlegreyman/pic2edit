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
    script = None
    for p in _CANDIDATES:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            script = p
            break
    if script is None:
        pytest.skip("未找到 pic2edit skill 脚本，跳过 skill 渲染测试")

    # skill 脚本在模块级用 _find_ocr_app() 定位 ocr_app，找不到会抛 RuntimeError。
    # 本测试文件本身就在 ocr_app/ 内，直接把该路径喂给环境变量，这样在 CI
    # （检出于 /home/runner/work/... 等陌生路径）也能正常导入，而不是误报失败。
    os.environ["PIC2EDIT_OCR_APP"] = _HERE

    spec = importlib.util.spec_from_file_location("_p2e_skill_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except (Exception, SystemExit) as e:
        # 缺依赖（如无 numpy/cv2）或路径异常：跳过而不是让 CI 变红
        sys.modules.pop(spec.name, None)
        pytest.skip(f"skill 脚本导入失败，跳过：{e!r}")
    return mod


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


# ---------------------------------------------------------------------------
# Phase 3.2：schema 三态在 skill 渲染链路里的表现
# ---------------------------------------------------------------------------
def _invoice_data():
    from testdata import invoice_sample

    page = invoice_sample.page()
    page["engine"] = "disc"
    return {"pages": [page]}


def test_md_uses_schema_when_hit(skill, tmp_path):
    """命中发票 schema → md 必须按字段输出，而不是把 OCR 文本原样吐出来。"""
    from schemas import apply_schemas

    data = _invoice_data()
    apply_schemas(data["pages"], "auto")
    out = skill._render(data, "md", str(tmp_path / "inv.md"), "in.pdf", False, "markdown")
    text = open(out, encoding="utf-8").read()

    assert "北京星辰科技有限公司" in text
    assert "[购买方]" in text or "购买方" in text
    assert "壹万零陆佰元整" in text
    assert "增值税专用发票" in text


def test_md_without_schema_falls_back_to_text(skill, tmp_path):
    """--schema none → 不抽 schema，退回通用渲染（这里没有表格版式，应为原文）。"""
    from schemas import MODE_NONE, apply_schemas

    data = _invoice_data()
    apply_schemas(data["pages"], MODE_NONE)
    assert "schema" not in data["pages"][0], "none 态不得写入 schema 键"
    out = skill._render(data, "md", str(tmp_path / "raw.md"), "in.pdf", False, "markdown")
    text = open(out, encoding="utf-8").read()

    # 未启用 schema 时，发票的字段化排版（[购买方] 段落标题）不应出现
    assert "[购买方]" not in text


def test_html_only_embeds_css_of_hit_schemas(skill, tmp_path):
    """html 导出只在真正命中时拼票面 CSS，避免无 schema 的文档带上用不上的样式。"""
    from schemas import MODE_NONE, apply_schemas

    plain = {"pages": [{"page": 1, "raw_text": "这是一段普通正文。",
                        "lines": [], "img_w": 0, "img_h": 0, "engine": "disc"}]}
    apply_schemas(plain["pages"], MODE_NONE)
    out = skill._render(plain, "html", str(tmp_path / "p.html"), "in.pdf", False, "markdown")
    html = open(out, encoding="utf-8").read()
    assert "inv-wrap" not in html, "未命中任何 schema 时不应嵌入票面 CSS"

    data = _invoice_data()
    apply_schemas(data["pages"], "auto")
    out = skill._render(data, "html", str(tmp_path / "i.html"), "in.pdf", False, "markdown")
    html = open(out, encoding="utf-8").read()
    assert "inv-wrap" in html, "命中发票 schema 时必须带上票面 CSS"
