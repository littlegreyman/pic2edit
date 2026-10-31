"""GUI 树形化单测（headless / offscreen）：树结构 + 选择语义。

验证 Task #36 的核心不变量：
- 顶层=文档、子项=页；
- 选中顶层文档 -> 转换/导出整文档（全部页）；
- 仅选同文档子页 -> 仅这些页（多页合并）；
- 跨源多选子页 -> 拒绝（_build_units 返回 None）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from PySide6.QtWidgets import QApplication

from gui import MainWindow, InputItem, InputDocument


def make_doc(name, n):
    pages = [
        InputItem(f"{name}-p{i}", f"/tmp/{name}_{i}.png", f"/tmp/{name}.pdf")
        for i in range(1, n + 1)
    ]
    return InputDocument(source_path=f"/tmp/{name}.pdf", name=name, pages=pages)


def main():
    app = QApplication.instance() or QApplication([])

    w = MainWindow()
    docA = make_doc("a", 3)
    docB = make_doc("b", 1)
    w._add_document(docA)
    w._add_document(docB)

    # --- 树结构 ---
    assert w.tree_widget.topLevelItemCount() == 2, "应有 2 个顶层文档"
    assert w.tree_widget.topLevelItem(0).childCount() == 3, "docA 应有 3 页"
    assert w.tree_widget.topLevelItem(1).childCount() == 1, "docB 应有 1 页"
    assert len(w.page_tree_items) == 4, "page_tree_items 应与 items 平行"
    assert len(w.items) == 4, "items 应有 4 页"

    # --- 选中顶层 docA -> 整文档（全部页）---
    w.tree_widget.clearSelection()
    topA = w.tree_widget.topLevelItem(0)
    topA.setSelected(True)
    top_docs, child_pages = w._gather_selection()
    assert len(top_docs) == 1 and top_docs[0] is docA
    units = w._build_units(top_docs, child_pages)
    assert units == [(docA, [0, 1, 2])], units

    # --- 仅选 docA 的两个子页 -> 仅这两页 ---
    w.tree_widget.clearSelection()
    c0 = w.tree_widget.topLevelItem(0).child(0)
    c1 = w.tree_widget.topLevelItem(0).child(1)
    c0.setSelected(True)
    c1.setSelected(True)
    top_docs, child_pages = w._gather_selection()
    assert len(top_docs) == 0 and len(child_pages) == 2, child_pages
    units = w._build_units(top_docs, child_pages)
    assert units == [(docA, [0, 1])], units

    # --- 跨源：docA 一页 + docB 一页 -> 拒绝 ---
    w.tree_widget.clearSelection()
    c0.setSelected(True)
    cb = w.tree_widget.topLevelItem(1).child(0)
    cb.setSelected(True)
    top_docs, child_pages = w._gather_selection()
    units = w._build_units(top_docs, child_pages)
    assert units is None, "跨源多选必须返回 None"

    # --- 顶层 docA + docB 子页 -> 两个单元（docA 整篇，docB 该页）---
    w.tree_widget.clearSelection()
    topA.setSelected(True)
    cb.setSelected(True)
    top_docs, child_pages = w._gather_selection()
    units = w._build_units(top_docs, child_pages)
    assert units == [(docA, [0, 1, 2]), (docB, [3])], units

    # --- 同文档：顶层 docA + docA 自己的子页 -> 忽略子页，只整篇 docA ---
    w.tree_widget.clearSelection()
    topA.setSelected(True)
    c0.setSelected(True)
    c1.setSelected(True)
    top_docs, child_pages = w._gather_selection()
    units = w._build_units(top_docs, child_pages)
    assert units == [(docA, [0, 1, 2])], units

    # offscreen 下解释器退出时 Qt 对象析构竞态可能崩溃，显式销毁窗口（app 仍存活）
    w.deleteLater()
    app.processEvents()
    print("ALL PASS: test_gui_tree")


if __name__ == "__main__":
    main()
