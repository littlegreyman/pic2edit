"""统一 OCR 流水线：判别式优先 + VLM 兜底 + 版面感知 + 表格结构化。

取代原先“每个 InputItem 直接丢给 VLM”的做法，从根上消除生成式幻觉：
- 干净印刷体 -> RapidOCR（零幻觉，纯 CPU 即可，不依赖 GPU 引擎）
- 判别式失败/空/低置信 -> DeepSeek-OCR / Ollama / Transformers（作为兜底）
- 表格区域（mode=table 且表格识别依赖可用）-> pix2text-table-rec

关键收益：即使不拉起 llama-server，也能产出可编辑文件（只是“智能页”质量下降）。

GUI 现在通过 OcrWorker -> 本流水线处理每个图像项，因此“零幻觉”设计对 GUI 同样
生效。早期 GUI 曾直接把 VLM 当唯一后端（见 gui._build_backend / worker），导致干净
中文页也被迫走会幻觉的 VLM，混入英文 boilerplate（如“美国宪法第一修正案”）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from utils.input_prep import InputItem  # noqa: E402  (used as doc/type reference)
from backends.discriminative_backend import DiscriminativeBackend  # noqa: E402
from utils.logging_setup import get_logger  # noqa: E402

_logger = get_logger("ocr_pipeline")

ZH_PROMPT = ("<|grounding|>请逐字识别图中的中文文本，保留原意与版面，只输出原文对应的 "
             "Markdown，不要翻译、不要改写、不要补充英文。")

# VLM 兜底输出若命中这些英文 boilerplate，即可判定为生成式幻觉（与中文文档无关），应丢弃。
# 覆盖本项目已观测到的“美国宪法第一修正案”等典型注入（“Congress shall make no law” /
# “First Amendment” 均在内）。
_HALLUCINATION_MARKERS = [
    "congress shall make no law",
    "first amendment",
    "shall not be infringed",
    "establishment of religion",
    "freedom of speech",
    "freedom of the press",
    "the constitution of the united states",
    "we the people",
    "pursuant to the constitution",
    "in the united states of america",
    "all men are created equal",
    "life, liberty and the pursuit",
]


def _vlm_backend(url, model):
    try:
        from backends.llamacpp_backend import LlamaCppBackend
        b = LlamaCppBackend(url, model)
        return b if b.available() else None
    except Exception:
        return None


def _quality_ok(md):
    if not md or len(md.strip()) < 5:
        return False
    han = sum(1 for c in md if '\u4e00' <= c <= '\u9fff')
    lat = sum(1 for c in md if c.isascii() and c.isalpha())
    return (han + lat) >= 5


def _looks_hallucinated(md):
    """VLM 兜底输出疑似生成式幻觉（语义注入）时返回 True。

    仅命中已知英文 boilerplate 才判为幻觉——精确、不会对正常英文文档误伤。
    """
    if not md:
        return False
    low = md.lower()
    for m in _HALLUCINATION_MARKERS:
        if m in low:
            _logger.warning("hallucination marker hit: %r", m)
            return True
    return False


def run(items, vlm_backend=None, server_url="http://localhost:8080", model="deepseek-ocr",
        use_vlm_fallback=True, mode="markdown", skip_discriminative=False,
        prompt=None, guard_hallucination=True):
    """填充每个 InputItem.result（Markdown）。返回 items。

    vlm_backend: 兜底用的 VLM 后端实例（如 LlamaCppBackend / OllamaBackend /
        TransformersBackend）。为 None 时回退到按 server_url/model 构造的
        LlamaCppBackend。use_vlm_fallback=False 则纯判别式（无 GPU 也可出文件）。
    mode="table" 会尝试用表格识别器处理表格区域（需 pix2text 依赖）。
    skip_discriminative: 跳过判别式后端（RapidOCR）。用于内存受限环境
        （如 WorkBuddy 沙箱 ulimit -v=4GB），此时 onnxruntime 加载模型会被
        OOM killer 连父进程一起 SIGKILL(137)；直接走 VLM 兜底（需本机 :8080）。
    """
    disc = None
    if not skip_discriminative:
        try:
            disc = DiscriminativeBackend()
        except Exception:
            disc = None

    # 表格识别（阶段4，可选依赖）
    table_rec = None
    if mode == "table":
        try:
            from backends.table_backend import TableRecognizer
            tr = TableRecognizer()
            table_rec = tr if tr.available() else None
        except Exception:
            table_rec = None

    vlm = None
    if use_vlm_fallback:
        if vlm_backend is not None:
            vlm = vlm_backend if vlm_backend.available() else None
        else:
            vlm = _vlm_backend(server_url, model)

    # VLM 实际使用的提示词：优先用调用方传入的（来自 GUI 所选输出格式模板），
    # 为空则回退到默认中文指令。这样“图表/公式/纯文本”等输出格式才能真正影响 VLM。
    vlm_prompt = prompt if (prompt and prompt.strip()) else ZH_PROMPT

    for it in items:
        md = ""
        # 表格模式：优先表格识别
        if table_rec is not None:
            try:
                t = table_rec.recognize(it.image_path)
                if t and t.strip():
                    md = t
            except Exception:
                md = ""
        # 判别式优先（零幻觉，干净中文印刷页在此即结束，VLM 不会被触发）
        if not _quality_ok(md) and disc is not None and disc.available():
            md = disc.ocr_image(it.image_path)
        # VLM 兜底（仅当判别式未给出合格结果）
        if not _quality_ok(md) and vlm is not None:
            try:
                vmd = vlm.ocr_image(it.image_path, vlm_prompt)
                if vmd and (not guard_hallucination or not _looks_hallucinated(vmd)):
                    md = vmd
                else:
                    # 兜底输出疑似幻觉：丢弃，保留判别式结果（哪怕为空），绝不把幻觉写进文档
                    _logger.warning("VLM fallback output discarded as hallucination for %s", it.name)
            except Exception:
                md = md or ""
        it.result = md
        it.status = "完成" if md else "失败"
    return items
