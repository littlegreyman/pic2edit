"""文档编排模型：文档分组、扁平页序、选择规约（**无 Qt 依赖**）。

为什么单独抽出来
----------------
这部分逻辑此前长在 ``gui.MainWindow`` 里，且以 ``QTreeWidget`` 为唯一事实源
（``_reindex`` 反向扫树重建 ``items``）。后果有三：

1. 编排规则（整篇 / 选页 / 跨源禁止）只能靠起一个 offscreen ``QApplication``
   才能测，单测成本高、失败信息还不指向规则本身；
2. 「列表状态」有了两个事实源（树 + ``items``），任何一处漏同步都是隐性 bug；
3. 想在 Agent 侧复用「把一组文档/页规约成转换单元」的逻辑时，会被 Qt 绑架。

现在把状态与规则收进 ``DocumentController``：它只依赖 ``InputDocument`` /
``InputItem`` 这两个纯数据对象，可以脱离 Qt 直接单测。GUI 里树退化为**视图**
（``_rebuild_tree`` 单向从模型渲染），选择则先由 Qt 侧翻译成 ``Selection``
再交给本模块判定——GUI 不再自己实现任何编排规则。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from utils.input_prep import InputDocument, InputItem

# 一个转换单元 = (源文档, 该单元包含的页在扁平序列中的索引)
Unit = Tuple[InputDocument, List[int]]


@dataclass(frozen=True)
class Selection:
    """与 UI 无关的选择描述。

    Attributes:
        docs: 被选中的**文档级**（树顶层）节点，保持 UI 选择顺序。
        pages: 被选中的**页级**（树子项）节点，元素为 ``(所属文档, 页)``。
    """

    docs: Tuple[InputDocument, ...] = ()
    pages: Tuple[Tuple[InputDocument, InputItem], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.docs or self.pages)


@dataclass
class DocumentController:
    """文档列表 + 扁平页序 + 选择规约。

    文档顺序即 ``documents`` 顺序，扁平页序即各文档 ``pages`` 顺序拼接，
    因此 ``items`` 的索引在增删/移动后会自动重排，无需调用方维护。
    """

    documents: List[InputDocument] = field(default_factory=list)
    # 扁平页序列（派生量，只读；每次变更后由 _sync() 重建）
    items: List[InputItem] = field(default_factory=list)

    # ---------- 状态查询 ----------
    def __len__(self) -> int:
        return len(self.items)

    @property
    def doc_count(self) -> int:
        return len(self.documents)

    @property
    def page_count(self) -> int:
        return len(self.items)

    def owns(self, doc: InputDocument) -> bool:
        """doc 是否属于本模型（按**对象身份**判定，避免 dataclass 值相等误判）。"""
        return any(d is doc for d in self.documents)

    def index_of(self, item: InputItem) -> int:
        """页的扁平索引；不属于本模型时返回 ``-1``（不抛异常）。"""
        for i, it in enumerate(self.items):
            if it is item:
                return i
        return -1

    def doc_flat_indices(self, doc: InputDocument) -> List[Tuple[InputItem, int]]:
        """该文档所有页的 ``(item, 扁平索引)``，保持页序；未知文档返回空表。"""
        if not self.owns(doc):
            return []
        out = []
        for it in doc.pages:
            idx = self.index_of(it)
            if idx >= 0:
                out.append((it, idx))
        return out

    # ---------- 状态变更 ----------
    def _sync(self) -> None:
        """由 documents 重建扁平页序（唯一的重排入口）。"""
        self.items = [it for doc in self.documents for it in doc.pages]

    def add_document(self, doc: InputDocument) -> int:
        """追加一个文档，返回其页数。"""
        self.documents.append(doc)
        self._sync()
        return len(doc.pages)

    def add_documents(self, docs: Iterable[InputDocument]) -> int:
        """批量追加，返回新增页数。"""
        before = len(self.items)
        for doc in docs:
            self.documents.append(doc)
        self._sync()
        return len(self.items) - before

    def remove_documents(self, docs: Sequence[InputDocument]) -> int:
        """移除给定文档（按身份），返回实际移除的文档数。"""
        targets = [d for d in self.documents if any(d is t for t in docs)]
        if not targets:
            return 0
        self.documents = [d for d in self.documents
                          if not any(d is t for t in targets)]
        self._sync()
        return len(targets)

    def move_document(self, doc: InputDocument, delta: int) -> bool:
        """把文档在列表中移动 ``delta`` 位；越界或未找到返回 ``False``（no-op）。"""
        idx = next((i for i, d in enumerate(self.documents) if d is doc), -1)
        if idx < 0:
            return False
        new = idx + delta
        if new < 0 or new >= len(self.documents) or new == idx:
            return False
        self.documents.pop(idx)
        self.documents.insert(new, doc)
        self._sync()
        return True

    def clear(self) -> None:
        self.documents = []
        self.items = []

    def find_document(self, item: InputItem) -> Optional[InputDocument]:
        """页所属的文档；找不到返回 ``None``。"""
        for doc in self.documents:
            if any(it is item for it in doc.pages):
                return doc
        return None

    # ---------- 选择规约 ----------
    def build_units(self, selection: Selection) -> Optional[List[Unit]]:
        """把选择规约为转换单元列表，每个单元 = ``(doc, [扁平索引, ...])``。

        规则（与 GUI 树形选择语义一一对应）：
          - 选中文档顶层 → 该文档**整篇**，此时该文档下的页级选择被忽略；
          - 仅选中同文档的若干页 → 按**页序**（而非点击顺序）归为一个单元；
          - 跨多个文档多选页 → 返回 ``None``（调用方据此提示并拒绝）。

        Returns:
            单元列表；跨源多选时返回 ``None``；空选择返回空列表。
        """
        units: List[Unit] = []
        covered = set()
        for doc in selection.docs:
            units.append((doc, [idx for _it, idx in self.doc_flat_indices(doc)]))
            covered.add(id(doc))

        by_doc: dict[int, Tuple[InputDocument, List[InputItem]]] = {}
        for doc, item in selection.pages:
            if id(doc) in covered:
                continue  # 该文档已按整篇处理，页级选择不再重复计入
            by_doc.setdefault(id(doc), (doc, []))[1].append(item)
        if len(by_doc) > 1:
            return None

        for _did, (doc, picked) in by_doc.items():
            wanted = {id(it) for it in picked}
            idxs = [idx for it, idx in self.doc_flat_indices(doc) if id(it) in wanted]
            units.append((doc, idxs))
        return units
