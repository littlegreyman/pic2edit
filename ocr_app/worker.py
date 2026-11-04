"""后台 OCR 工作线程：避免界面卡顿。"""
from PySide6.QtCore import QThread, Signal

from backend_api import OCRBackend, DiscriminativeBackend
from utils.input_prep import InputItem
from utils.logging_setup import get_logger, log_exception
from utils.table_utils import kv_text_to_markdown_table
from utils.table_layout import build_table_from_lines
from backend_api import run as pipeline_run, RoutingPolicy, vmem_limited_gb, env_force_skip

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
        self._disc = None  # 表格重建复用的判别式后端（冻结态进程内模型只加载一次）
        # 输出格式真正生效：
        # - 表格/图表/HTML表格 模式跳过判别式（RapidOCR 会压平/漏识别表格与图，交由 VLM 原生输出）；
        # - 图表/公式模式下关闭英文 boilerplate 幻觉守卫（公式/化学符号等含拉丁字符属正常）。
        # 路由策略收敛到共享内核 RoutingPolicy：页面类型决定「是否跳过判别式」
        # 与「是否启用幻觉守卫」，GUI 与 Skill 共用同一套规则（修复两侧路由不一致）。
        # 路由入参与 Skill 对齐：除页面类型外，统一注入运行环境信息（虚拟内存软限制、
        # 强制跳过环境变量），确保沙箱/受限环境下两侧路由一致。
        # hw_detected：GUI 当前未做手写体预筛（属 Phase 3.2 后端能力下沉），传 False 等价原行为。
        self.policy = RoutingPolicy.for_page_type(
            "table" if as_table else prompt_template,
            force_skip=env_force_skip(),
            vmem_limited_gb=vmem_limited_gb(),
            hw_detected=False,
        )

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
                    # 表格模式要通知流水线走表格路径（此前恒传 markdown，表格识别永不生效）
                    mode=("table" if self.as_table else "markdown"),
                    # 路由策略（页面类型驱动）替代硬编码的 skip_discriminative/guard_hallucination
                    policy=self.policy,
                    # 把 GUI 所选输出格式的提示词真正传给 VLM（之前被硬编码 ZH_PROMPT 屏蔽）
                    prompt=self.prompt,
                    # 把配置化的模型采样参数传给 VLM（llama.cpp 在请求 payload 中生效）
                    model_params=self.model_params,
                )
                text = item.result
                if self.as_table:
                    text = self._table_result(item, text)
                else:
                    text = self._maybe_layout_table(item, text)
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

    # ---------- 表格重建（坐标 -> Markdown 表格）----------
    def _disc_backend(self):
        """惰性创建并复用判别式后端（失败只尝试一次，之后返回 None）。"""
        if self._disc is None:
            try:
                self._disc = DiscriminativeBackend()
            except Exception as e:
                _logger.warning("判别式后端不可用（表格重建将回退）: %s", e)
                self._disc = False
        return self._disc or None

    def _table_and_disc_text(self, item: InputItem):
        """返回 ``(表格 Markdown, 判别式全文)``，任一不可得则为 ``""``。

        无缓存坐标时才调用判别式后端补取（表格模式）——一次拿到“文本+坐标”。
        任何异常都吞掉并返回空串，确保表格模式绝不“报错”。
        """
        lines = getattr(item, "lines", None)
        W = getattr(item, "img_w", 0) or 0
        H = getattr(item, "img_h", 0) or 0
        disc_text = ""
        if not lines:
            try:
                disc = self._disc_backend()
                if disc is not None and disc.available():
                    with_lines = getattr(disc, "ocr_image_with_lines", None)
                    if with_lines is not None:
                        disc_text, data = with_lines(item.image_path)
                    else:  # 兼容只有 ocr_image 的后端
                        disc_text = disc.ocr_image(item.image_path)
                        data = disc.ocr_lines(item.image_path) or {}
                    if data:
                        lines = data.get("lines")
                        W = data.get("img_w") or 0
                        H = data.get("img_h") or 0
            except Exception as e:
                _logger.warning("table layout: 判别式行框不可用 %s: %s", item.name, e)
        if not lines:
            return "", disc_text
        try:
            return build_table_from_lines(lines, W, H), disc_text
        except Exception as e:
            _logger.warning("table layout: 重建失败 %s: %s", item.name, e)
            return "", disc_text

    def _table_result(self, item: InputItem, text: str) -> str:
        """表格模式：优先坐标重建真表格；否则回退「键值→表」；再否则保留全文。

        回退时优先用判别式全文（比可能近空的 VLM 输出可靠），再退回流水线文本。
        """
        tbl, disc_text = self._table_and_disc_text(item)
        if tbl:
            return tbl
        base = disc_text or text
        try:
            return kv_text_to_markdown_table(base)
        except Exception as e:
            _logger.warning("表格模式：键值->表失败 %s: %s", item.name, e)
            return base

    def _maybe_layout_table(self, item: InputItem, text: str) -> str:
        """Markdown 模式：页面若为明确的对照/表格布局（多数行含 >=2 格），自动还原为表格。

        只用流水线已缓存的坐标（判别式跑过才有），不额外 OCR；不满足表格特征则原样返回。
        """
        lines = getattr(item, "lines", None)
        if not lines:
            return text
        try:
            tbl = build_table_from_lines(lines, getattr(item, "img_w", 0) or 0,
                                         getattr(item, "img_h", 0) or 0)
        except Exception:
            return text
        return tbl or text
