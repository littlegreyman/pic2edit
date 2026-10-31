#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 导出单测（headless / offscreen）：验证“单一导出按钮 + 按选择语义”。

不依赖 pypdfium2 / GPU：用合成 InputItem 造文档，mock 掉文件对话框与消息框，
断言导出文件内容符合选择语义：
- 选中一级目录（顶层文档） -> 该文档全部页；
- 选中二级页面（1 个或多个，同文档） -> 这些页组成的文档；
- 跨文档多选二级页 -> 拒绝（不写文件）。
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from gui import MainWindow, InputItem, InputDocument


def make_doc(name, n):
    pages = [
        InputItem(f"{name}-p{i}", f"/tmp/{name}_{i}.png", f"/tmp/{name}.pdf")
        for i in range(1, n + 1)
    ]
    return InputDocument(source_path=f"/tmp/{name}.pdf", name=name, pages=pages)


class Ctx:
    """mock 上下文：捕获导出目标路径，并接管消息框。"""
    def __init__(self):
        self.save_path = None
        self.warned = False
        self.info = False

    def get_save(self, *a, **k):
        self.save_path = os.path.join(tempfile.gettempdir(), "pic2edit_export_test.md")
        return self.save_path, ""

    def warn(self, *a, **k):
        self.warned = True

    def info(self, *a, **k):
        self.info = True

    def crit(self, *a, **k):
        pass


def main():
    app = QApplication.instance() or QApplication([])
    ctx = Ctx()
    QFileDialog.getSaveFileName = ctx.get_save
    QMessageBox.warning = ctx.warn
    QMessageBox.information = ctx.info
    QMessageBox.critical = ctx.crit

    w = MainWindow()
    docA = make_doc("a", 3)
    docB = make_doc("b", 1)
    # 预置 OCR 结果
    for k, d in enumerate([docA, docB]):
        for j, it in enumerate(d.pages):
            it.result = f"[{d.name}#{j + 1}] 内容"
    w._add_document(docA)
    w._add_document(docB)

    # --- 用例 1：选中顶层 docA -> 全部 3 页 ---
    ctx.save_path = None
    w.tree_widget.clearSelection()
    w.tree_widget.topLevelItem(0).setSelected(True)
    w.export_selected()
    assert ctx.save_path and os.path.isfile(ctx.save_path), "整文档应写出文件"
    with open(ctx.save_path, encoding="utf-8") as f:
        txt = f.read()
    for j in range(1, 4):
        assert f"[a#{j}] 内容" in txt, f"整文档缺失 a#{j}: {txt}"
    assert "[b#1] 内容" not in txt, "整文档不应包含其它文档"
    print("PASS - 选中一级目录 -> 整文档（全部页）")

    # --- 用例 2：仅选 docA 的第 1、2 页 -> 仅这两页 ---
    ctx.save_path = None
    w.tree_widget.clearSelection()
    w.tree_widget.topLevelItem(0).child(0).setSelected(True)
    w.tree_widget.topLevelItem(0).child(1).setSelected(True)
    w.export_selected()
    assert ctx.save_path and os.path.isfile(ctx.save_path)
    with open(ctx.save_path, encoding="utf-8") as f:
        txt = f.read()
    assert "[a#1] 内容" in txt and "[a#2] 内容" in txt
    assert "[a#3] 内容" not in txt, "不应包含未选中的第 3 页"
    print("PASS - 选中二级页面（2 个） -> 仅这些页组成的文档")

    # --- 用例 3：仅选 docA 的 1 个页面 -> 单一页文档 ---
    ctx.save_path = None
    w.tree_widget.clearSelection()
    w.tree_widget.topLevelItem(0).child(2).setSelected(True)
    w.export_selected()
    assert ctx.save_path and os.path.isfile(ctx.save_path)
    with open(ctx.save_path, encoding="utf-8") as f:
        txt = f.read()
    assert "[a#3] 内容" in txt and "[a#1] 内容" not in txt
    print("PASS - 选中二级页面（1 个） -> 该页文档")

    # --- 用例 4：跨源多选二级页 -> 拒绝，不写文件 ---
    ctx.save_path = None
    ctx.warned = False
    w.tree_widget.clearSelection()
    w.tree_widget.topLevelItem(0).child(0).setSelected(True)
    w.tree_widget.topLevelItem(1).child(0).setSelected(True)
    w.export_selected()
    assert ctx.warned is True, "跨源多选必须弹警告"
    assert ctx.save_path is None, "跨源多选不应写出文件"
    print("PASS - 跨文档选择二级页 -> 拒绝（不写出）")

    # --- 用例 5：同文档「一级 + 其下级页」-> 忽略下级页，只导出整文档 ---
    ctx.save_path = None
    w.tree_widget.clearSelection()
    w.tree_widget.topLevelItem(0).setSelected(True)        # 一级 docA
    w.tree_widget.topLevelItem(0).child(1).setSelected(True)  # docA 自己的第 2 页
    w.export_selected()
    assert ctx.save_path and os.path.isfile(ctx.save_path)
    with open(ctx.save_path, encoding="utf-8") as f:
        txt = f.read()
    for j in range(1, 4):
        assert f"[a#{j}] 内容" in txt, f"整文档应含全部页，缺 a#{j}: {txt}"
    assert "[b#1] 内容" not in txt
    print("PASS - 一级 + 其下级页 -> 忽略下级页，只导出整文档")

    # --- 用例 6：未显式选择但聚焦某页 -> 导出该页 ---
    ctx.save_path = None
    w.tree_widget.clearSelection()
    c = w.tree_widget.topLevelItem(1).child(0)
    w.tree_widget.setCurrentItem(c)
    w.export_selected()
    assert ctx.save_path and os.path.isfile(ctx.save_path)
    with open(ctx.save_path, encoding="utf-8") as f:
        txt = f.read()
    assert "[b#1] 内容" in txt
    print("PASS - 未多选但聚焦某页 -> 导出该页")

    print("ALL PASS: test_export_selected")


if __name__ == "__main__":
    main()
