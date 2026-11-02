"""判别式 OCR 后端（RapidOCR >=3.10）—— 默认引擎，DeepSeek-OCR 仅作兜底。

- 开发态 / 运行态：RapidOCR 已随主 venv（ocr_app/.venv，Python 3.14）安装，
  通过子进程调用 _rapidocr_cli.py（用当前解释器 sys.executable 运行，无需独立 .venv_ocr）。
- 冻结态（PyInstaller）：无法可靠地用 ``sys.executable script.py`` 重新拉起
  子进程（单文件夹/单文件构建下行为不稳定），改为 **进程内** 直接调用
  rapidocr（已随包收集，模型在打包的 rapidocr_models/ 目录）。
  若进程内引擎初始化失败，available() 返回 False，由 ocr_pipeline 的
  try/except 降级到 VLM 兜底，不会崩溃。
"""
import os
import sys
import json
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OCR_VENV = os.path.join(HERE, "..", ".venv_ocr")
CLI = os.path.join(HERE, "..", "_rapidocr_cli.py")


def _frozen():
    return getattr(sys, "frozen", False)


def _rapidocr_models_dir():
    """冻结态打包的 RapidOCR onnx 模型目录；不存在则返回 None。"""
    try:
        from utils.app_home import resource_path
    except Exception:
        return None
    d = resource_path("rapidocr_models")
    return d if os.path.isdir(d) else None


def _lines_from_output(result, image_path):
    """把 RapidOCROutput 对象转成统一 lines 结构。

    result: rapidocr 的 RapidOCROutput（.boxes 为 (N,4,2) numpy，.txts/.scores 为 tuple）。
    空结果（txts=None / len==0）返回 {"lines": [], ...}。
    """
    if result is None or getattr(result, "txts", None) is None or len(result) == 0:
        return {"lines": [], "img_w": 0, "img_h": 0}
    import numpy as np
    from PIL import Image
    boxes = np.asarray(result.boxes)
    lines = []
    W = H = 0
    for i, (text, score) in enumerate(zip(result.txts, result.scores)):
        box = boxes[i]
        ys = box[:, 1]
        xs = box[:, 0]
        lines.append({
            "text": text,
            "y0": float(min(ys)), "y1": float(max(ys)),
            "x0": float(min(xs)), "x1": float(max(xs)),
            "score": round(float(score), 3),
        })
    try:
        with Image.open(image_path) as im:
            W, H = im.size
    except Exception:
        pass
    return {"lines": lines, "img_w": W, "img_h": H}

# 章节/节 标题（独立成段；标题内各检测框以两个空格相接）
_HEADING_RE = re.compile(r"^第[0-9一二三四五六七八九十百千]+[章节目]")
# 条文（同段：条文号 + 两个空格 + 正文）
_ARTICLE_RE = re.compile(r"^第[0-9一二三四五六七八九十百千]+条")
# 任一结构标题（章/节/条/目）都另起一段
_STRUCT_RE = re.compile(r"^第[0-9一二三四五六七八九十百千]+[章节目条]")
# 规整：章/节/条/目 号后若紧跟非空白则补两个空格（幂等）
_NORM_RE = re.compile(r"^(第[0-9一二三四五六七八九十百千]+[章节目条])(?=\S)")


def _norm_spacing(text):
    """条文号 / 章节号后若紧跟非空白字符，补两个空格（符合公文排版，且幂等）。"""
    return _NORM_RE.sub(lambda m: m.group(1) + "  ", text)


def _group_rows(lines):
    """按 y 波段把检测框聚成视觉行；行内按 x0 从左到右（修“第一章/总则”顺序）。

    返回 [{"text","y0","y1"}]，已按阅读顺序排列。
    """
    if not lines:
        return []
    hs = [max(l["y1"] - l["y0"], 1) for l in lines]
    band = max(6.0, 0.6 * sorted(hs)[len(hs) // 2])  # 行高约 60% 作为同行判定带宽
    rows = {}
    for l in lines:
        rows.setdefault(round(l["y0"] / band), []).append(l)
    ordered = []
    for key in sorted(rows):
        grp = sorted(rows[key], key=lambda l: l["x0"])
        if _HEADING_RE.match(grp[0]["text"].strip()):
            txt = "  ".join(g["text"].strip() for g in grp)
        else:
            txt = "".join(g["text"].strip() for g in grp)
        ordered.append({
            "text": txt,
            "y0": min(g["y0"] for g in grp),
            "y1": max(g["y1"] for g in grp),
        })
    return ordered


def assemble_markdown(lines, H=0):
    """将判别式 OCR 的逐行结果重组为段落，消除误换行与顺序错乱。

    - 先按 y 波段聚成视觉行，行内按 x0 从左到右（修“第一章/总则”顺序）
    - 任何结构标题（第X章/节/条/目）都另起一段（章/节标题独立成段，
      条文号与正文同行）
    - 行间距 <= ~0.8 行高 -> 同一段落（中文连续拼接，不插空格）
    - 条文号 / 章节号后补两个空格
    - 段落之间用空行分隔
    """
    rows = _group_rows(lines)
    if not rows:
        return ""
    paras = []
    cur = [rows[0]]
    for prev, nxt in zip(rows, rows[1:]):
        # 任何结构标题都另起一段：直接断段
        if _STRUCT_RE.match(nxt["text"]):
            paras.append(cur)
            cur = [nxt]
            continue
        gap = nxt["y0"] - prev["y1"]
        h = max(max(prev["y1"] - prev["y0"], 1), max(nxt["y1"] - nxt["y0"], 1))
        if gap <= 0.8 * h:
            cur.append(nxt)
        else:
            paras.append(cur)
            cur = [nxt]
    paras.append(cur)

    out = []
    for p in paras:
        texts = [r["text"] for r in p if r["text"].strip()]
        if not texts:
            continue
        # 段落内所有行连续拼接，再统一规整条文/章节号后的两个空格
        out.append(_norm_spacing("".join(texts)))
    return "\n\n".join(out)


def _pick_python():
    """选取运行 _rapidocr_cli.py 的解释器。

    优先用当前解释器（ocr_app/.venv，Python 3.14），前提是 rapidocr 可导入；
    否则回退到独立的 .venv_ocr（若仍保留）。都不可用则返回 None。
    """
    try:
        import rapidocr  # 新 rapidocr 支持 3.14，随主 venv 安装
        return sys.executable
    except Exception:
        pass
    if os.name == "nt" or sys.platform.startswith("win"):
        cand = os.path.join(OCR_VENV, "Scripts", "python.exe")
        return cand if os.path.exists(cand) else None
    for name in ("python3", "python"):
        cand = os.path.join(OCR_VENV, "bin", name)
        if os.path.exists(cand):
            return cand
    return None


class DiscriminativeBackend:
    name = "rapidocr"

    def __init__(self):
        self._py = None        # 开发态子进程解释器
        self._engine = None    # 冻结态进程内引擎
        if _frozen():
            # 冻结态：进程内直接调用 RapidOCR（无法可靠地重新拉起子进程）
            try:
                from rapidocr import RapidOCR
                kwargs = {}
                models = _rapidocr_models_dir()
                if models:
                    # 新 rapidocr 通过 params 指定模型根目录（目录内放 det/rec/cls onnx）
                    kwargs["params"] = {"Global.model_root_dir": models}
                self._engine = RapidOCR(**kwargs)
            except Exception as e:
                raise RuntimeError(
                    "判别式 OCR(RapidOCR) 进程内加载失败：%s" % e
                )
        else:
            self._py = _pick_python()
            if self._py is None:
                raise RuntimeError(
                    "判别式 OCR 子环境缺失：请在本 venv 安装 rapidocr"
                    "（pip install rapidocr onnxruntime），或创建 ocr_app/.venv_ocr 并安装 rapidocr"
                )

    def available(self) -> bool:
        return self._py is not None or self._engine is not None

    def _raw(self, image_path):
        if self._engine is not None:
            return self._raw_inprocess(image_path)
        out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        out.close()
        try:
            r = subprocess.run([self._py, CLI, image_path, out.name],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return None
            with open(out.name, encoding="utf-8") as f:
                return json.load(f)
        finally:
            try:
                os.unlink(out.name)
            except Exception:
                pass

    def _raw_inprocess(self, image_path):
        try:
            result = self._engine(image_path)
        except Exception:
            return None
        return _lines_from_output(result, image_path)

    def ocr_image(self, image_path, prompt=None) -> str:
        """返回 Markdown 文本（阅读顺序拼接，已剔除页眉/页脚/页码）。识别失败返回空串。"""
        data = self._raw(image_path)
        if not data or "error" in data:
            return ""
        lines = data.get("lines") or []
        if not lines:
            return ""
        H = data.get("img_h") or 0
        # 版面感知（阶段3）：剔除页眉(<4%H)、页脚(>96%H) 与纯页码行，避免噪声串入
        keep = []
        for ln in lines:
            cy = (ln["y0"] + ln["y1"]) / 2.0
            if H and (cy < 0.04 * H or cy > 0.96 * H):
                continue
            if re.fullmatch(r"\s*\d+\s*/\s*\d+\s*", ln["text"]):
                continue
            keep.append(ln)
        return assemble_markdown(keep, H)
