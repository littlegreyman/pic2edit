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


def _check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def main():
    ok = True
    md = kv_text_to_markdown_table(TEMPO)

    # 1) 基本结构：表头 + 分隔行 + 数据行
    ok &= _check("输出为 Markdown 表格", md.startswith("| 项目 | 内容 |"))
    ok &= _check("数据行数 >= 12", len(_rows(md)) >= 14)

    # 2) 关键键值各就各位
    ok &= _check("产品名称行", "| 产品名称 | 得宝牌抽取式纸面巾 |" in md)
    ok &= _check("执行标准值含后缀", "| 执行标准 | GB/T 20808 优等品 检验合格 |" in md)
    ok &= _check("许可证行1", "浙卫消证字（2008）第0054号" in md)
    ok &= _check("许可证行2（重复键保序）",
                 md.count("生产企业卫生许可证号") == 2)

    # 3) 括号内冒号不算键：（代码：F）留在地址值里
    ok &= _check("（代码：F）不被拆成键", "| 代码 |" not in md)
    ok &= _check("代码随地址保留", "凤坤路9号（代码：F）" in md)

    # 4) 无冒号尾随文本并入前值/备注
    ok &= _check("条形码保留", "947509910710" in md)

    # 5) 已是 Markdown 表格 → 原样返回
    already = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    ok &= _check("已是表格则原样返回", kv_text_to_markdown_table(already) == already)

    # 6) 无键值文本 → 原样返回
    plain = "这是一段没有冒号键值的普通文本"
    ok &= _check("无键值原样返回", kv_text_to_markdown_table(plain) == plain)

    # 7) 空/极短输入安全
    ok &= _check("空串安全", kv_text_to_markdown_table("") == "")
    ok &= _check("空白安全", kv_text_to_markdown_table("  \n ") == "  \n ")

    print("\nTABLE_UTILS SUMMARY:", "ALL PASS" if ok else "FAILURES PRESENT")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
