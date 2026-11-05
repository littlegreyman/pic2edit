"""DocumentController 单测：**不需要 QApplication**（这正是抽出来的目的）。

覆盖 Phase 3.1 的门禁「分组编排有单测」——此前这些规则只能靠起一个 offscreen
窗口点树节点来验证，失败信息也不指向规则本身。这里直接对模型断言：

- 扁平页序随增删/移动自动重排（索引由模型算，不由界面维护）；
- 选择规约四条规则：整篇优先 / 同文档选页按页序 / 跨源拒绝 / 空选择；
- 文档身份按对象判定（dataclass 值相等的两个文档不能被误当成同一个）。
"""
import subprocess
import sys
import textwrap

from document_controller import DocumentController, Selection
from utils.input_prep import InputDocument, InputItem


def make_doc(name, pages):
    items = [
        InputItem(f"{name}-p{i}", f"/tmp/{name}_{i}.png", f"/tmp/{name}.pdf")
        for i in range(1, pages + 1)
    ]
    return InputDocument(source_path=f"/tmp/{name}.pdf", name=name, pages=items)


# ---------- 状态与索引 ----------
def test_add_flattens_in_document_order():
    c = DocumentController()
    a, b = make_doc("a", 3), make_doc("b", 2)

    assert c.add_document(a) == 3
    assert c.add_document(b) == 2

    assert [d.name for d in c.documents] == ["a", "b"]
    assert len(c.items) == 5 and c.page_count == 5 and c.doc_count == 2
    # 扁平索引 = 文档顺序 + 页序
    assert c.index_of(a.pages[0]) == 0
    assert c.index_of(a.pages[2]) == 2
    assert c.index_of(b.pages[0]) == 3


def test_index_of_unknown_item_returns_minus_one():
    c = DocumentController()
    c.add_document(make_doc("a", 1))
    stray = InputItem("stray", "/tmp/x.png", "/tmp/x.png")
    assert c.index_of(stray) == -1


def test_doc_flat_indices_keeps_page_order_and_rejects_foreign_doc():
    c = DocumentController()
    a = make_doc("a", 3)
    c.add_document(a)

    assert [i for _it, i in c.doc_flat_indices(a)] == [0, 1, 2]
    assert c.doc_flat_indices(make_doc("zzz", 2)) == []


def test_find_document_by_page_identity():
    c = DocumentController()
    a, b = make_doc("a", 2), make_doc("b", 1)
    c.add_documents([a, b])

    assert c.find_document(a.pages[1]) is a
    assert c.find_document(b.pages[0]) is b
    assert c.find_document(InputItem("x", "/tmp/x.png", "/tmp/x")) is None


# ---------- 选择规约（分组编排）----------
def test_build_units_whole_document():
    c = DocumentController()
    a = make_doc("a", 3)
    c.add_document(a)

    units = c.build_units(Selection(docs=(a,)))
    assert units == [(a, [0, 1, 2])]


def test_build_units_selected_pages_keep_page_order_not_click_order():
    c = DocumentController()
    a = make_doc("a", 4)
    c.add_document(a)

    # 故意按「第3页、第1页」的顺序传入选择
    units = c.build_units(Selection(pages=((a, a.pages[2]), (a, a.pages[0]))))
    assert units == [(a, [0, 2])], "应按页序而非点击顺序排列"


def test_build_units_rejects_cross_document_page_selection():
    c = DocumentController()
    a, b = make_doc("a", 2), make_doc("b", 2)
    c.add_documents([a, b])

    assert c.build_units(Selection(pages=((a, a.pages[0]), (b, b.pages[0])))) is None


def test_build_units_top_doc_plus_other_doc_page_yields_two_units():
    c = DocumentController()
    a, b = make_doc("a", 2), make_doc("b", 3)
    c.add_documents([a, b])

    units = c.build_units(Selection(docs=(a,), pages=((b, b.pages[1]),)))
    assert units == [(a, [0, 1]), (b, [3])]


def test_build_units_top_doc_swallows_its_own_page_selection():
    c = DocumentController()
    a = make_doc("a", 3)
    c.add_document(a)

    units = c.build_units(Selection(docs=(a,), pages=((a, a.pages[0]),)))
    assert units == [(a, [0, 1, 2])], "整篇优先，同文档的页级选择应被忽略"


def test_build_units_empty_selection():
    c = DocumentController()
    c.add_document(make_doc("a", 2))
    assert c.build_units(Selection()) == []
    assert not Selection()


def test_document_identity_is_object_based_not_value_based():
    """两个字段完全相同的文档必须被视为不同对象（按 id 判定）。"""
    c = DocumentController()
    a1 = make_doc("same", 2)
    a2 = make_doc("same", 2)  # 字段值相同，但页对象不同
    c.add_documents([a1, a2])

    units = c.build_units(Selection(docs=(a2,)))
    assert units == [(a2, [2, 3])], units


# ---------- 变更后的重排 ----------
def test_remove_documents_densifies_indices():
    c = DocumentController()
    a, b, d = make_doc("a", 2), make_doc("b", 1), make_doc("d", 3)
    c.add_documents([a, b, d])

    assert c.remove_documents([b]) == 1
    assert [x.name for x in c.documents] == ["a", "d"]
    # b 的页被摘掉后，d 的页索引应前移
    assert c.index_of(d.pages[0]) == 2
    assert len(c.items) == 5


def test_remove_unknown_document_is_noop():
    c = DocumentController()
    c.add_document(make_doc("a", 1))
    assert c.remove_documents([make_doc("ghost", 1)]) == 0
    assert c.doc_count == 1


def test_move_document_reorders_and_reindexes():
    c = DocumentController()
    a, b = make_doc("a", 2), make_doc("b", 3)
    c.add_documents([a, b])

    assert c.move_document(b, -1) is True
    assert [d.name for d in c.documents] == ["b", "a"]
    assert c.index_of(b.pages[0]) == 0
    assert c.index_of(a.pages[0]) == 3


def test_move_document_out_of_range_is_noop():
    c = DocumentController()
    a, b = make_doc("a", 1), make_doc("b", 1)
    c.add_documents([a, b])

    assert c.move_document(a, -1) is False   # 已在顶部
    assert c.move_document(b, 1) is False    # 已在底部
    assert [d.name for d in c.documents] == ["a", "b"]


def test_clear_resets_state():
    c = DocumentController()
    c.add_documents([make_doc("a", 2), make_doc("b", 1)])
    c.clear()
    assert c.documents == [] and c.items == []
    assert c.build_units(Selection()) == []


# ---------- 门禁：本模块必须无 Qt 依赖 ----------
def test_controller_imports_without_qt():
    """在**全新解释器**里导入本模块，PySide6 不得被拉进来。

    只做源码扫描不够（`import gui` 之类可能间接引入），所以真起一个子进程验证：
    这是 Phase 3.1「编排逻辑脱离 Qt 可单测」的硬门禁。
    """
    code = textwrap.dedent(
        """
        import sys
        import document_controller  # noqa: F401
        leaked = [m for m in sys.modules if m.startswith("PySide6")]
        assert not leaked, "document_controller 不应引入 Qt: %s" % leaked
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parent),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
