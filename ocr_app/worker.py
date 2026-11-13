"""后台 OCR 工作线程：避免界面卡顿。"""
import os

from PySide6.QtCore import QThread, Signal

from backend_api import OCRBackend, DiscriminativeBackend
from utils.input_prep import InputItem
from utils.logging_setup import get_logger, log_exception
from utils.table_utils import kv_text_to_markdown_table
from utils.table_layout import build_table_from_lines
from backend_api import run as pipeline_run, RoutingPolicy, vmem_limited_gb, env_force_skip
from backend_api import MODE_AUTO, apply_schemas, detect_handwriting

_logger = get_logger("worker")


class OcrWorker(QThread):
    progress = Signal(int, int, str)   # 当前序号, 总数, 名称
    item_done = Signal(int, str)        # 序号, 文本
    worker_finished = Signal()
    error = Signal(str)

    def __init__(self, backend: OCRBackend, items: list, prompt: str, indices: list,
                 as_table: bool = False, prompt_template: str = "markdown",
                 model_params=None, schema_mode: str = MODE_AUTO):
        super().__init__()
        self.backend = backend
        self.items = items
        self.prompt = prompt
        self.indices = indices
        # 表格模式：OCR 后把“键：值”文本转成 Markdown 表格
        self.as_table = as_table
        self.prompt_template = prompt_template
        self.model_params = model_params
        # 凭证 schema 三态（none / auto / <id>）。默认 auto 与改写前行为等价：
        # 改写前对每页无条件跑一次发票判定，auto 的语义正好是"逐页判定"。
        self.schema_mode = schema_mode or MODE_AUTO
        self._disc = None  # 表格重建复用的判别式后端（冻结态进程内模型只加载一次）
        # 输出格式真正生效：
        # - 表格/图表/HTML表格 模式跳过判别式（RapidOCR 会压平/漏识别表格与图，交由 VLM 原生输出）；
        # - 图表/公式模式下关闭英文 boilerplate 幻觉守卫（公式/化学符号等含拉丁字符属正常）。
        # 路由策略收敛到共享内核 RoutingPolicy：页面类型决定「是否跳过判别式」
        # 与「是否启用幻觉守卫」，GUI 与 Skill 共用同一套规则（修复两侧路由不一致）。
        # 路由入参与 Skill 对齐：除页面类型外，统一注入运行环境信息（虚拟内存软限制、
        # 强制跳过环境变量），确保沙箱/受限环境下两侧路由一致。
        #
        # 手写预筛**逐页**做（见 _policy_for）：此前 GUI 硬编码 hw_detected=False，
        # 而 skill 用真实预筛结果，于是同一张手写件从两个入口进来走不同分支。
        self._static_policy = RoutingPolicy.for_page_type(
            "table" if as_table else prompt_template,
            force_skip=env_force_skip(),
            vmem_limited_gb=vmem_limited_gb(),
            hw_detected=False,
        )

    # ---------- 逐页路由 ----------
    @staticmethod
    def _hw_force() -> bool:
        """手写预筛的显式开关（默认关闭，与 skill 的 settings 默认一致）。

        关闭不是因为做不出来，而是因为独立盲测的误报率太高（印刷 vs 手写
        65% 误报），盲开会把大量正常印刷件推去走会幻觉的 VLM——代价远大于收益。
        """
        return os.environ.get("PIC2EDIT_HW_FORCE", "").lower() in ("1", "true", "yes")

    def _detect_hw(self, item: InputItem) -> bool:
        if not self._hw_force():
            return False
        path = getattr(item, "image_path", "") or ""
        if not path or path.lower().endswith((".pdf", ".tif", ".tiff")):
            return False       # 多页影像不适用单张预筛，保守按印刷
        try:
            hw, conf = detect_handwriting(path)
            _logger.info("hw screen: %s -> %s (conf=%.2f)", item.name, hw, conf)
            return bool(hw)
        except Exception as e:   # 缺 cv2 / 图片异常 → 保守按印刷
            _logger.warning("hw screen failed (%s): %s", item.name, e)
            return False

    def _policy_for(self, item: InputItem):
        """按本页的手写预筛结果取路由策略（无预筛时直接复用静态策略）。"""
        hw = self._detect_hw(item)
        if not hw:
            return self._static_policy
        return RoutingPolicy.for_page_type(
            "table" if self.as_table else self.prompt_template,
            force_skip=env_force_skip(),
            vmem_limited_gb=vmem_limited_gb(),
            hw_detected=True,
        )

    def run(self):
        total = len(self.indices)
        _logger.info("OCR job started: %d item(s)%s",
                     total, "（表格模式）" if self.as_table else "")
        for n, idx in enumerate(self.indices, 1):
            item: InputItem = self.items[idx]
            item.status = "转换中"
            # 清掉上一轮遗留的行框坐标：同一 item 反复转换时，若本轮走 VLM，
            # _maybe_layout_table 会拿旧坐标重建出与当前结果无关的表格。
            item.lines = None
            item.img_w = 0
            item.img_h = 0
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
                    policy=self._policy_for(item),
                    # 把 GUI 所选输出格式的提示词真正传给 VLM（之前被硬编码 ZH_PROMPT 屏蔽）
                    prompt=self.prompt,
                    # 把配置化的模型采样参数传给 VLM（llama.cpp 在请求 payload 中生效）
                    model_params=self.model_params,
                )
                text = item.result
                raw_for_schema = text or ""
                if self.as_table:
                    text = self._table_result(item, text)
                else:
                    text = self._maybe_layout_table(item, text)
                item.result = text
                self._apply_schema(item, raw_for_schema)
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

    # ---------- 凭证 schema ----------
    def _apply_schema(self, item: InputItem, raw_text: str) -> None:
        """按 schema 三态抽取本页字段，挂到 item 上供导出使用。

        抽的是**表格化之前**的原始文本（``raw_text``）：表格模式会把"键：值"压成
        表格，而 schema 要的是字段语义，压平后反而更难抽。
        任何异常都只降级（本页当作未命中），绝不让 schema 把整次转换拖垮。
        """
        if self.schema_mode == "none":
            return
        page = {
            "page": 1,
            "text": raw_text,
            "raw_text": raw_text,
            "lines": getattr(item, "lines", None) or [],
            "img_w": getattr(item, "img_w", 0) or 0,
            "img_h": getattr(item, "img_h", 0) or 0,
        }
        try:
            apply_schemas([page], self.schema_mode)
        except KeyError as e:
            # 配置里存了一个已经被删掉的 schema id：降级为不启用，并提示一次。
            _logger.warning("schema 选择无效，本次改为不启用: %s", e.args[0])
            self.schema_mode = "none"
            return
        except Exception as e:      # 单个 schema 抛错不应影响识别结果
            _logger.warning("schema 抽取失败（%s）: %s", item.name, e)
            return
        item.schema = page.get("schema")
        item.schema_id = page.get("schema_id")
        item.schema_version = page.get("schema_version")
        if item.schema_id:
            _logger.info("schema hit: %s -> %s v%s", item.name, item.schema_id,
                         item.schema_version or "?")

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
        # 把补取到的坐标回写到 item：schema 抽取（尤其是明细表）也在等这份坐标，
        # 不回写就等于同一页为了不同目的各跑一次 RapidOCR。
        item.lines = lines
        item.img_w = W
        item.img_h = H
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
