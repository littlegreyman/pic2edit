"""装配模块单测：整文档拼接 / 空页占位 / 分隔符 / 纯字符串列表。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from utils.assemble import assemble_document, PAGE_SEP


class FakePage:
    def __init__(self, result=""):
        self.result = result


def test_assemble_basic():
    pages = [FakePage("第一页内容"), FakePage("第二页内容")]
    out = assemble_document(pages)
    assert "第一页内容" in out and "第二页内容" in out
    assert "<!-- 第 1 页 -->" in out and "<!-- 第 2 页 -->" in out
    assert PAGE_SEP in out  # 两页之间应有分隔


def test_assemble_empty_page():
    pages = [FakePage(""), FakePage("有内容")]
    out = assemble_document(pages)
    assert "第 1 页（无内容）" in out
    assert "第 2 页" in out and "有内容" in out


def test_assemble_str_list():
    out = assemble_document(["纯文本a", "纯文本b"])
    assert "纯文本a" in out and "纯文本b" in out


def test_assemble_single():
    out = assemble_document([FakePage("仅一页")])
    assert "仅一页" in out
    assert out.count(PAGE_SEP) == 0  # 单页不应出现分隔符


def main():
    test_assemble_basic()
    test_assemble_empty_page()
    test_assemble_str_list()
    test_assemble_single()
    print("ALL PASS: test_assemble")


if __name__ == "__main__":
    main()
