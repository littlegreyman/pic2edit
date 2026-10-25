"""后台 OCR 工作线程：避免界面卡顿。"""
from PyQt6.QtCore import QThread, pyqtSignal

from backends.base import OCRBackend
from utils.input_prep import InputItem
from utils.logging_setup import get_logger, log_exception
from utils.table_utils import kv_text_to_markdown_table

_logger = get_logger("worker")


class OcrWorker(QThread):
    progress = pyqtSignal(int, int, str)   # 当前序号, 总数, 名称
    item_done = pyqtSignal(int, str)        # 序号, 文本
    finished = pyqtSignal()
    error = pyqtSignal(str)

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
                text = self.backend.ocr_image(item.image_path, self.prompt)
                if self.as_table:
                    text = kv_text_to_markdown_table(text)
                item.result = text
                item.status = "完成"
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
