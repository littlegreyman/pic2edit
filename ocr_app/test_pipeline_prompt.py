"""流水线提示词贯通单测：验证输出格式（提示词）真正影响 VLM，且幻觉守卫按格式生效。

验证 Task #38 的不变量：
- 调用方传入 prompt 时，VLM 实际收到的就是该 prompt（不再被硬编码 ZH_PROMPT 屏蔽）；
- 不传 prompt 时回退到 ZH_PROMPT（保持默认中文指令行为）；
- 文本模式（guard 开）：VLM 命中英文 boilerplate 幻觉 -> 丢弃；
- 图表/公式模式（guard 关）：即便含疑似 boilerplate 也保留（公式/符号含拉丁字符属正常）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

import backends.ocr_pipeline as P
from backends.ocr_pipeline import run
from utils.input_prep import InputItem


class FakeVLM:
    name = "fake"

    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def ocr_image(self, image_path, prompt):
        self.calls.append(prompt)
        return "FAKE OUTPUT for: " + prompt


class FakeDisc:
    def available(self):
        return True

    def ocr_image(self, image_path):
        return ""  # 低质量 -> 触发 VLM 兜底


def _patch():
    P.DiscriminativeBackend = FakeDisc


def test_prompt_passthrough():
    _patch()
    vlm = FakeVLM()
    item = InputItem("x", "/tmp/x.png", "/tmp/x.pdf")
    run([item], vlm_backend=vlm, use_vlm_fallback=True, skip_discriminative=True,
        prompt="Parse the figure.", guard_hallucination=False)
    assert vlm.calls == ["Parse the figure."], vlm.calls
    assert "Parse the figure." in item.result


def test_default_prompt_is_zh_when_none():
    _patch()
    vlm = FakeVLM()
    item = InputItem("x", "/tmp/x.png", "/tmp/x.pdf")
    run([item], vlm_backend=vlm, use_vlm_fallback=True, skip_discriminative=True,
        prompt=None)
    assert vlm.calls == [P.ZH_PROMPT], vlm.calls


def test_guard_discards_hallucination_text_mode():
    _patch()
    vlm = FakeVLM()
    vlm.ocr_image = lambda p, pr: "Congress shall make no law ..."
    item = InputItem("x", "/tmp/x.png", "/tmp/x.pdf")
    run([item], vlm_backend=vlm, use_vlm_fallback=True, skip_discriminative=True,
        prompt="Free OCR.", guard_hallucination=True)
    assert item.result == "", repr(item.result)  # 幻觉被丢弃


def test_guard_off_keeps_hallucination_figure_mode():
    _patch()
    vlm = FakeVLM()
    vlm.ocr_image = lambda p, pr: "Congress shall make no law ..."
    item = InputItem("x", "/tmp/x.png", "/tmp/x.pdf")
    run([item], vlm_backend=vlm, use_vlm_fallback=True, skip_discriminative=True,
        prompt="Parse the figure.", guard_hallucination=False)
    assert "Congress shall make no law" in item.result  # 图表模式不守卫，保留


def main():
    test_prompt_passthrough()
    test_default_prompt_is_zh_when_none()
    test_guard_discards_hallucination_text_mode()
    test_guard_off_keeps_hallucination_figure_mode()
    print("ALL PASS: test_pipeline_prompt")


if __name__ == "__main__":
    main()
