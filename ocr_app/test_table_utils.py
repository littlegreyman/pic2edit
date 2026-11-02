"""Unit tests for utils.table_utils.kv_text_to_markdown_table."""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "ocr_app"))

from utils.table_utils import kv_text_to_markdown_table

# 真实 tempo_package.jpg OCR 输出（裁剪后）的代表片段
TEMPO = (
    "产品名称：得宝牌抽取式纸面巾 香型：天然无香 执行标准：GB/T 20808 优等品 检验合格 "
    "卫生标准：GB 15979 生产日期：见包装 保质期：三年 产品规格：4层160mmX195mm/张 净含量：90抽 "
    "责任单位：维达商贸有限公司 地址：广东省江门市新会区会城东侯路65号 邮政编码：529100 "
    "生产企业：维达纸业（浙江）有限公司 地址：浙江省江门市新会区金坛区凤坤路9号（代码：F） "
    "生产企业卫生许可证号：浙卫消证字（2008）第0054号 "
    "生产企业卫生许可证号：浙卫消证字（2015）第0031号 "
    "条形码 6 947509910710"
)


def _rows(md):
    return [ln for ln in md.splitlines() if ln.startswith("|")]


def test_kv_text_to_markdown_table():
    md = kv_text_to_markdown_table(TEMPO)

    # 1) 基本结构：表头 + 分隔行 + 数据行
    assert md.startswith("| 项目 | 内容 |")
    assert len(_rows(md)) >= 14

    # 2) 关键键值各就各位
    assert "| 产品名称 | 得宝牌抽取式纸面巾 |" in md
    assert "| 执行标准 | GB/T 20808 优等品 检验合格 |" in md
    assert "浙卫消证字（2008）第0054号" in md
    # 3) 重复键保序
    assert md.count("生产企业卫生许可证号") == 2

    # 4) 括号内冒号不算键：（代码：F）留在地址值里
    assert "| 代码 |" not in md
    assert "凤坤路9号（代码：F）" in md

    # 5) 无冒号尾随文本并入前值/备注
    assert "947509910710" in md

    # 6) 已是 Markdown 表格 → 原样返回
    already = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    assert kv_text_to_markdown_table(already) == already

    # 7) 无键值文本 → 原样返回
    plain = "这是一段没有冒号键值的普通文本"
    assert kv_text_to_markdown_table(plain) == plain

    # 8) 空/极短输入安全
    assert kv_text_to_markdown_table("") == ""
    assert kv_text_to_markdown_table("  \n ") == "  \n "
