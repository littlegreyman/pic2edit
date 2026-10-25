"""Unit tests for utils.text_utils.trim_trailing_repetition.

Covers the degenerate-loop cases seen in the wild plus the "clean output must
not be touched" guarantee.
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "ocr_app"))

from utils.text_utils import trim_trailing_repetition

REAL = (
    "产品名称：得宝牌抽取式纸面巾 执行标准：GB/T 20808 "
    "生产企业卫生许可证号：（鲁）卫消证字（2013）第0054号 地址：山东省济南市莱芜高新区泰山路38号 "
    "维达纸业（中国）有限公司 地址：广东省江门市新会区三江镇新江村寺北洋沙"
)

# Loop onset = first square bracket; everything from there is garbage.
LOOP_BRACKET = REAL + (
    " 生产企业卫生许可证号：粤卫消证字[2021]-13-第0006号 具体生产企业见喷码代码 服务热线：4006-315-138 "
    "生产企业卫生许可证号：浙卫消证字[2021]-13-第0031号 维达护理用品（中国）有限公司 地址：浙江省江门市新会区金坛区凤坤路9号（代码：F） "
    "生产企业卫生许可证号：浙卫消证字[2015]第0031号 维达护理用品（中国）有限公司 地址：浙江省江门市新会区金坛区凤坤路9号（代码：F） "
    "生产企业卫生许可证号：浙卫消证字[2021]-13-第0031号 维达护理用品（中国）有限公司 地址：浙江省江门市新会区金坛区凤坤路9号（代码：F） "
    "生产企业卫生许可证号：浙卫消证字[2015]第0031号 维达护理用品（中国）有限公司 地址：浙江省江门市新会区金坛区凤坤路9号（代码：F）"
)

# Same-notation loop (no brackets): the line repeats verbatim.
REPEAT_LINE = "生产企业卫生许可证号：鲁卫消证字（2013）第0054号 地址：山东省济南市莱芜高新区泰山路38号"
LOOP_SAME = REAL + (" " + REPEAT_LINE) * 40

# A clean doc that legitimately contains a single bracketed code (must stay).
CLEAN_WITH_ONE_BRACKET = REAL + " 条形码：[6941234567890] 价格：￥12.90"

# Block repeated once at the tail, but the final entry of the copy differs
# (粤 -> （鲁）). Not an exact duplicate, so the exact-match detector misses it;
# the anchored/signature detector must still cut the regurgitated block.
_BLOCK = (
    "生产企业卫生许可证号：浙卫消证字（2015）第0031号 维达护理用品（中国）有限公司 "
    "地址：浙江省孝感市孝南区新安开发区316国道复线（代码：H） "
    "生产企业卫生许可证号：（鄂）卫消证字（2010）第0020号 地址：湖北省孝感市孝南高新区维达大道1号（代码：X） "
    "生产企业卫生许可证号：粤卫消证字[2021]-13-第0006号 具体生产企业见喷码代码 服务热线：4006-315-138"
)
_BLOCK_DUP = (
    "生产企业卫生许可证号：浙卫消证字（2015）第0031号 维达护理用品（中国）有限公司 "
    "地址：浙江省孝感市孝南区新安开发区316国道复线（代码：H） "
    "生产企业卫生许可证号：（鄂）卫消证字（2010）第0020号 地址：湖北省孝感市孝南高新区维达大道1号（代码：X） "
    "生产企业卫生许可证号：（鲁）卫消证字[2021]-13-第0006号 具体生产企业见喷码代码 服务热线：4006-315-138"
)
GENUINE_BLOCK = REAL + " " + _BLOCK
LOOP_BLOCK_DUP = GENUINE_BLOCK + " " + _BLOCK_DUP


def _check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def main():
    ok = True
    # 1) clean text untouched
    ok &= _check("clean text unchanged", trim_trailing_repetition(REAL) == REAL)
    # 2) loop with brackets is trimmed to the real prefix (loop removed)
    t1 = trim_trailing_repetition(LOOP_BRACKET)
    ok &= _check("bracket loop trimmed", t1 == REAL)
    ok &= _check("bracket loop removes all [20xx]", t1.count("[20") == 0)
    # 3) same-notation verbatim loop is trimmed. The loop line differs from the
    #    real （鲁） line by a parenthesis, so it is a *new* block (its first
    #    occurrence is the loop start) and the whole loop is removed -> 0 left.
    t2 = trim_trailing_repetition(LOOP_SAME)
    ok &= _check("same-notation loop trimmed", t2 == REAL)
    ok &= _check("same-notation loop removes repeats",
                 t2.count(REPEAT_LINE) == 0)
    # 4) a single legitimate bracketed code is NOT treated as a loop
    t3 = trim_trailing_repetition(CLEAN_WITH_ONE_BRACKET)
    ok &= _check("single bracket not trimmed", t3 == CLEAN_WITH_ONE_BRACKET)
    # 5) block regurgitated once with a differing final entry is cut (Signal C)
    t4 = trim_trailing_repetition(LOOP_BLOCK_DUP)
    ok &= _check("block-dup (differing tail) trimmed", t4 == GENUINE_BLOCK)
    ok &= _check("block-dup removes the extra copy", t4.endswith("服务热线：4006-315-138")
                 and t4.count("服务热线") == 1)
    # 5) empty / short input safe
    ok &= _check("empty safe", trim_trailing_repetition("") == "")
    ok &= _check("short safe", trim_trailing_repetition("abc") == "abc")

    print("\nTEXT_UTILS SUMMARY:", "ALL PASS" if ok else "FAILURES PRESENT")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
