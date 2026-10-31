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
    finished = Signal()
    error = Signal(str)

    def __init__(self, backend: OCRBackend, items: list, prompt: str, indices: list,
                 as_table: bool = False):
        super().__init__()
        self.backend = backend
        self.items = items
        self.prompt = prompt
        self.indices = indices
        # 表格模式：OCR 后把“键：值”文本转成 Markdown 表格
        self.as_table = as_table

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
                    # 表格模式：跳过判别式后端（RapidOCR 会把网格/键值版式压成一段
                    # 扁平文本、彻底丢失表格结构）；改用 VLM（DeepSeek-OCR）原生输出，
                    # 其“键：值”行再由 kv_text_to_markdown_table 转成 Markdown 表格。
                    skip_discriminative=self.as_table,
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
        self.finished.emit()
