"""GUI 列表管理单测（headless）：删除/上下移动/清空 仅文档级 + 按钮可用状态。

验证 Task #37 的不变量：
- 删除/移动仅对一级文档节点生效；选中二级页时按钮禁用；
- 删除整个文档后扁平索引（self.items / page_tree_items / 子项 UserRole+1）保持同步；
- 上下移动只改变一级文档顺序，不影响页内容；
- 清空后列表为空。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from PySide6.QtWidgets import QApplication, QMessageBox

import gui
from gui import MainWindow, InputItem, InputDocument


def make_doc(name, n):
    pages = [
        InputItem(f"{name}-p{i}", f"/tmp/{name}_{i}.png", f"/tmp/{name}.pdf")
        for i in range(1, n + 1)
    ]
    return InputDocument(source_path=f"/tmp/{name}.pdf", name=name, pages=pages)


def _patch_dialogs():
    # 自动确认 question=Yes、忽略 information，便于 headless 运行
    gui.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    gui.QMessageBox.information = staticmethod(lambda *a, **k: None)


def _check_flat(w, expected_items):
    """校验扁平索引一致性：items 长度、page_tree_items 平行、子项 UserRole+1 == 位置。"""
    assert len(w.items) == expected_items, (len(w.items), expected_items)
    assert len(w.page_tree_items) == expected_items
    for i, tw in enumerate(w.page_tree_items):
        assert tw.data(0, gui.Qt.UserRole + 1) == i, (i, tw.data(0, gui.Qt.UserRole + 1))
    # documents 顺序与顶层一致
    assert len(w.documents) == w.tree_widget.topLevelItemCount()


def main():
    _patch_dialogs()
    app = QApplication.instance() or QApplication([])

    w = MainWindow()
    docs = [make_doc("a", 2), make_doc("b", 1), make_doc("c", 3)]
    for d in docs:
        w._add_document(d)
    _check_flat(w, 6)

    # --- 按钮状态：选中子页 -> 删除/移动禁用 ---
    w.tree_widget.clearSelection()
    w.tree_widget.topLevelItem(0).child(0).setSelected(True)
    w._update_list_buttons()
    assert w.btn_delete.isEnabled() is False, "选中子页时删除应禁用"
    assert w.btn_up.isEnabled() is False
    assert w.btn_down.isEnabled() is False
    assert w.btn_clear.isEnabled() is True

    # --- 按钮状态：选中一个顶层文档 -> 删除/移动使能 ---
    w.tree_widget.clearSelection()
    top1 = w.tree_widget.topLevelItem(1)  # 文档 b
    top1.setSelected(True)
    w._update_list_buttons()
    assert w.btn_delete.isEnabled() is True
    assert w.btn_up.isEnabled() is True   # idx=1 可上移
    assert w.btn_down.isEnabled() is True  # idx=1 可下移

    # --- 删除文档 b（顶层 idx=1）---
    w.delete_selected()
    # 剩余 a(2页) + c(3页) = 5
    _check_flat(w, 5)
    assert w.tree_widget.topLevelItemCount() == 2
    assert w.tree_widget.topLevelItem(0).text(0).startswith("a")
    assert w.tree_widget.topLevelItem(1).text(0).startswith("c")

    # --- 移动：把 c（现在 idx=1）上移到 idx=0 ---
    w.tree_widget.clearSelection()
    c_top = w.tree_widget.topLevelItem(1)
    c_top.setSelected(True)
    w.move_selected(-1)
    assert w.tree_widget.topLevelItem(0).text(0).startswith("c")
    assert w.tree_widget.topLevelItem(1).text(0).startswith("a")
    _check_flat(w, 5)

    # --- 移动边界：c 已在 idx=0，上移应为 no-op ---
    w.tree_widget.clearSelection()
    w.tree_widget.topLevelItem(0).setSelected(True)
    w.move_selected(-1)
    assert w.tree_widget.topLevelItem(0).text(0).startswith("c")  # 未变

    # --- 清空 ---
    w.clear_all()
    _check_flat(w, 0)
    assert w.tree_widget.topLevelItemCount() == 0
    assert w.btn_clear.isEnabled() is False

    print("ALL PASS: test_gui_list_mgmt")


if __name__ == "__main__":
    main()
