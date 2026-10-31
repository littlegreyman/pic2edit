"""后台 OCR 工作线程：避免界面卡顿。"""
from PySide6.QtCore import QThread, Signal

from backends.base import OCRBackend
from utils.input_prep import InputItem
from utils.logging_setup import get_logger, log_exception
from utils.table_utils import kv_text_to_markdown_table
from backends.ocr_pipeline import run as pipeline_run

_logger = get_logger("worker")


class OcrWorker(QThread):
    progress = Signal(int, int, str)   # 当前序号, 总数, 名称
    item_done = Signal(int, str)        # 序号, 文本
    worker_finished = Signal()
    error = Signal(str)

    def __init__(self, backend: OCRBackend, items: list, prompt: str, indices: list,
                 as_table: bool = False, prompt_template: str = "markdown",
                 model_params=None):
        super().__init__()
        self.backend = backend
        self.items = items
        self.prompt = prompt
        self.indices = indices
        # 表格模式：OCR 后把“键：值”文本转成 Markdown 表格
        self.as_table = as_table
        self.prompt_template = prompt_template
        self.model_params = model_params
        # 输出格式真正生效：
        # - 表格/图表 模式跳过判别式（RapidOCR 会压平/漏识别表格与图，交由 VLM 原生输出）；
        # - 图表/公式模式下关闭英文 boilerplate 幻觉守卫（公式/化学符号等含拉丁字符属正常）。
        self.skip_disc = as_table or (prompt_template == "figure")
        self.guard = prompt_template != "figure"

    def run(self):
        total = len(self.indices)
        _logger.info("OCR job started: %d item(s)%s",
                     total, "（表格模式）" if self.as_table else "")
        for n, idx in enumerate(self.indices, 1):
            item: InputItem = self.items[idx]
            item.status = "转换中"
            self.progress.emit(n, total, item.name)
            try:
                # 走统一流水线：判别式优先（RapidOCR，零幻觉）+ VLM 兜底（zh 提示词 +
                # 幻觉防御层），而非把整页直接丢给会幻觉的 VLM。干净中文印刷页在判别式
                # 阶段即完成，VLM 不会被触发，从根本上消除“英文宪法”这类幻觉。
                pipeline_run(
                    [item],
                    vlm_backend=self.backend,
                    use_vlm_fallback=True,
                    mode="markdown",
                    # 跳过判别式：表格/图表模式（RapidOCR 会压平或漏识别表格与图，
                    # 交由 VLM 原生输出）；其余文本模式保留判别式优先（零幻觉）。
                    skip_discriminative=self.skip_disc,
                    # 把 GUI 所选输出格式的提示词真正传给 VLM（之前被硬编码 ZH_PROMPT 屏蔽）
                    prompt=self.prompt,
                    # 图表/公式模式关闭幻觉守卫（正常公式/符号含拉丁字符，不应被误判丢弃）
                    guard_hallucination=self.guard,
                    # 把配置化的模型采样参数传给 VLM（llama.cpp 在请求 payload 中生效）
                    model_params=self.model_params,
                )
                text = item.result
                if self.as_table:
                    text = kv_text_to_markdown_table(text)
                item.result = text
                item.status = "完成" if text else "失败"
                _logger.info("OCR done: %s (%d/%d)", item.name, n, total)
            except Exception as e:
                item.result = f"[OCR 失败] {e}"
                item.status = "失败"
                log_exception(
                    _logger,
                    f"OCR failed: {item.name}",
                    extra=f"image={item.image_path}",
                )
            self.item_done.emit(idx, item.result)
        _logger.info("OCR job finished")
        self.worker_finished.emit()
