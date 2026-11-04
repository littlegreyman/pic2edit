#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pic2edit_convert.py — 把图片/PDF 转成可编辑文件（Markdown/Word/纯文本/JSON）。

设计目标：作为 pic2edit-ocr skill 的核心 helper，完全脱离 GUI 运行。
流程（两层引擎选择）：
  0) P0 手写/印刷预筛（纯视觉启发式，无模型，**默认关闭**）：手写体 → 直接走 VLM
     （跳过 RapidOCR）；印刷体 → 进入第 1~ 步的「判别式为主 + VLM 兜底」分支。
     关闭原因见下方路由注释（独立盲测误报 65%，判据本身不可分）与报告
     `handwriting_screen_risk_report.md`；需要时用 PIC2EDIT_HW_FORCE=1 显式启用；
  1) 把项目 ocr_app 加入 sys.path，复用项目已有的 OCR / 导出能力；
  2) 确保本地 llama.cpp 服务可用（VLM 兜底确需且判别式不可用/被跳过/启用兜底时拉起）；
  3) prepare_input 拆 PDF / 多页 TIF 为图像项；
  4) 逐项走统一流水线（判别式优先 + VLM 兜底 + 版面/表格感知）；
  5) 通过「探针包装」记录每页使用的引擎（disc / vlm / table），用于无幻觉溯源；
  6) 全文重复护栏：截断 VLM/判别式偶发的整段循环重复，避免噪声入库；
  7) 按 --format 导出 md / txt / docx / json，落到输出路径并打印。

新增能力（相对原版，均在 skill 层、不修改 pic2edit 项目）：
  --cache       按「文件内容哈希 + mode + 版本」缓存识别结果，重复预处理秒回；
  --prov        在 md/txt/docx 输出中为每个页面加 `<!-- engine:xxx -->` 溯源注释；
  --format json 输出结构化 JSON（含每页 text 与 engine），便于入库一致性校验。

用法：
  python pic2edit_convert.py <输入图片/PDF> [--format md|docx|txt|json]
                              [--mode markdown|ocr|figure|table]
                              [--url http://localhost:8080]
                              [--gpu-layers N] [--out OUT_PATH] [--stop-server]
                              [--cache] [--prov]

注意：
  - 引擎默认常驻（转换后不退出），便于后续调用秒开；传 --stop-server 可在本次拉起后退出。
  - 容器无 GPU 时传 --gpu-layers 0 走 CPU。
  - 设置 PIC2EDIT_SKIP_DISCRIMINATIVE=1 可强制跳过 RapidOCR 判别式（仅 VLM 兜底，需本机 :8080）。
"""
import argparse
import os
import re
import sys
import time
import subprocess
import tempfile
import hashlib
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # 确保同目录下的 invoice_render 可被导入（纯 stdlib，无 pic2edit 依赖）


def _find_ocr_app():
    """定位 pic2edit 的 ocr_app 目录（含 run_llamacpp_server.py）。

    解析优先级：
      1) 环境变量 PIC2EDIT_OCR_APP / PIC2EDIT_ROOT（显式指路，最可靠）
      2) 从本脚本位置向上查找（适用于技能随项目放置的情况）
      3) 本机已知的固定项目路径兜底（适用于技能装为「用户级」、
         脚本位于 ~/.workbuddy/skills/... 时，向上找不到仓库）
    """
    env = os.environ.get("PIC2EDIT_OCR_APP")
    if not env:
        root = os.environ.get("PIC2EDIT_ROOT")
        if root:
            env = os.path.join(root, "ocr_app")
    if env and os.path.isfile(os.path.join(env, "run_llamacpp_server.py")):
        return env
    # 向上查找（技能随项目放在 <repo>/.workbuddy/skills/... 时命中）
    cur = HERE
    for _ in range(6):
        if os.path.isfile(os.path.join(cur, "run_llamacpp_server.py")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    # 3) 常见用户目录候选（跨平台，含本地化“文档”目录）
    home = os.path.expanduser("~")
    xdg_docs = (os.environ.get("XDG_DOCUMENTS_DIR") or "").rstrip("/")
    doc_dirs = [os.path.join(home, "Documents"),
                os.path.join(home, "文档")]
    if xdg_docs:
        doc_dirs.append(xdg_docs)
    rel_roots = [
        "pic2edit/ocr_app",
        "pic2edit/pic2edit/ocr_app",   # 嵌套 clone 形态
        "projects/pic2edit/ocr_app",
        "ocr_app",
    ]
    for doc in doc_dirs:
        for rel in rel_roots:
            cand = os.path.join(doc, rel)
            if os.path.isfile(os.path.join(cand, "run_llamacpp_server.py")):
                return cand
    # 也允许项目直接放在 home 下
    for rel in rel_roots:
        cand = os.path.join(home, rel)
        if os.path.isfile(os.path.join(cand, "run_llamacpp_server.py")):
            return cand

    # A5 修复：不再回退到一个几乎必错的相对路径，改为明确报错，
    # 引导用户设置 PIC2EDIT_OCR_APP / PIC2EDIT_ROOT。
    raise RuntimeError(
        "找不到 pic2edit 的 ocr_app 目录。请设置环境变量 "
        "PIC2EDIT_OCR_APP 指向 .../ocr_app，或 PIC2EDIT_ROOT 指向项目根目录。"
    )


OCR_APP = _find_ocr_app()
if OCR_APP not in sys.path:
    sys.path.insert(0, OCR_APP)

from utils.input_prep import prepare_input                      # noqa: E402
from utils.export_docx import save_text, md_to_docx              # noqa: E402
from utils.table_utils import kv_text_to_markdown_table         # noqa: E402
from backends.llamacpp_backend import LlamaCppBackend           # noqa: E402
from backends.discriminative_backend import DiscriminativeBackend  # noqa: E402
from backends.ocr_pipeline import run as pipeline_run
from backends.routing import RoutingPolicy                          # noqa: E402               # noqa: E402
import run_llamacpp_server as launcher                           # noqa: E402

# 发票经典版式还原（schema 驱动，纯 stdlib，无 pic2edit 依赖）
from invoice_render import (  # noqa: E402
    _invoice_fragment, invoice_title_text, INVOICE_CSS, PROVINCE_NAMES,
)

# 手写/印刷预筛（P0 路由闸，纯 numpy + cv2 无模型；MIT 友好，独立重新实现）
from handwriting_screen import detect_handwriting  # noqa: E402

# skill 层基础设施：设置 / 原始 OCR 日志 / 按显存选模型（均纯 stdlib）
import settings_store  # noqa: E402
import log_store  # noqa: E402
import model_select  # noqa: E402

PORT = "8080"
READY_TIMEOUT = 240.0
# 缓存只存"识别结果"（raw_text/lines/engine），渲染与 schema 抽取在读取时进行，
# 因此渲染主干重构无需清缓存；命中时会补做重复护栏与 schema 抽取。
CACHE_VERSION = "v2"


# --------------------------------------------------------------------------
# 新增工具函数（skill 层，无 ocr_app 依赖）
# --------------------------------------------------------------------------
def _sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def _cache_file(key):
    d = os.path.join(tempfile.gettempdir(), "pic2edit_cache")
    os.makedirs(d, exist_ok=True)
    # key 可能含 Windows 非法文件名字符（如 |），用其哈希作文件名，避免 OSError
    return os.path.join(d, hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")


# --------------------------------------------------------------------------
# 密码区 / 防伪底纹掩膜预处理（skill 层，不动 pic2edit）
# --------------------------------------------------------------------------
def _parse_mask_rect(rect_str):
    """解析 PIC2EDIT_MASK_RECT（x0,y0,x1,y1 比例）为元组；非法/缺失返回 None。"""
    if not rect_str:
        return None
    try:
        x0, y0, x1, y1 = [float(v) for v in rect_str.split(",")]
    except Exception:
        sys.stderr.write(f"[warn] PIC2EDIT_MASK_RECT 解析失败: {rect_str!r}，忽略掩膜\n")
        return None
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        sys.stderr.write(f"[warn] PIC2EDIT_MASK_RECT 越界: {rect_str!r}，忽略掩膜\n")
        return None
    return (x0, y0, x1, y1)


def _mask_copy(src, dst, rect):
    """把 src 中 rect 比例区域涂白后存为 dst（PNG）。依赖 PIL，缺失则抛错由调用方兜底。"""
    from PIL import Image, ImageDraw
    im = Image.open(src).convert("RGB")
    W, H = im.size
    x0, y0, x1, y1 = rect
    box = (int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H))
    ImageDraw.Draw(im).rectangle(box, fill=(255, 255, 255))
    im.save(dst)


class _VlmProbe:
    """包装 VLM 后端，记录哪些图像真正走了 VLM 兜底，从而拿到逐页溯源。

    仅依赖 ocr_app 已暴露的 LlamaCppBackend 接口（available / ocr_image），
    不改变 ocr_app 任何逻辑。
    """

    def __init__(self, url, model):
        self._real = LlamaCppBackend(url.rstrip("/"), model)
        self.called = set()

    def available(self):
        try:
            return self._real.available()
        except Exception:
            return False

    def ocr_image(self, image_path, prompt=None):
        self.called.add(image_path)
        return self._real.ocr_image(image_path, prompt)


def _table_available():
    try:
        from backends.table_backend import TableRecognizer
        tr = TableRecognizer()
        return bool(tr.available())
    except Exception:
        return False


def _vmem_limited_gb():
    # `resource` 是 Unix 专属模块；Windows 上不可用，按「无虚拟内存限制」处理
    # （即不自动跳过判别式，由后续逻辑按可用性决定）。
    try:
        import resource as _rs
        soft, _ = _rs.getrlimit(_rs.RLIMIT_AS)
    except Exception:
        return 0
    if soft == _rs.RLIM_INFINITY:
        return 0
    return soft / (1024 ** 3)


def ensure_server(url, gpu_layers, model_files=None):
    """若服务未就绪则拉起 llama.cpp 服务；返回 (backend, proc_or_None)。

    Args:
        url: 服务地址。
        gpu_layers: 强制 GPU 层数；None 表示交给 llama.cpp 自行决定。
        model_files: ``(model_path, mmproj_path)``，由 :mod:`model_select` 按显存选定。
            **只替换传给 pic2edit ``build_command()`` 的两个权重文件名**，不修改
            pic2edit 的任何代码；为 None 或某项为空时沿用它的 ``discover()`` 默认。
    """
    base = url.rstrip("/")
    backend = LlamaCppBackend(base, "deepseek-ocr")
    if backend.available():
        # 已有服务在跑：无法确认它加载的是哪个量化，提示一句便于排查精度差异
        sys.stderr.write("[model] 检测到已有服务在运行，沿用其已加载的模型（本次不切换）\n")
        return backend, None
    exe, model, mmproj = launcher.discover()
    if model_files:
        _m, _mp = model_files
        if _m:
            model = _m
        if _mp:
            mmproj = _mp
    if not exe:
        sys.stderr.write("[error] 未找到 llama-server，请先运行 download_models 部署引擎。\n")
        sys.exit(1)
    if not model:
        sys.stderr.write("[error] 未找到 GGUF 模型，请先下载模型权重。\n")
        sys.exit(1)
    cmd = launcher.build_command(exe, model, mmproj)
    if gpu_layers is not None:
        cmd += ["--n-gpu-layers", str(gpu_layers)]
    print(f"[launch] {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.Popen(cmd, cwd=OCR_APP,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.stderr.write(f"[error] 引擎进程异常退出，code={proc.poll()}\n")
            sys.exit(1)
        if backend.available():
            print("[ok] 引擎就绪", file=sys.stderr)
            return backend, proc
        time.sleep(2)
    sys.stderr.write("[error] 引擎在超时内未就绪（检查 CUDA / 模型路径）\n")
    sys.exit(1)


def _safe_path(p):
    """把传入路径规范化为绝对路径；同时兼容 WorkBuddy 附件的相对/带协议写法。"""
    p = p.strip()
    if p.startswith("file://"):
        p = p[len("file://"):]
    return os.path.abspath(p)


def _classify_engine(it, mode, skip_disc, disc_ok, table_avail, vlm_called):
    # 溯源必须基于“VLM 是否真的被调用”，而非 skip_disc 标志，
    # 否则强制跳过判别式时会被错误地一律标成 vlm（实际 VLM 可能根本没跑）。
    if mode == "table" and table_avail:
        return "table"
    if it.image_path in vlm_called:
        return "vlm"
    if disc_ok and not skip_disc:
        return "disc"
    return "unknown"


def _trim_repetition_lines(text, min_reps=3, max_distinct=4, min_tail_lines=16):
    """行级循环检测（模型循环的真实形态：少数几行被反复吐出，末尾常截断在行中）。

    为什么不能只做「严格周期」检测：真实 VLM 循环是**抖动**的——例如重庆票样尾部
    绝大多数是「普通/普通/普通/专用」，但个别块是「普通×4 + 专用」，周期长度不固定；
    而且最后一轮常被 token 上限截断成半行（"重庆"）。严格行周期与字符尾部对齐都会失配。

    因此改用「尾部词表收敛」判据：从末尾向前扫描，只要去重后的行内容种类不超过
    max_distinct 且累计行数达到 min_tail_lines，就认定尾部是循环；遇到第一种新内容
    即停止，其位置就是循环起点（真实前缀的边界）。

    Args:
        text: 原始 OCR 文本。
        min_reps: 同一行内容至少出现多少次才认同为"在循环"。
        max_distinct: 循环体允许的不同行内容上限（越小越保守）。
        min_tail_lines: 命中的尾部至少覆盖多少非空行，避免误伤正常发票。

    Returns:
        (clean_text, reps)；未检测到行级循环返回 None（交给字符级检测继续判断）。
    """
    body = text.rstrip("\n")
    lines = body.split("\n")
    m = len(lines)
    if m < min_tail_lines:
        return None

    stripped = [ln.strip() for ln in lines]
    distinct = set()
    counts: dict[str, int] = {}
    tail_start = m
    for i in range(m - 1, -1, -1):
        s = stripped[i]
        if not s:
            continue  # 空行忽略：既不进词表，也不终止判定
        if s not in distinct and len(distinct) >= max_distinct:
            tail_start = i + 1
            break
        distinct.add(s)
        counts[s] = counts.get(s, 0) + 1
        tail_start = i

    tail_len = sum(1 for i in range(tail_start, m) if stripped[i])
    if tail_len < min_tail_lines:
        return None
    if not any(c >= min_reps for c in counts.values()):
        return None

    prefix = "\n".join(lines[:tail_start]).rstrip()
    # 循环体只保留一份：按首次出现顺序取每个不同行一次，避免再次堆叠
    seen: set[str] = set()
    cycle_lines: list[str] = []
    for i in range(tail_start, m):
        s = stripped[i]
        if not s or s in seen:
            continue
        seen.add(s)
        cycle_lines.append(lines[i].rstrip())
    if not cycle_lines:
        return None
    reps = max(counts.values())
    out = (prefix + "\n" if prefix else "") + "\n".join(cycle_lines) + "\n[… 模型输出在此处发生重复循环，已自动截断 …]"
    return out, reps


def _trim_repetition(text, min_len=12, max_len=160, min_reps=3):
    """检测并截断模型输出的「整段循环重复」（几乎总发生在文末）。

    返回 (clean_text, repeat_count)。repeat_count>0 表示发生了截断。
    专治 VLM/判别式把同一段文本无限循环的故障（如重庆票样），避免噪声入库。

    两层策略（先行的更贴近真实故障形态）：
      1) 行级：以「行」为周期，允许末行被截断成半行——真实 VLM 循环几乎都是这种形态。
      2) 字符级：无换行或行内自循环时，从尾部按周期 p 对齐探测。

    两者都只截断「尾部循环」，保留真实前缀 + 一份循环体 + 显式标记；中途出现的
    循环（极罕见）不处理，避免误伤真实文本。
    """
    if not text:
        return text, 0

    # 先做行级（覆盖"末尾被截断在行中"的真实故障形态）
    _line_hit = _trim_repetition_lines(text, min_reps=min_reps)
    if _line_hit is not None:
        return _line_hit

    # 字符级兜底
    if len(text) < min_len * min_reps:
        return text, 0
    n = len(text)
    for p in range(min_len, max_len + 1):
        if n < p * min_reps:
            continue
        # 取末尾 min_reps 个连续单元（从最末一个往前数），全部相同才判定为尾部循环
        units = [text[n - (i + 1) * p : n - i * p] for i in range(min_reps)]
        a = units[0]
        if any(u != a for u in units):
            continue
        # 向前扩展，找到连续相同单元的最前位置（循环起点）
        start = n - p * min_reps
        while start - p >= 0 and text[start - p:start] == a:
            start -= p
        reps = (n - start) // p
        return text[:start] + a + "\n[… 模型输出在此处发生重复循环，已自动截断 …]", reps
    return text, 0


# ==========================================================================
# 发票结构化：密码区智能定位 + 表格/HTML 渲染（skill 层，仅读 pic2edit API）
# ==========================================================================
DEFAULT_VAT_RECT = (0.50, 0.20, 0.95, 0.44)  # 增值税专用/普通发票密码区通用预设


def _rapidocr_lines(image_path):
    """用 pic2edit 的判别式后端取带坐标的逐行结果（仅读 API，不改 pic2edit）。

    返回 (lines, W, H)：lines 每项含 text/x0/x1/y0/y1；失败返回 (None,0,0)。
    """
    try:
        backend = DiscriminativeBackend()
        if not backend.available():
            return None, 0, 0
        data = backend._raw(image_path)
    except Exception as e:
        sys.stderr.write(f"[warn] 判别式取带坐标行框失败: {e}\n")
        return None, 0, 0
    if not data:
        return None, 0, 0
    return data.get("lines") or [], data.get("img_w") or 0, data.get("img_h") or 0


def _is_gibberish(text):
    """密码区字符：多为数字/特殊符号，基本无汉字。"""
    if not text or len(text.strip()) < 4:
        return False
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return cjk == 0


def _find_password_label(lines):
    """找'密码区'竖排标签。支持整词识别或单字竖排堆叠两种情形。"""
    # 1) 整词识别
    exact = next((ln for ln in lines if "密码区" in ln["text"].replace(" ", "")), None)
    if exact:
        return exact
    # 2) 单字竖排：在偏右上区域寻找 密/码/区 字符，按纵向堆叠定位
    cand_chars = []
    for ln in lines:
        t = ln["text"].replace(" ", "")
        if len(t) == 1 and t in "密码区":
            cand_chars.append(ln)
    if len(cand_chars) >= 2:
        x1 = max(c["x1"] for c in cand_chars)
        y0 = min(c["y0"] for c in cand_chars)
        y1 = max(c["y1"] for c in cand_chars)
        return {"x1": x1, "y0": y0, "y1": y1}
    # 3) 含 密码 / 码区 的片段
    for ln in lines:
        t = ln["text"].replace(" ", "")
        if "密码" in t or "码区" in t:
            return ln
    return None


def _locate_password_region(image_path):
    """智能定位密码区：先找'密码区'竖排标签，取其右侧褐色矩形；用区域内文字是否
    均为数字/符号乱码验证；失败回退增值税通用预设。仅读 pic2edit 的 _raw。"""
    lines, W, H = _rapidocr_lines(image_path)
    if not (lines and W and H):
        return DEFAULT_VAT_RECT
    label = _find_password_label(lines)
    if label is None:
        return DEFAULT_VAT_RECT
    lx1, ly0, ly1 = label["x1"], label["y0"], label["y1"]
    h = max(ly1 - ly0, 1)
    x0 = min(W * 0.97, lx1 + 0.002 * W)
    # 褐色密码框宽约为竖排标签高的 3~4 倍（实测噪声框右缘约在 lx1+3.8h 处）
    x1 = min(W * 0.965, lx1 + h * 3.8)
    y0 = max(0, ly0 - 0.15 * h)
    # 不越过下方「税率/金额」表头，避免误伤货物明细表
    y1 = min(H * 0.405, ly1 + 0.15 * h)
    cx0, cy0, cx1, cy1 = x0 / W, y0 / H, x1 / W, y1 / H
    region = [ln["text"] for ln in lines
              if cx0 <= (ln["x0"] + ln["x1"]) / 2 / W <= cx1
              and cy0 <= (ln["y0"] + ln["y1"]) / 2 / H <= cy1]
    if region and sum(_is_gibberish(t) for t in region) / len(region) >= 0.5:
        return (cx0, cy0, cx1, cy1)
    return DEFAULT_VAT_RECT


# --------------------------------------------------------------------------
# 键名归一化：不同时期发票版本/底纹会导致近义键名（如“开票日期”vs“开票时间”、
# “货物或应税劳务、服务名称”vs“货物或服务名称”），语义无歧义应视为相同并提取。
# --------------------------------------------------------------------------
def _norm_key(s):
    """去空白与常见标点/间隔，便于近义键名匹配。"""
    s = (s or "").replace(" ", "").replace("\u00a0", "")
    for p in "、，,。.:：·—_~（）()[]【】%":
        s = s.replace(p, "")
    return s


def _is_item_header(text):
    """判断一行是否为货物明细表头。

    三种措辞都要认（同一套 schema 要通吃各省版式）：
      1. 老版专用发票的整句：`货物或应税劳务、服务名称`
      2. 新版/电子发票的简写：`项目名称`、`名称`
      3. 表头被拆成一排多框时，任一框命中 ≥3 个列名关键字

    前两条之外必须有兜底：电子发票的表头措辞改成"项目名称/规格型号/单位/…"后，
    只认"货物"会把整张票判成"无明细表"，明细直接为空——样本里 7/16 张就是这么丢的。
    """
    n = _norm_key(text)
    if not n or len(n) > 24 or "：" in n or ":" in n:
        return False
    # 「应税劳务」是发票明细表头独有的措辞（业务数据里不会出现），
    # 而它的前半截极容易被读歪——北京票读成「简物戒应税劳务名你」。
    # 只要出现这个词就足以判定是明细表头，不必强求「货物」二字也认对。
    if "应税劳务" in n:
        return True
    if ("货物" in n) and ("服务" in n or "名称" in n):
        return True
    if n in ("项目名称", "名称", "服务名称", "货物名称"):
        return True
    hits = sum(1 for k in ("名称", "规格", "型号", "单位", "数量", "单价",
                           "金额", "税率", "征收率", "税额", "项目") if k in n)
    return hits >= 3


# 明细表头可能出现的单元格词。合并表头（"单位数量"/"金额税额"）是这些词的任意拼接，
# 故判据用"全部由表头词拼成"，而不是逐个枚举。
_HDR_WORD = r"(?:规格型号|规格|型号|单位|数量|单价|金额|税率|征收率|税额|名称|项目)"

# 表头形近误识归一：`单住`（位→住）、`数置`（量→置）在小字号票面上很常见。
# 只在**明细表头**语境里用（_nhk），不污染通用的 _norm_key。
_HDR_ALIAS = str.maketrans({"住": "位", "伩": "位", "値": "位", "置": "量", "宣": "单"})


def _nhk(s):
    """明细表头专用归一：把形近误识拉回规范列名（``单住`` → ``单位``）。"""
    return _norm_key(s).translate(_HDR_ALIAS)


def _is_item_header_cell(text):
    """判断一个框是否为明细表头的单元格（含合并表头）。

    为什么必须识别合并表头：`单位数量` 这类合并框在 y 上比表头行心更低，会抢到行聚类的
    主导位置，把首行明细的税额吞进"表头 band"而被 `_itemish` 过滤掉——本样本首行税额
    ``3186.07`` 就是这样丢的。判据取"全部由表头关键字拼接而成"，任何拼接组合都能命中，
    而 `碳结圆`/`中90` 这类真实值不会被误判。
    """
    n = _nhk(text)
    return _is_item_header(text) or re.fullmatch(_HDR_WORD + r"+", n) is not None


_ITEM_COL_WORDS = (
    ("spec", ("规格型号", "规格", "型号")),
    ("unit", ("单位",)),
    ("quantity", ("数量",)),
    ("unit_price", ("单价",)),
    ("amount", ("金额",)),
    ("tax_rate", ("税率", "征收率")),
    ("tax_amount", ("税额",)),
)


def _detect_item_columns(lines, H):
    """探测明细表**票面实际印了哪几列**（用于渲染时裁掉"结构上不存在"的列）。

    代开版式/部分老版普票根本没有「单位」列，全量输出 8 列会给每行都塞一个
    「（未识别）」，把"票面没这栏"和"没识别出来"混成一团。明细表头所在的
    一条横带上逐框识别列名，命中即为该列存在。

    Args:
        lines: 行框列表。
        H: 页高。

    Returns:
        列键列表（按票面从左到右排序）；找不到表头时返回 ``[]``（调用方回退到全 8 列）。
    """
    hdr = next((ln for ln in lines or [] if _is_item_header(ln["text"])), None)
    if hdr is None:
        return []
    tol = max(8.0, 0.02 * H)
    cols = {}
    for ln in lines:
        if abs(_cy(ln) - _cy(hdr)) > tol:
            continue
        n = _nhk(ln["text"])
        if not n or len(n) > 16:
            continue
        for key, words in _ITEM_COL_WORDS:
            if key in cols:
                continue
            if any(w in n for w in words):
                cols[key] = ln["x0"]
                break
    if not cols:
        return []
    order = sorted(cols.items(), key=lambda kv: kv[1])
    return ["name"] + [k for k, _x in order if k != "name"]


# ----------------------------- 发票结构解析 -----------------------------
# 计划单列市：票面标题直接印市名（"宁波增值税电子普通发票"），而 PROVINCE_NAMES
# 只有省名，导致这些票的标题前缀补不出来。只用于标题前缀匹配，不进行政区划码表。
PLAN_CITIES = ("宁波", "厦门", "大连", "青岛", "深圳")


def _split_addr_phone(ap):
    """把「地址电话」串拆成 (地址, 电话)。拆不开时整段归地址，绝不凭空造电话号。

    两种印刷形态都要覆盖：带「电话」标签的（地址、电话：xx 电话：yy），
    以及不带标签、电话直接跟在地址后面的（某某省某某市某某路 0000-0000001）。
    """
    ap = (ap or "").strip()
    if not ap:
        return "", ""
    m = re.search(r"(.*?)(?:电\s*话)\D{0,4}(\S+)$", ap)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # 无标签形态：尾部 区号+号码（区号 0 开头 3~4 位，号码 7~8 位，允许连字符/空格）
    m = re.search(r"^(.*?[\u4e00-\u9fff）)])\s*(\(?0\d{2,3}\)?[-\s]?\d{7,8}(?:[-\s]?\d{1,4})?)\s*$", ap)
    if m and len(re.findall(r"[\u4e00-\u9fff]", m.group(1))) >= 2:
        return m.group(1).strip(), m.group(2).strip()
    # 无区号形态：OCR 经常把 `021-50000001` 的区号段整块丢掉（低分辨率票面上「021-」
    # 只有三四个极窄字符），只剩 `50000001` 挂在地址末尾。样本里 6/16 张都是这样丢电话的。
    # 判据取「尾部 7~8 位纯数字，且其前**紧邻一个非数字字符**」：这样
    # `...1161室50000001`、`...A2010-50000002` 都能拆出电话，而连写的
    # `073100000000`（12 位，前面是汉字、中间没有非数字）不会被切掉前 4 位。
    m = re.search(r"^(.*?\D)(\d{7,8})$", ap)
    if m:
        _head = m.group(1).rstrip(" \t-—，,、")
        if len(re.findall(r"[\u4e00-\u9fff]", _head)) >= 2 and len(_head) >= 4:
            return _head, m.group(2)
    return ap, ""


def _split_bank(bank):
    """把「开户行及账号」串拆成 (开户行, 账号)。拆不开时整段归开户行。

    账号是纯数字长串（≥10 位），开户行是中文行名，两者拼接时没有分隔符很常见，
    故用"尾部连续数字"作为切点；切出的账号必须够长，避免把行名里的门牌号当账号。
    """
    bank = (bank or "").strip()
    if not bank:
        return "", ""
    m = re.search(r"(.*?)(?:账\s*号|账号|帐号)\D{0,4}(\S+)$", bank)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.search(r"^(.*?[\u4e00-\u9fff）)])\s*(\d{10,32})\s*$", bank)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return bank, ""


_JUNK_VALUE_RE = re.compile(r"^[#＃*＊※·•\-—_~\.。,，、:：|\s]+$")


def _is_junk_value(v):
    """判断抽到的「值」是不是占位符号而不是真实内容。

    发票上会出现「称：#」这种印刷占位/花字残留。把 `#` 当字段值输出，比留空更具误导性——
    下游会认为这是一个有效字段。真实值至少含 1 个字母/数字/汉字。
    """
    v = (v or "").strip()
    if not v:
        return True
    if _JUNK_VALUE_RE.match(v):
        return True
    # 以冒号结尾/含冒号的串是「标签」而不是「值」——坐标法偶尔会把右侧的另一个标签抓过来
    if v.endswith(("：", ":")) or re.search(r"[:：]", v):
        return True
    return re.search(r"[0-9A-Za-z\u4e00-\u9fff]", v) is None


def _label_value(ln, band, tol):
    """取一个「标签行」对应的值：优先取标签自带的冒号后内容，否则取同排右侧最近的文本块。

    OCR 的中文发票里，标签与值有时在同一框（"称：示例电机有限公司"），有时分属两框
    且 x 相邻。两种形态都要覆盖，否则会像旧实现那样整片丢字段。
    """
    t = ln["text"]
    m = re.search(r"[:：]\s*(.+)$", t)
    # 冒号后是 `#`/`*` 这类占位符时不能直接当值返回，要继续走坐标找右侧真实值
    if m and not _is_junk_value(m.group(1)):
        return m.group(1).strip()
    lx1, ly = ln["x1"], _cy(ln)
    cands = [b for b in band
             if b is not ln and b["x0"] >= lx1 - 8 and abs(_cy(b) - ly) <= tol]
    if not cands:
        return ""
    cands.sort(key=lambda b: b["x0"] - lx1)
    for b in cands:
        if not _is_junk_value(b["text"]):
            return b["text"].strip()
    return ""


def _name_candidate(band, ln_label, tol):
    """名称值被印成占位符时，在标签附近另找「像公司名」的框。

    本样本的销售方把公司名单独印在「称：#」这一行的**上一行**（x 还有轻微重叠），
    因此不能要求 x0 必须严格在标签右侧，改为按 (纵向距离, 横向偏移) 取最近的候选。
    只接受含 ≥4 个汉字、且不含其它字段标签词的框，避免把「纳税人识别号」的值抓来当名称。
    """
    skip = ("名称", "称", "识别号", "识号", "税号", "地址", "开户行",
            "购买方", "销售方", "密码区", "代开")
    best, best_key = "", None
    for b in band:
        if b is ln_label:
            continue
        t = (b["text"] or "").strip()
        if not t:
            continue
        n = _norm_key(t)
        if any(k in n for k in skip):
            continue
        if re.search(r"[:：]", t):  # 含冒号的是标签框，不是值
            continue
        if len(re.findall(r"[\u4e00-\u9fff]", t)) < 4:
            continue
        dy = abs(_cy(b) - _cy(ln_label))
        if dy > tol * 1.5:
            continue
        key = (dy, abs(b["x0"] - ln_label["x0"]))
        if best_key is None or key < best_key:
            best, best_key = t, key
    return best


def _clean_name(v):
    """把名称值清理成"只有公司名"：截断粘在后面的字段标签，并拒绝乱码。

    两件事都必须做：
      1. 截断——OCR 常把"南京某某公司统一社会信用代码/"并成一个框，名称里不能带尾巴；
      2. 拒乱码——密码区字符（``57<2*-1/227…``）没有汉字，被抽成公司名会直接污染
         知识库，这是"无幻觉入库"红线。公司名至少要有 2 个汉字且汉字占比 ≥ 30%。
    """
    v = (v or "").strip()
    if not v:
        return ""
    v = re.split(r"统一社会信用代码|纳税人识别号|纳税人识号|地址|开户行|电\s*话", v)[0].strip()
    v = v.rstrip("／/|｜").strip()
    if not v or _is_junk_value(v):
        return ""
    cjk = sum(1 for c in v if "\u4e00" <= c <= "\u9fff")
    if cjk < 2 or cjk / max(1, len(v)) < 0.3:
        return ""
    return v


def _has_cjk(v, at_least=2):
    """判断字符串是否含足够汉字（用于地址/开户行这类必须有中文的字段）。"""
    return sum(1 for c in (v or "") if "\u4e00" <= c <= "\u9fff") >= at_least


_ORG_SUFFIX_RE = re.compile(r"(公司|厂|商行|商店|中心|事务所|银行|集团|合作社|分店)$")
_PARTY_LABEL_WORDS = ("识别", "税号", "地址", "开户", "密码", "发票", "电话", "帐号", "账号")


def _guess_party_name(band):
    """「名称」标签被读歪时的兜底：按**公司名的形态**在当事人区块里找。

    北京票把「称：」读成「苏：」，四种标签写法全落空，销方名称整块丢失。
    但公司名本身有很强的形态特征——以 公司/厂/商行/中心 等结尾、至少 4 个汉字、
    不含任何其他字段标签词。满足这三点基本不可能是别的东西。

    Args:
        band: 当事人区块内的行框列表。

    Returns:
        公司名；找不到返回空串（宁缺勿错）。
    """
    best = ""
    for ln in sorted(band, key=_cy):
        t = (ln["text"] or "").strip()
        if not t:
            continue
        n = _norm_key(t)
        if any(w in n for w in _PARTY_LABEL_WORDS):
            continue
        # 允许 1~2 个字的标签前缀（`苏：xxx`），也允许整框就是公司名
        m = re.match(r"^.{1,2}[:：]\s*(.+)$", t)
        cand = _clean_name(m.group(1) if m else t)
        if cand and _ORG_SUFFIX_RE.search(cand) and cand not in best:
            best = cand
            break
    return best


def _extract_party_geom(lines, y_lo, y_hi, H):
    """用 OCR 坐标抽取购销方四要素（名称 / 纳税人识别号 / 地址电话 / 开户行账号）。

    比纯正则在粘连文本里瞎猜可靠得多：发票是固定版式，"标签在左、值在右同排"是硬约束。

    Args:
        lines: 行框列表。
        y_lo, y_hi: 纵向区间（购买方在明细表头之上，销售方在价税合计之下）。
        H: 页高，用于自适应容差。

    Returns:
        字段字典；区间内找不到任何标签时返回 ``{}``。
    """
    band = [ln for ln in lines if y_lo <= _cy(ln) <= y_hi]
    if not band:
        return {}
    # 纵向容差必须小于行距（约 30~36px），否则上一行的地址会被当成下一行税号的"右邻值"
    tol = max(14.0, 0.014 * H)

    def find(*keys):
        for ln in sorted(band, key=_cy):
            t = _norm_key(ln["text"])
            if any(k in t for k in keys):
                return ln
        return None

    def find_re(pat):
        """按正则找标签框——用于标签被读歪的情形（``纳税人识别骨``、``的税人认测号``）。"""
        for ln in sorted(band, key=_cy):
            if re.search(pat, _norm_key(ln["text"])):
                return ln
        return None

    out = {}
    ln_name = find("名称", "称")
    ln_tax = find("纳税人识别号", "内税人识别号", "纳税人税号") \
        or find_re(r"税人")
    ln_addr = find("地址") or find_re(r"地\s*址|地址")
    ln_bank = find("开户行") or find_re(r"开[户尸][行厅]")

    if ln_name is not None:
        v = _clean_name(_label_value(ln_name, band, tol))
        if not v:
            v = _clean_name(_name_candidate(band, ln_name, tol))
        if v:
            out["name"] = v
    if not out.get("name"):
        _g = _guess_party_name(band)
        if _g:
            out["name"] = _g
    if ln_tax is not None:
        v = _label_value(ln_tax, band, tol)
        if v:
            # 税号在定义上就是 15~20 位字母数字（15 位老税号 / 18 位统一社会信用代码）。
            # 抽到的串不满足这个形态时**宁可留空**：像江苏"代开"版式会把代开机关名称
            # 抽进税号栏，输出一个公司名当税号比留「（未识别）」危害大得多。
            digits = re.sub(r"[^0-9A-Z]", "", v.upper())
            if 15 <= len(digits) <= 20:
                out["taxpayer_id"] = digits
    if ln_addr is not None:
        v = _label_value(ln_addr, band, tol)
        if v:
            _a, _ph = _split_addr_phone(v)
            # 地址必须有中文（防密码区/税号串位），电话必须是号码形态
            if _has_cjk(_a):
                out["address"] = _a
            if _ph and re.search(r"\d", _ph):
                out["phone"] = _ph
    if ln_bank is not None:
        v = _label_value(ln_bank, band, tol)
        if v:
            _bn, _ba = _split_bank(v)
            if _has_cjk(_bn):
                out["bank_name"] = _bn
            if _ba and re.search(r"\d", _ba):
                out["bank_account"] = _ba
    # 地址被抽成了税号（"代开"版式常见）时清掉，避免同一串数字同时当两个字段
    if out.get("address") and out.get("taxpayer_id") \
            and re.sub(r"\s", "", out["address"]) == out["taxpayer_id"]:
        out["address"] = ""
    return out


def _extract_party(text, label_re):
    # 注意：label_re 是“购买方|购货单位|购货方”形式的多选，必须用括号整体分组，
    # 否则 (.*?) 只绑定到最后一个分支；匹配到“购买方”时 group(1) 会是 None。
    m = re.search("(" + label_re + r")(.*?)(?=销售方|销货|货物|合计|价税合计|$)",
                  text, re.S)
    block = m.group(2) if m else ""
    out = {}
    # 中文 OCR 无空格，字段值用「前瞻到下一字段标签」截断，避免整段粘连。
    # 键名容忍近义写法（如 纳税人税号、开户行及帐号）；分隔符冒号/空格均可。
    # 名称的「名」字常被 OCR 丢掉（只剩"称：xxx"），故"名"设为可选。
    specs = [
        ("name", r"(?:名\s*)?称[:：]?\s*(.*?)(?=(?:纳|内)税人识别号|(?:纳|内)税人税号|地址[、,，]?电话|开户行及账号|开户行及帐号|$)"),
        ("taxpayer_id", r"(?:纳|内)税人识别号[:：]?\s*(.*?)(?=地址[、,，]?电话|开户行及账号|开户行及帐号|$)"),
        ("addr_phone", r"地址[、,，]?电话[:：]?\s*(.*?)(?=开户行及账号|开户行及帐号|$)"),
        ("bank", r"开户行及[账帐]号[:：]?\s*(.*?)(?=收款人|复核|开票人|备注|$)"),
    ]
    for key, pat in specs:
        mm = re.search(pat, block, re.S)
        if mm:
            out[key] = mm.group(1).strip()
    # 拆分 地址、电话 / 开户行及账号（尽力而为；拆不开则整体归首字段，不臆造）
    ap = out.pop("addr_phone", "")
    if ap:
        out["address"], out["phone"] = _split_addr_phone(ap)
    bank = out.pop("bank", "")
    if bank:
        out["bank_name"], out["bank_account"] = _split_bank(bank)
    return out


def _cy(ln):
    """文本块的行中心 y（像素）。"""
    return (ln["y0"] + ln["y1"]) / 2.0


def _cluster_1d(vals, tol):
    """一维聚类：相邻差值 ≤ tol 归为一组，返回各组均值（用于推断列中心）。"""
    vals = sorted(vals)
    groups, cur = [], []
    for v in vals:
        if cur and v - cur[-1] > tol:
            groups.append(cur)
            cur = []
        cur.append(v)
    if cur:
        groups.append(cur)
    return [sum(g) / len(g) for g in groups]


_ITEM_KEYS = ["name", "spec", "unit", "quantity", "unit_price", "amount", "tax_rate", "tax_amount"]


def _extract_items_from_lines(lines, W, H):
    """从带坐标的 OCR 行框重建货物明细表（按表头锚点对齐 8 个 schema 列）。

    为什么不能像以前那样"行内按 x 排序后依次填 8 列"：一旦某行缺列（发票很常见——
    本样本的"单位"列就是空的），后续所有值会整体左移一格，把单价填进数量、把税额
    填进税率，产出"错得很有说服力"的数据。宁可留「（未识别）」，也不能错位。

    做法：
      1) 用表头行拿到 name / spec / unit 三个**锚点列的 x 中心**；
      2) 数据区所有单元格的 x 中心做一维聚类，得到实际存在的列中心；
      3) 锚点左侧的簇按 x 顺序填 name/spec；锚点右侧的簇按 x 顺序填
         quantity/unit_price/amount/tax_rate/tax_amount；
      4) 每行按"最近列中心 + 容差"落格，落不进的留空。

    Args:
        lines: RapidOCR 行框列表，每项含 text / x0 / y0 / x1 / y1。
        W, H: 页面宽高（像素），用于自适应容差。

    Returns:
        schema 列字典组成的行项列表；无法可靠对齐时返回 ``[]``（宁缺勿错）。
    """
    if not lines:
        return []
    hdr = next((ln for ln in lines if _is_item_header(ln["text"])), None)
    if hdr is None:
        return []

    # 明细行行距通常只有 20~30px，容差必须小于行距，否则相邻两行会被并成一行
    row_tol = max(6.0, 0.015 * H)
    hdr_cy = _cy(hdr)

    # 表头带的锚点列：name / spec / unit
    anchors = {}
    for ln in lines:
        if abs(_cy(ln) - hdr_cy) > row_tol:
            continue
        t = _nhk(ln["text"])
        cx = (ln["x0"] + ln["x1"]) / 2.0
        if "规格" in t:
            anchors.setdefault("spec", cx)
        elif "单位" in t:
            anchors.setdefault("unit", cx)
        elif _is_item_header(ln["text"]):
            anchors.setdefault("name", cx)

    # 数据区下界：价税合计行（其上的"合计金额/税额"行属于汇总，不是明细）
    end_cy = H
    for ln in lines:
        if _cy(ln) > hdr_cy and "价税合计" in _norm_key(ln["text"]):
            end_cy = min(end_cy, _cy(ln))
    if end_cy >= H:
        # 兜底：「价税合计」读歪时用「（大写）」定位。否则下界一路落到页底，
        # 销售方四要素（名称/税号/地址/开户行）会被当成明细行吞进表里。
        for ln in lines:
            if _cy(ln) > hdr_cy and "大写" in _norm_key(ln["text"]):
                end_cy = min(end_cy, _cy(ln))

    # 下界取表头框"上沿"：表头框往往比首行明细更高，用 hdr_cy+row_tol 会切掉首行。
    # 表头单元格按"文本形态"排除（含数字与否不可靠：品名列"碳结圆"就没有数字），
    # 合并表头（"单位数量"）也要排除，否则会吞掉首行明细，详见 _is_item_header_cell。
    data_top = hdr["y0"]
    # 表头常占**两行**（老版发票的「税率/税额」比「名称/规格」低半行，北京票就是这样），
    # 第二行表头会被当成首行明细，把「税山（税额）」读成品名。故把紧随表头
    # 1.6 个行距内的「2 字表头碎片」一并排除。判据同时要求首字属于列名用字，
    # 3 字以上的真实品名（如「印花税」）不受影响。
    _hdr_frag_top = hdr_cy + row_tol * 1.6
    _HDR_FRAG_HEAD = set("税金额价量位格名号项规单数")
    cands = [ln for ln in lines
             if data_top < _cy(ln) < end_cy
             and not _is_item_header_cell(ln["text"])
             and not (hdr_cy <= _cy(ln) <= _hdr_frag_top
                      and len(_norm_key(ln["text"])) == 2
                      and _norm_key(ln["text"])[0] in _HDR_FRAG_HEAD)]
    if not cands:
        return []

    # 行聚类
    bands, cur = [], []
    for ln in sorted(cands, key=_cy):
        # 用「与行首」比较而非「与上一个」：逐项比较会让误差累积，把多行串成一行
        if cur and abs(_cy(ln) - _cy(cur[0])) <= row_tol:
            cur.append(ln)
        else:
            if cur:
                bands.append(cur)
            cur = [ln]
    if cur:
        bands.append(cur)

    # 剔除"合计/税额小计"这类纯数字汇总行（否则会把列中心带偏）
    def _is_summary(band):
        texts = [b["text"].strip() for b in band if b["text"].strip()]
        if not texts:
            return True
        if any(re.search(r"合计|小写|大写", t) for t in texts):
            return True  # "（小写）110657.00" 这类汇总行不能当成明细
        # 合计行的金额常把小数点读成冒号/短横（`¥181：62` = 181.62），货币符号
        # 也常被读成 `Y`/`￥`/空白。判据：**整行没有任何汉字**且全部单元格都是"数字串"。
        # 明细行一定带汉字（品名/规格），所以不会被这条误杀。
        return all(not re.search(r"[\u4e00-\u9fff]", t)
                   and re.fullmatch(r"[^\u4e00-\u9fff\d]{0,2}[\d.,\s:：\-]+", t)
                   for t in texts)

    # 只保留"像明细行"的 band：至少 2 个数字单元格。必须**在算列中心之前**过滤，
    # 否则"税总函…""备注碎片"这类单格噪声会造出假列，把品名/规格挤到错误列上。
    def _itemish(band):
        return sum(1 for b in band if re.search(r"\d", b["text"])) >= 2

    data_bands = [b for b in bands if not _is_summary(b) and _itemish(b)]
    if not data_bands:
        return []

    # 列中心聚类
    centers = _cluster_1d([(b["x0"] + b["x1"]) / 2.0 for band in data_bands for b in band],
                          tol=max(20.0, 0.02 * W))
    if not centers:
        return []

    unit_x = anchors.get("unit", anchors.get("spec"))
    if unit_x is None:
        return []  # 定位不到锚点列，宁可不出明细也不出错位数据

    left = [c for c in centers if c < unit_x - 1]
    right = [c for c in centers if c >= unit_x - 1]

    # 左侧 → name / spec（可能存在也可能不存在，按 x 顺序）
    col_map = {}
    left_keys = ["name", "spec"][-len(left):] if left else []
    for k, c in zip(left_keys, left):
        col_map[k] = c
    # 右侧 → quantity / unit_price / amount / tax_rate / tax_amount
    right_keys = ["quantity", "unit_price", "amount", "tax_rate", "tax_amount"][-len(right):] if right else []
    for k, c in zip(right_keys, right):
        col_map[k] = c
    if len(right) > 5:
        col_map = {k: v for k, v in col_map.items() if k in _ITEM_KEYS}

    # 校验：税率列必须真的像税率，否则说明列映射不可信 → 放弃明细则不臆造。
    # 「像税率」= 带 %，或本身就是个 1~3 位小整数（低质扫描常把 `17%` 读成 `179`、
    # 把 `6%` 读成 `6`）。金额一律带两位小数，不会被误认成税率。
    rate_c = col_map.get("tax_rate")
    if rate_c is not None:
        rate_tol = max(12.0, 0.02 * W)
        rate_cells = [b["text"] for band in data_bands for b in band
                      if abs((b["x0"] + b["x1"]) / 2.0 - rate_c) <= rate_tol]
        if rate_cells and not any(
                ("%" in t) or re.fullmatch(r"\d{1,3}", t.strip()) for t in rate_cells):
            return []

    tol_cell = max(12.0, 0.02 * W)
    items = []
    for band in data_bands:
        row = {}
        for b in band:
            cx = (b["x0"] + b["x1"]) / 2.0
            if not col_map:
                break
            key = min(col_map, key=lambda k: abs(col_map[k] - cx))
            if abs(col_map[key] - cx) <= tol_cell and key not in row:
                row[key] = b["text"].strip()
        if len(row) >= 2:
            items.append(row)
    _fix_star_name(items)
    return items


def _fix_star_name(items):
    """把电商票「*类别*品名」从规格列挪回品名列（原地修改）。

    国家税务总局要求普通发票的品名写成 ``*类别*具体名称``（如
    ``*文具*古德牌凭证装订机``）。这类文本很长、又常是整行唯一的宽框，
    列心聚类容易把它归到相邻的「规格型号」列，于是品名列空、规格列一堆长文本。
    判据用 ``*类别*`` 前缀——它是品名专用标记，规格型号里不会出现。
    """
    for it in items:
        spec = (it.get("spec") or "").strip()
        name = (it.get("name") or "").strip()
        # 规格型号里永远不会出现 `*`，所以只要规格列以 `*` 开头且品名列空，
        # 这个值就一定是品名。不必强求两个 `*` 都读出来——低质扫描常丢掉后一个
        # （`*运输服务*客运服务费` 被读成 `*运输服务客运服务费`）。
        if spec and spec.startswith("*") and not name:
            it["name"], it["spec"] = spec, ""
        elif spec and re.match(r"^\*[^*\s]{1,20}\*", spec) and not name:
            it["name"], it["spec"] = spec, ""
        # 品名列空、规格列却是一串**纯中文短语**：几乎一定是品名错位到了规格列。
        # 规格型号总含数字/字母/Φ/×/- 等符号（W310、中90、DN80、Φ65、A4），
        # 而「信息服务费」这类 4 字以上的纯中文短语不可能是规格。
        elif (spec and not name and len(spec) >= 4
              and not re.search(r"[0-9A-Za-zΦφ×*\-]", spec)):
            it["name"], it["spec"] = spec, ""


_CHECK_CODE_RE = re.compile(r"校\s*[^\s\d码]{0,2}\s*码\s*[:：]?\s*([\d\s]{15,32})")


def _extract_check_code(lines, text=""):
    """抽取纸质发票的校验码（税总函规定的 20 位数字串）。

    「校验码」三个字在低分辨率票面上极易读歪（样本里出现过 ``校绘码``），
    而且常与 20 位数字直接连写、没有冒号（``校验码60000000000000000002``）。
    故标签按"校 + 0~2 个非数字非码字 + 码"模糊匹配，先框内、后全文明文。

    Args:
        lines: 行框列表（可为空）。
        text: 明文兜底。

    Returns:
        纯数字校验码；形态不符（位数不够、混入乱码）时返回空串，绝不臆造。
    """
    for src in ([ln["text"] or "" for ln in sorted(lines or [], key=_cy)], [text or ""]):
        for t in src:
            m = _CHECK_CODE_RE.search(t)
            if not m:
                continue
            d = _only_digits(m.group(1))
            if 15 <= len(d) <= 32:
                return d
    return ""


def _extract_header_geom(lines, H):
    """用坐标抽取票头三要素（发票代码 / 发票号码 / 开票日期）与联次。

    为什么必须用坐标：老式发票把「代码」和「No 号码」印在同一行，OCR 会连写成
    ``1000000134000000003400000000...``，任何正则在粘连数字里都只能切出一段错值
    （本样本曾得到 ``100000013400``）。但分行框里 ``No 10000001`` 与 ``3400000000``
    是两个独立框，按"框内纯数字位数"即可精确还原。

    Args:
        lines: 行框列表。
        H: 页高，用于把搜索范围限制在票头区域。

    Returns:
        ``{"invoice_code", "invoice_no", "issue_date", "copy_type"}`` 的子集。
    """
    out = {}
    if not lines:
        return out
    # 票头区域：把搜索限定在页面上 32%，避开正文里的长数字（税号/银行账号）
    head = [ln for ln in lines if _cy(ln) <= 0.32 * H]

    # 开票日期：整框就是一个规范日期（标签常与值分处两框、甚至顺序颠倒）
    for ln in sorted(head, key=_cy):
        m = re.fullmatch(r"(\d{4})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?",
                         (ln["text"] or "").strip())
        if m:
            out["issue_date"] = f"{m.group(1)}年{int(m.group(2)):02d}月{int(m.group(3)):02d}日"
            break

    # 发票号码：`No 10000001` 形态最可靠（发票号码为 8 位）
    for ln in sorted(head, key=_cy):
        m = re.fullmatch(r"NO[.:：]?\s*(\d{8})", (ln["text"] or "").strip(), re.I)
        if m:
            out.setdefault("invoice_no", m.group(1))

    # 发票代码：独立的 10 / 12 位纯数字框（老式票代码 10 位，新版 12 位）
    for ln in sorted(head, key=_cy):
        m = re.fullmatch(r"(\d{10}|\d{12})", (ln["text"] or "").strip())
        if m:
            out.setdefault("invoice_code", m.group(1))

    # 无 No 前缀时，8 位独立框作为发票号码兜底
    for ln in sorted(head, key=_cy):
        m = re.fullmatch(r"(\d{8})", (ln["text"] or "").strip())
        if m:
            out.setdefault("invoice_no", m.group(1))

    # 联次：竖排的「第三联发票联购买方记账凭证」
    for ln in sorted(lines, key=_cy):
        t = (ln["text"] or "").strip()
        if re.match(r"第[一二三四五六七八]联", t) and len(t) <= 30:
            out.setdefault("copy_type", t)
            break
    return out


def _seal_ok(v):
    """签章栏（收款人/复核/开票人）的值是否可信。

    这三栏都是人名。三种东西都不是人名，宁可留「（未识别）」：
      - 纯数字（≥10 位，通常是相邻的税号/账号被串过来）；
      - 印刷注记（"发票专用章（章）开票员"）；
      - **同排的另一个字段标签**——北京票的「收款人：」右侧只印了「销货单位」，
        右邻取值会把单位名当成人名填进收款人栏，这是"错填"，比留空危险得多。
    """
    v = (v or "").strip()
    if _is_junk_value(v):
        return False
    if any(k in v for k in ("单位", "购买方", "销售方", "章", "开户", "识别号")):
        return False
    return not (re.fullmatch(r"\d+", v) or len(v) > 12)


_SEAL_LABELS = (("收款人", "payee"), ("复核", "reviewer"),
                ("开票人", "issuer"), ("开票员", "issuer"))


def _extract_seal_geom(lines, H):
    """抽取票尾签章三项（收款人 / 复核 / 开票人）。

    这三个标签在票尾**同一行相邻**印刷，纯正则会串成一长串
    （本样本曾得到复核=``李四开票人张三销售方（``）。改为"只在本框文本内取值"，
    并把搜索范围限定在页面下 30%，避免命中正文里的同名词。

    值分两种印刷形态，都要覆盖：和标签同框（``收款人：张三``），或印在标签
    右侧的独立框里（``收款人：`` + 隔壁 ``张三``）——后者在样本里占多数，
    旧实现只认前者，7 张票的收款人因此全丢。

    Args:
        lines: 行框列表。
        H: 页高。

    Returns:
        ``(values, blanks)``：values 是 ``{"payee", "reviewer", "issuer"}`` 的子集；
        blanks 是"标签印了、但票面确实没填内容"的键集合——它与"没识别出来"是两回事，
        渲染时要分开标注（专票的收款人/复核常由机器打印，本就留空）。
    """
    values, blanks = {}, set()
    if not lines:
        return values, blanks
    band = [ln for ln in lines if _cy(ln) >= 0.70 * H]
    if not band:
        return values, blanks
    tol = max(12.0, 0.018 * H)

    for label, key in _SEAL_LABELS:
        if key in values:
            continue
        hit_label = False
        for ln in sorted(band, key=lambda x: (abs(_cy(x) - H * 0.85), x["x0"])):
            t = (ln["text"] or "").strip()
            if label not in _norm_key(t):
                continue
            hit_label = True
            m = re.search(label + r"[:：，,、]?\s*([^\s、，,。：:]{1,12})", t)
            v = m.group(1).strip() if m else ""
            if not _seal_ok(v):
                # 标签自己是干净的（冒号后没粘东西）→ 值可能在右侧独立框
                v = ""
                if re.fullmatch(label + r"\s*[:：]?\s*", t):
                    cands = [b for b in band
                             if b is not ln and b["x0"] >= ln["x1"] - 6
                             and abs(_cy(b) - _cy(ln)) <= tol]
                    cands.sort(key=lambda b: b["x0"])
                    for b in cands:
                        if _seal_ok((b["text"] or "").strip()):
                            v = b["text"].strip()
                            break
            if _seal_ok(v):
                values[key] = v
                break
        # 标签印在票面上、但左右都取不到可信的人名 → 票面空白，不是识别失败
        if hit_label and key not in values:
            blanks.add(key)
    return values, blanks


# 大写金额是**封闭字符集**，出现集合外字符必然是 OCR 形近误识。
# 这里只做形近/繁体归一，并把原始串另存 capital_raw，绝不丢原值。
_UPPER_ALIAS = str.maketrans({
    "貳": "贰", "參": "叁", "叄": "叁", "陸": "陆", "圓": "圆", "圈": "圆",
    "萬": "万", "億": "亿", "柴": "柒", "染": "柒", "玫": "玖", "玟": "玖", "案": "柒",
    # 低质票面上「壹→意」「圆→画」这类整字读歪很常见（北京票得到「意仟贰佰伍拾画整」）。
    # 大写金额是封闭字符集，集合外的字必然是误识，归一到最可能的那个不算臆造。
    "意": "壹", "画": "圆", "弍": "贰", "弎": "叁", "垒": "叁", "扒": "捌", "什": "拾",
    "装": "柒", "董": "壹",
})


def _norm_upper_amount(v):
    """归一大写金额里的形近误识字符（如「伍拾柴圆整」→「伍拾柒圆整」）并去掉非汉字噪声。

    只做两件事：① 形近/繁体 → 规范大写汉字；② 去掉 ``?``、空格等非汉字字符。
    **不**改写任何认不出的汉字——认不出就原样保留，让它在交付物里显眼，由人来判断。
    """
    v = (v or "").translate(_UPPER_ALIAS)
    return "".join(c for c in v if "\u4e00" <= c <= "\u9fff")


def _clean_amount(v):
    """规范化金额单元格：去货币符号/千分位，**拒绝粘连或多值**（宁可留空）。

    低质量扫描里 ￥ 常被识成 X / Y，两个相邻金额也会粘进同一个框
    （如 ``X4.640.77`` 实际是 4.64 与 0.77 两个值）。这种串既不能整体当金额，
    也不能猜其中哪一段是哪个字段——一律返回空串，让它在交付物里显示「（未识别）」。
    """
    v = (v or "").strip().replace(",", "").replace("，", "")
    # 小数点被读成冒号是低质票面上的高频错误（`¥181：62` 实为 181.62）。
    # 只认「数字 + 冒号 + 恰好两位数字」这个精确形态，避免把 `金额：100.00` 这类
    # 键值串也一起改写。
    v = re.sub(r"(\d)[：:](\d{2})(?!\d)", r"\1.\2", v)
    hits = re.findall(r"\d+\.\d{2}", v)
    if len(hits) != 1:
        return ""
    # 除了这一个金额之外还残留别的数字 → 说明是两值粘连，不可信
    if len(re.sub(r"[^\d.]", "", v)) > len(hits[0]) + 2:
        return ""
    return hits[0]


_FIG_LABEL_RE = re.compile(r"小\s*写|[（(]\s*小\s*[）)]")


def _is_figures_label(t):
    """判断该框是否为「价税合计（小写）」标签。

    标签被截断是常态：样本里出现过 `（小写）`、`(小写)`、`（小）`、`(小)` 四种写法，
    右侧的 ￥ 又被识成 羊 / 手 / ? / Y。只认完整的「小写」会漏掉一半。
    """
    return _FIG_LABEL_RE.search(t or "") is not None


def _figures_candidate(texts):
    """在一组文本里挑出唯一可信的小写金额（多值粘连时返回空，宁缺勿错）。"""
    for t in texts:
        v = _clean_amount(t)
        if v:
            return v
    return ""


def _extract_totals_geom(lines, H):
    """用坐标抽取「合计金额/税额」与「价税合计大小写」。

    纯正则在粘连文本里会把"价税合计（大写）"后面的整段（含销售方信息）都吞进来；
    坐标法直接把"标签同排右侧最近的那个框"当值，边界天然正确。

    Returns:
        ``{"total_excl_tax", "total_tax", "capital", "figures"}`` 的子集。
    """
    out = {}
    if not lines:
        return out
    tol = max(20.0, 0.02 * H)
    inc = next((ln for ln in lines if "价税合计" in _norm_key(ln["text"])), None)
    if inc is None:
        # 标签被读歪时（北京票「价税合计」→「佛税合许」），用「（大写）」定位：
        # 整张票上只有价税合计这一栏带「大写」字样，这个锚点比四个汉字更耐误识。
        inc = next((ln for ln in lines if "大写" in _norm_key(ln["text"])), None)
    if inc is None:
        return out
    inc_cy = _cy(inc)
    band = [ln for ln in lines if abs(_cy(ln) - inc_cy) <= tol * 2]

    cap = _label_value(inc, band, tol)
    if cap:
        cap = re.sub(r"^[（(]?\s*大写\s*[）)]?\s*", "", cap).strip()
        if cap:
            out["capital"] = cap

    # 小写金额：三个来源依次兜底。这一栏是旧实现丢得最多的地方——
    #   ① 标签与本值同框（`（小写）￥5200.00`、`(小)Y4780.00`）→ 取标签后残余串；
    #   ② 标签与值分框（`（小写）` 在左、`¥5200.00` 在右甚至下一行）→ 取右邻/近邻框；
    #   ③ 标签整块没检出（重庆票只有 `￥ 3276.00`）→ 取价税合计行右侧、不含成串汉字的框。
    # 大写金额串是**纯汉字**，天然被 ③ 的"汉字 <3 个"条件排除，不会大小写串台。
    fig = ""
    lbl = next((ln for ln in band if _is_figures_label(ln["text"])), None)
    if lbl is not None:
        m = re.search(r"[）)]?\s*([^）)]*)$", lbl["text"])
        if m:
            fig = _clean_amount(re.sub(r"^[^\d]{0,4}", "", m.group(1)))
        if not fig:
            near = [ln for ln in band if ln is not lbl and ln["x0"] >= lbl["x1"] - 8
                    and len(re.findall(r"[\u4e00-\u9fff]", ln["text"])) < 3]
            near.sort(key=lambda x: (abs(_cy(x) - _cy(lbl)), x["x0"]))
            fig = _figures_candidate([n["text"] for n in near])
    if not fig:
        right = [ln for ln in band if ln["x0"] > inc["x1"] - 8
                 and len(re.findall(r"[\u4e00-\u9fff]", ln["text"])) < 3]
        right.sort(key=lambda x: (abs(_cy(x) - inc_cy), -x["x0"]))
        fig = _figures_candidate([n["text"] for n in right])
    if fig:
        out["figures"] = fig

    # 合计金额/税额：价税合计行**上方**、最靠近的一行里若有两个金额，即视为合计行。
    # 金额允许不带 ￥（电子发票常见），但必须排除含"大写/小写"的价税合计行——它也在
    # 上方且更靠下，用"最近一行"会把合计行抢掉。故按行分组后，**从下往上找第一个
    # 含 ≥2 个金额的组**；明细行虽然在同样形态，但一定比合计行更靠上，不会被先选中。
    # 判据直接复用 _clean_amount 而不是抄一份正则：它已经处理了"小数点被读成冒号"
    # （`¥181：62`），抄正则会漏掉这类格子，导致合计行只剩 1 个金额而被整行丢弃。
    cur = [ln for ln in lines
           if _cy(ln) < inc_cy and _clean_amount(ln["text"])
           and not re.search(r"大写|小写", ln["text"])]
    if len(cur) >= 2:
        cur.sort(key=_cy)
        row_tol = max(10.0, 0.01 * H)
        rows, grp = [], []
        for ln in cur:
            if grp and abs(_cy(ln) - _cy(grp[0])) <= row_tol:
                grp.append(ln)
            else:
                if grp:
                    rows.append(grp)
                grp = [ln]
        if grp:
            rows.append(grp)
        for cand in reversed(rows):
            if len(cand) < 2:
                continue
            row = sorted(cand, key=lambda x: x["x0"])
            _ex = _clean_amount(row[0]["text"])
            _tx = _clean_amount(row[-1]["text"])
            if _ex:
                out["total_excl_tax"] = _ex
            if _tx:
                out["total_tax"] = _tx
            break
    return out


_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _only_digits(s):
    """提取纯数字并归一全角数字（票面上 `１２１０００` 这类全角印刷很常见）。"""
    return re.sub(r"\D", "", (s or "").translate(_FW_DIGITS))


def _extract_invoice(text, lines, W, H):
    """把发票 OCR 文本 + 坐标重建为符合 docs/invoice_schema.md 的结构化 schema 字典。
    非发票返回 None。键名匹配容忍不同时期版本的近义写法（见 _is_item_header 与各处正则）。"""
    # 票种判定用「明文 + 按阅读顺序拼回的坐标行」：raw_text 有可能缺标题行
    # （VLM 兜底或旧缓存的拼接顺序不同），只看明文会把整张发票判成非发票而丢 schema。
    _ordered = "".join(
        (ln.get("text") or "") for ln in sorted(lines or [], key=lambda x: (_cy(x), x["x0"]))
    )
    full = text + _ordered
    if not re.search(r"发票", full):
        return None
    doc = {
        "header": {}, "buyer": {}, "seller": {}, "items": [],
        "totals": {}, "total_incl_tax": {}, "remarks": "", "signatures": {},
    }
    # 标题：识别是否含省份（如票样“重庆增值税专用发票”）；优先保留 OCR 标题省份，
    # 避免与发票代码推断的省份（样本代码常与印刷省份不一致）冲突。
    mt = re.search(r"(" + "|".join(PROVINCE_NAMES) + r")?\s*"
                   r"(增值税电子普通发票|增值税电子专用发票|增值税专用发票|增值税普通发票"
                   r"|电子发票|普通发票)", full)
    if mt:
        doc["header"]["invoice_type"] = (mt.group(1) or "") + mt.group(2)
    # 发票代码/号码都有**固定位数**（税总函）：代码 10 位（老版）或 12 位（新版），
    # 号码 8 位（纸质/税控票）或 20 位（数电票）。不按位数守，就会把 18 位税号
    # 当成「发票代码」填进去——那是最典型的"错得很有说服力"。
    m = re.search(r"(?:发票代码|代码|发票(?!号码))\D{0,10}(\d{6,12})", text)
    if m and len(_only_digits(m.group(1))) in (10, 12):
        doc["header"]["invoice_code"] = _only_digits(m.group(1))
    m = re.search(r"(?:发票号码|发票号|号码)\D{0,10}(?:No)?\s*(\d{6,20})", text)
    if m and len(_only_digits(m.group(1))) in (8, 20):
        doc["header"]["invoice_no"] = _only_digits(m.group(1))
    m = re.search(r"(?:开票日期|开票时间|开具日期)[:：]?\s*([\d]{4}年[\d]{1,2}月[\d]{1,2}日|[\d]{4}[-/][\d]{1,2}[-/][\d]{1,2})", text)
    if m:
        doc["header"]["issue_date"] = m.group(1)
    m = re.search(r"机器编号\D{0,6}(\d{6,12})", text)
    if m:
        doc["header"]["machine_no"] = _only_digits(m.group(1))
    _cc = _extract_check_code(lines, text)
    if _cc:
        doc["header"]["check_code"] = _cc
    m = re.search(r"(第[一二三四五六]联[：:].{0,24})", text)
    if m:
        doc["header"]["copy_type"] = m.group(1).strip()

    # 票头三要素：坐标结果覆盖明文正则结果。
    # 明文里的「代码+号码」是连写的（100000013400000000...），正则只能切出错值；
    # 分行框里两者是独立框，按位数即可精确还原，故坐标优先。
    for _k, _v in _extract_header_geom(lines or [], H).items():
        if _v:
            doc["header"][_k] = _v

    # 购销方：先用明文块（无坐标时也有效），再用坐标结果覆盖（更可靠）
    buyer_txt = _extract_party(text, r"购买方|购货单位|购货方")
    seller_txt = _extract_party(text, r"销售方|销货单位|销货方")
    buyer_geo, seller_geo = {}, {}
    if lines:
        _hdr = next((ln for ln in lines if _is_item_header(ln["text"])), None)
        _row_tol = max(6.0, 0.02 * H)
        _hdr_cy = _cy(_hdr) if _hdr else H * 0.42
        _inc_cy = next((_cy(ln) for ln in lines if "价税合计" in _norm_key(ln["text"])), None)
        if _inc_cy is None:
            _inc_cy = next((_cy(ln) for ln in lines if "大写" in _norm_key(ln["text"])), None)
        if _inc_cy is None:
            # 两个锚点都没读出时**必须**给一个几何下界，否则销售方四要素整块丢失
            # （北京票就是这样把销方全丢的）。销售方块一定在明细表之后、票尾签章之前，
            # 取页高 62% 作为保守分界，宁可多收一点噪声也不整块放弃。
            _inc_cy = H * 0.62
        buyer_geo = _extract_party_geom(lines, 0, _hdr_cy - _row_tol, H)
        if _inc_cy is not None:
            seller_geo = _extract_party_geom(lines, _inc_cy, H, H)
    # 名称统一过一遍清理（截断粘连的字段标签 + 拒乱码），明文与坐标两条路都要走
    for _d in (buyer_txt, seller_txt, buyer_geo, seller_geo):
        if _d.get("name"):
            _n = _clean_name(_d["name"])
            if _n:
                _d["name"] = _n
            else:
                _d.pop("name", None)
    doc["buyer"] = {**buyer_txt, **{k: v for k, v in buyer_geo.items() if v}}
    doc["seller"] = {**seller_txt, **{k: v for k, v in seller_geo.items() if v}}
    doc["items"] = _extract_items_from_lines(lines, W, H)

    # 合计/价税合计：坐标优先，明文兜底
    _tot = _extract_totals_geom(lines or [], H)
    m = re.search(r"小写[）)]?\s*[¥￥]?\s*([\d]+\.\d{2})", text)
    if m and not _tot.get("figures"):
        _tot["figures"] = m.group(1)
    m = re.search(r"合计[^\d]{0,8}([¥￥]?\d[\d.]*)\s*(\d+%)\s*([¥￥]?\d[\d.]*)", text)
    if m:
        _tot.setdefault("total_excl_tax", m.group(1))
        _tot.setdefault("total_tax", m.group(3))
    doc["totals"]["total_excl_tax"] = _tot.get("total_excl_tax")
    doc["totals"]["total_tax"] = _tot.get("total_tax")
    _cap = _tot.get("capital")
    doc["total_incl_tax"]["capital"] = _norm_upper_amount(_cap)
    if _cap and doc["total_incl_tax"]["capital"] != _cap:
        doc["total_incl_tax"]["capital_raw"] = _cap  # 归一前的原始串，保证可追溯
    doc["total_incl_tax"]["figures"] = _tot.get("figures")

    m = re.search(r"备注[:：]?\s*(.*?)(?=收款人|复核|开票人|$)", text, re.S)
    if m:
        doc["remarks"] = m.group(1).strip()
    for key, label in (("payee", "收款人"), ("reviewer", "复核"), ("issuer", "开票人")):
        mm = re.search(label + r"[:：]?\s*([^\s、，,。]{1,12})", text)
        if mm and _seal_ok(mm.group(1)):
            doc["signatures"][key] = mm.group(1).strip()
    # 签章：坐标结果覆盖明文结果（明文会把三个标签串成一段）
    _seal, _seal_blank = _extract_seal_geom(lines or [], H)
    for _k, _v in _seal.items():
        if _v:
            doc["signatures"][_k] = _v

    # 「票面空白」与「没识别出来」必须分开记：专票的收款人/复核由机器打印，
    # 票面上本就留空，把它标成「（未识别）」会让复核人去找一个不存在的失败。
    _blank = set()
    for _k in _seal_blank:
        if not doc["signatures"].get(_k):
            _blank.add(f"signatures.{_k}")
    _head_band = _norm_key("".join(
        ln.get("text") or "" for ln in (lines or []) if 0.62 * H <= _cy(ln) <= 0.92 * H
    ))
    if not (doc.get("remarks") or "").strip() and "备注" in _head_band:
        _blank.add("remarks")     # 备注栏印了、但票面没写内容

    # 票种元信息：kind 决定"要不要密码区掩膜"，header_rows 决定基本信息显示哪几栏。
    # 二者都必须落到 schema 里，渲染层才能真正按票种输出，而不是所有票都套同一份栏位。
    _kind = _invoice_kind(_norm_key(full), doc["header"], lines, W, H)
    if not doc["header"].get("invoice_type"):
        # 标题被印章压住或拆框时（北京普票读成「通发票」，连省份都不完整），
        # 用票种判定结果补一个规范标题。这是**从标题区证据反推出的规范名**，
        # 不是凭空造字段——判不出票种时上面那条路会落到 vat_general，同样有据。
        doc["header"]["invoice_type"] = {
            "e_invoice": "电子发票",
            "vat_special": "增值税专用发票",
            "vat_general": "增值税普通发票",
        }[_kind]
    # 标题补省份：电子普票/新版普票的发票代码前 2 位不再是行政区划，代码推断会失手。
    # 但省份**只能从标题区取**（页面上 25%），用整页文本会把购销方地址里的省份
    # 当成开票地（宁波票曾被补成"上海普通发票"，因为销售方地址在上海）。
    _head_txt = _norm_key("".join(
        (ln.get("text") or "") for ln in sorted(lines or [], key=lambda x: (_cy(x), x["x0"]))
        if _cy(ln) <= 0.25 * H
    ))
    _t = str(doc["header"].get("invoice_type") or "")
    _place = list(PROVINCE_NAMES) + list(PLAN_CITIES)
    if _t and not any(p in _t for p in _place):
        # 取**在标题区文本里出现位置最靠前**的那个地名，而不是按列表顺序取第一个。
        # 重庆票的标题区同时含「重庆」（标题）和「天津城区」（购买方地址），
        # 按列表顺序会先命中「天津」，把标题补成「天津增值税专用发票」——这是错值。
        _hits = [(_head_txt.find(p), p) for p in _place if p in _head_txt]
        if _hits:
            doc["header"]["invoice_type"] = min(_hits)[1] + _t
    doc["meta"] = {
        "kind": _kind,
        # 密码区是否存在**只看版面**，不从票种反推：纸质「增值税电子普通发票」
        # 也有密码区，按"只有专票才有"去推会漏掉它；而数电票一定没有。
        "has_password_area": _has_password_area(lines or [], W, H) and _kind != "e_invoice",
        "header_rows": _pick_header_rows(doc["header"], full, _kind),
        "item_columns": _detect_item_columns(lines or [], H),
        "blank": sorted(_blank),
    }
    return doc


# ----------------------------- 渲染层 -----------------------------
# 渲染分派总原则：
#   1) md 与 html 都必须「人能读」；json 才是给 RAG / 资料库接入用的结构化载体。
#   2) 有 schema 的凭证类（发票 / 后续的证书等）→ 严格按 schema 输出，
#      该字段没抽到就写「（未识别）」，绝不用破折号混淆成"本来就为空"。
#   3) 无 schema 的普通文档 / 图片 → html 尽量还原原图版式；md 输出可读文本。
#   4) 原始 OCR 全文一律不进 md/html，只落 LOG 目录（见 log_store.py）。

_MISS = "（未识别）"
# 「票面空白」是第三种状态，必须与「未识别」分开：
#   - 未识别 = 票面印了内容，但没抽出来 → 复核人要去看原图/换手段重做；
#   - 票面空白 = 票面这一栏本来就空（专票收款人、备注栏）→ 复核人无需动作。
# 混成一个标记会让复核人对着 16 张都空的备注栏反复确认，纯属噪声。
_BLANK = "（票面空白）"
_ITEM_HEAD = ["name", "spec", "unit", "quantity", "unit_price", "amount", "tax_rate", "tax_amount"]
_ITEM_HEAD_CN = {"name": "名称", "spec": "规格型号", "unit": "单位", "quantity": "数量",
                 "unit_price": "单价", "amount": "金额", "tax_rate": "税率", "tax_amount": "税额"}


def _item_cols(doc):
    """明细表要渲染哪几列：优先按抽取层探测到的「票面实印列」，否则全 8 列。"""
    cols = [c for c in ((doc.get("meta") or {}).get("item_columns") or []) if c in _ITEM_HEAD]
    return cols or list(_ITEM_HEAD)


def _md_cell(v, blank=False):
    """markdown 表格单元格：竖线要转义（否则切坏表格）、换行压成空格、空值标状态。"""
    if v in (None, ""):
        return _BLANK if blank else _MISS
    return str(v).replace("|", "丨").replace("\n", " ").strip() or (_BLANK if blank else _MISS)


def _md_kv(rows):
    """把 [(标签, 值), (标签, 值, blank), ...] 渲染为两列 markdown 表。"""
    out = ["| 项目 | 内容 |", "| --- | --- |"]
    for r in rows:
        label, val = r[0], r[1]
        blank = bool(r[2]) if len(r) > 2 else False
        out.append(f"| {_md_cell(label)} | {_md_cell(val, blank)} |")
    return "\n".join(out)


def _combine(*vals):
    """组合字段（地址+电话、开户行+账号）：有值就拼，全缺才返回 None（交给 _md_cell 标未识别）。"""
    parts = [str(v).strip() for v in vals if v not in (None, "") and str(v).strip()]
    return "  ".join(parts) if parts else None


# 基本信息候选栏目：**不是每个票种都有全 6 项**。专票无校验码、电子发票无联次、
# 纸质普票有校验码。全量输出会让普票/电子票满屏「（未识别）」，把"结构上不存在"
# 和"没识别出来"混为一谈——对复核人是灾难。故按"票面是否印有该栏"裁剪。
# 这里也保留给后续凭证类型复用（证书等各自有候选表）。
_HEADER_CANDIDATES = [
    ("发票代码", "invoice_code"),
    ("发票号码", "invoice_no"),
    ("开票日期", "issue_date"),
    ("机器编号", "machine_no"),
    ("校验码", "check_code"),
    ("联次", "copy_type"),
]


# 数电票（全电发票）票面上**不存在**的栏目：它没有联次、没有机器编号、没有校验码。
# 注意纸质「增值税电子普通发票」不属于这里——它有这三栏，只有 20 位号码的才是数电票。
_KIND_EXCLUDE = {
    "e_invoice": {"copy_type", "machine_no", "check_code"},
}


def _pick_header_rows(header, full, kind=""):
    """按票面实际印制的栏目挑基本信息行（栏位随票种变化）。

    Args:
        header: schema 的 header 字典。
        full: 明文 + 坐标行拼接文本，用于判断票面上是否印有该栏目标签。
        kind: 票种（``e_invoice`` / ``vat_special`` / ``vat_general``），用于剔除
            该票种结构上不存在的栏目。

    Returns:
        ``[{"label": 栏名, "value": 值或 None}, ...]``；始终至少含发票号码与开票日期。
    """
    n = _norm_key(full)
    drop = _KIND_EXCLUDE.get(kind, set())
    rows = [{"label": lb, "value": header.get(k)}
            for lb, k in _HEADER_CANDIDATES
            if k not in drop and (header.get(k) or lb in n)]
    have = {r["label"] for r in rows}
    for lb, k in (("发票号码", "invoice_no"), ("开票日期", "issue_date")):
        if lb not in have:   # 票面标签被整体漏识别时的兜底，避免基本信息整块消失
            rows.append({"label": lb, "value": header.get(k)})
    # 按候选表原始顺序排列，保证不同票种的输出顺序一致可读
    order = {lb: i for i, (lb, _k) in enumerate(_HEADER_CANDIDATES)}
    return sorted(rows, key=lambda r: order.get(r["label"], 99))


def _has_password_area(lines, W, H):
    """用**版面特征**判断票面上是否存在密码区（只有专票有）。

    密码区是机器可读密文，OCR 往往认不出「密码区」这三个字，却认得那块乱码
    （``57<2*-1/227≤7--0+1292<4*599``）。所以判据取"位置 + 尺寸 + 字符构成"：
    票面中上部、宽度超过 1/7 页宽、汉字占比 ≤5% 的长串。

    这比找标签词稳，也比按标题判可靠（标题常被拆框读歪）；普票上不存在这样的
    区域，因此不会误掩。
    """
    if not lines or not W or not H:
        return False
    dense = []
    for ln in lines:
        if not (0.15 * H <= _cy(ln) <= 0.55 * H):
            continue
        t = (ln["text"] or "").strip()
        # 宽度下限取 1/7 页宽：密码区是**拆成多行**的长密文，每行只有整块的
        # 五六成宽（北京票每行 189~264px，页宽 1280）。按 1/4 页宽卡会整块漏判，
        # 进而把专票认成普票，密码区掩膜和栏位分派跟着一起错。
        if len(t) < 10 or (ln["x1"] - ln["x0"]) < 0.14 * W:
            continue
        cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
        if cjk / len(t) <= 0.05:
            dense.append(ln)
    # 单行长数字串不算：普票的 20 位校验码、银行卡号都是这个形态。
    # 密码区是**纵向堆叠的多行密文块**，故要求同一 x 区间内至少 3 行。
    if len(dense) < 3:
        return False
    # 再按**左边缘聚类**：密码区是一个左对齐的矩形块（每行起笔位置几乎相同），
    # 而税号、银行账号、发票号码都在别的列上，一聚类就被分出去。
    # 不做这一步，安徽票的 `No 10000001`（左 0.70）和税号（左 0.21）会把整块的
    # x 跨度撑到 0.70 页宽，明明有密码区却判成没有。
    tol_x = max(8.0, 0.05 * W)
    groups, cur = [], []
    for ln in sorted(dense, key=lambda x: x["x0"]):
        if cur and ln["x0"] - cur[-1]["x0"] > tol_x:
            groups.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        groups.append(cur)
    block = max(groups, key=len)
    if len(block) < 3:
        return False
    left = min(b["x0"] for b in block)
    span_x = max(b["x1"] for b in block) - left
    # 密码区一定在票面右侧（税控票版式固定）；左下角的同形长串是税号/账号，不是密文。
    return left >= 0.35 * W and span_x <= 0.45 * W


def _title_kind_from_head(n):
    """只凭**标题区文本**判票种（标题常被拆框/读歪，需要模糊判据）。

    发票标题是一个封闭集合，只有「专用发票」和「普通发票」两种后缀。汉字读歪之后
    仍有一条极其稳定的线索：**「通」只出现在「普通」里，专票标题永远没有「通」**。
    样本实测：`普通发票`/`首通发票`(山东)/`纸通发票`(北京普票)/`电普通发票`(上海)
    全都含 `通发票`；而 `专用发票`/`大用发票`(上海/湖南/广东)/`栈用发票`(安徽)/
    `税用发票`(湖北) 全都含 `用发票` 且不含 `通`。

    判据取 `通发票` 而不是单字 `通`，是为了避开地名——「南通增值税专用发票」
    里的「通」后面跟的是「增值税」，不会误判成普票。

    Args:
        n: 已 ``_norm_key`` 归一化的标题区文本。

    Returns:
        ``"vat_general"`` / ``"vat_special"`` / ``""``（判不出）。
    """
    if "通发票" in n:
        return "vat_general"
    if "用发票" in n:
        return "vat_special"
    return ""


def _invoice_kind(full, header, lines=None, W=0, H=0):
    """判定票种：``e_invoice``（数电票）/ ``vat_special`` / ``vat_general``。

    **只有数电票（全电发票）才没有密码区、校验码、机器编号、联次**。
    这是一个容易踩的坑：纸质「增值税电子普通发票」——名字里有"电子"——其实是
    税控系统的打印件，票面上密码区、校验码、机器编号一应俱全（北京发票样2 即如此）。
    所以**不能**用"标题含电子发票"来判数电票，硬判据只有一个：**发票号码 20 位**。

    判据顺序（标题最权威，因为它来自票面标题区且已归一）：
      1. 号码 20 位 / 明文含「数电」「全电」→ 数电票；
      2. 标题区模糊判别（见 :func:`_title_kind_from_head`）；
      3. 已抽出的标题里的「专用发票」/「普通发票」字面；
      4. 都判不出时按普票处理——**绝不能用"有没有密码区"来推专票**：
         增值税普通发票同样印密码区（北京普票、山东普票实拍均有），
         这个旁证是无效的，用了只会把普票批量误判成专票。
    """
    no = str(header.get("invoice_no") or "")
    title = str(header.get("invoice_type") or "")
    if len(re.sub(r"\D", "", no)) >= 20 or "数电" in full or "全电" in full:
        return "e_invoice"
    if "专用发票" in title:
        return "vat_special"
    if "普通发票" in title:
        return "vat_general"
    _tk = _title_kind_from_head(
        _norm_key("".join(ln.get("text") or "" for ln in (lines or []) if _cy(ln) <= 0.25 * H))
        if lines else full
    )
    if _tk:
        return _tk
    if "普通发票" in full:
        return "vat_general"
    if "专用发票" in full:
        return "vat_special"
    return "vat_general"


def _render_md_schema(doc):
    """发票 schema → 可读 markdown。

    栏目顺序固定（对齐纸质票面的阅读顺序）。缺值分两态：``（未识别）`` 与 ``（票面空白）``，
    见 ``_BLANK`` 的说明——这是"未识别比例偏高"观感的主要来源之一。
    """
    h = doc.get("header") or {}
    buyer = doc.get("buyer") or {}
    seller = doc.get("seller") or {}
    items = doc.get("items") or []
    totals = doc.get("totals") or {}
    incl = doc.get("total_incl_tax") or {}
    sig = doc.get("signatures") or {}
    meta = doc.get("meta") or {}
    blank = set(meta.get("blank") or [])

    parts = [f"# {invoice_title_text(h)}"]

    rows = meta.get("header_rows") or [
        {"label": lb, "value": h.get(k)} for lb, k in _HEADER_CANDIDATES
    ]
    parts.append("## 一、发票基本信息\n" + _md_kv([(r["label"], r["value"]) for r in rows]))

    for title, party in (("二、购买方", buyer), ("三、销售方", seller)):
        parts.append(f"## {title}\n" + _md_kv([
            ("名称", party.get("name")),
            ("纳税人识别号", party.get("taxpayer_id")),
            ("地址、电话", _combine(party.get("address"), party.get("phone"))),
            ("开户行及账号", _combine(party.get("bank_name"), party.get("bank_account"))),
        ]))

    cols = _item_cols(doc)
    if items:
        head = "| " + " | ".join(_ITEM_HEAD_CN[c] for c in cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        body = ["| " + " | ".join(_md_cell(it.get(c)) for c in cols) + " |" for it in items]
        parts.append("## 四、货物或应税劳务、服务名称\n" + "\n".join([head, sep] + body))
    else:
        parts.append(f"## 四、货物或应税劳务、服务名称\n{_MISS}")

    parts.append("## 五、合计与价税合计\n" + _md_kv([
        ("金额合计", totals.get("total_excl_tax")),
        ("税额合计", totals.get("total_tax")),
        ("价税合计（大写）", incl.get("capital")),
        ("价税合计（小写）", incl.get("figures")),
    ]))

    _rm = str(doc.get("remarks") or "").strip()
    parts.append("## 六、备注\n" + (_rm or (_BLANK if "remarks" in blank else _MISS)))

    parts.append("## 七、签章\n" + _md_kv([
        ("收款人", sig.get("payee"), "signatures.payee" in blank),
        ("复核", sig.get("reviewer"), "signatures.reviewer" in blank),
        ("开票人", sig.get("issuer"), "signatures.issuer" in blank),
    ]))

    return "\n\n".join(parts)


def _render_txt_schema(doc):
    """发票 schema → 纯文本（`标签：值` 逐行；明细用 TAB 分隔），便于喂给下游脚本。"""
    h = doc.get("header") or {}
    meta = doc.get("meta") or {}
    blank = set(meta.get("blank") or [])
    out = [invoice_title_text(h), ""]

    def kv(label, val, is_blank=False):
        out.append(f"{label}：{_BLANK if is_blank else (_MISS if val in (None, '') else str(val).strip())}")

    out.append("[发票基本信息]")
    _rows = meta.get("header_rows") or [
        {"label": lb, "value": h.get(k)} for lb, k in _HEADER_CANDIDATES
    ]
    for r in _rows:
        kv(r["label"], r["value"])

    for sec, party in (("购买方", doc.get("buyer") or {}), ("销售方", doc.get("seller") or {})):
        out.append(f"[{sec}]")
        kv("名称", party.get("name"))
        kv("纳税人识别号", party.get("taxpayer_id"))
        kv("地址、电话", _combine(party.get("address"), party.get("phone")))
        kv("开户行及账号", _combine(party.get("bank_name"), party.get("bank_account")))

    cols = _item_cols(doc)
    items = doc.get("items") or []
    out.append("[货物或应税劳务、服务名称]")
    if items:
        out.append("\t".join(_ITEM_HEAD_CN[c] for c in cols))
        for it in items:
            out.append("\t".join(_MISS if it.get(c) in (None, "") else str(it.get(c)) for c in cols))
    else:
        out.append(_MISS)

    totals = doc.get("totals") or {}
    incl = doc.get("total_incl_tax") or {}
    out.append("[合计与价税合计]")
    kv("金额合计", totals.get("total_excl_tax"))
    kv("税额合计", totals.get("total_tax"))
    kv("价税合计（大写）", incl.get("capital"))
    kv("价税合计（小写）", incl.get("figures"))

    out.append("[备注]")
    out.append(str(doc.get("remarks") or "").strip()
               or (_BLANK if "remarks" in blank else _MISS))

    sig = doc.get("signatures") or {}
    out.append("[签章]")
    kv("收款人", sig.get("payee"))
    kv("复核", sig.get("reviewer"))
    kv("开票人", sig.get("issuer"))
    return "\n".join(out)


HTML_CSS = ("body{font-family:sans-serif;margin:16px;color:#222}"
             "table{border-collapse:collapse;margin:8px 0;width:100%}"
             "td,th{border:1px solid #999;padding:4px 8px;text-align:left;vertical-align:top}"
             "h2{margin:14px 0 4px}section{border:1px solid #ccc;border-radius:6px;padding:8px;margin:8px 0}"
             "pre{white-space:pre-wrap;font-family:monospace}")


def _esc_html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cluster_rows(cells, H, row_tol_frac=0.02):
    """把带坐标文本块按 y 中心聚成行（相邻中心差 < 阈值归并）。"""
    if not cells:
        return []
    row_tol = max(6.0, row_tol_frac * H)
    ordered = sorted(cells, key=lambda c: (c["y0"] + c["y1"]) / 2.0)
    rows = []
    for c in ordered:
        cy = (c["y0"] + c["y1"]) / 2.0
        if rows and abs(cy - rows[-1]["cy"]) <= row_tol:
            rows[-1]["cells"].append(c)
        else:
            rows.append({"cy": cy, "y0": c["y0"], "y1": c["y1"], "cells": [c]})
    return rows


def _build_page_table(cells, W, H, as_html=True):
    """按 OCR 坐标“原样”重建整页表格：不套用任何固定表头/列顺序，仅依据文本块
    坐标做行列聚类与（保守的）合并单元格推断。页面所有文字——标题、表头
    （发票代码/号码/日期）、购销方信息、明细表、脚注（收款人/复核/开票人）等——
    一并纳入，零丢弃、零重排。

    - HTML：按全页自然列网格对齐渲染真实 <table>；跨列/跨行以“几何覆盖”推断
      （colspan = 文本块 x 范围覆盖的列中心数；rowspan = y 范围覆盖的行中心数），
      天然抑制旧逻辑里 colspan=3 之类的过度合并。
    - md/txt：逐行按 x 升序平铺单元格（保留原始从左到右阅读顺序），不强制全局列对齐、
      不强加表头，避免在表格里“自行理解重新排序”。
    """
    if len(cells) < 2:
        txts = [c["text"].strip() for c in cells if c["text"].strip()]
        if as_html:
            return "<table><tr><td>" + "<br>".join(
                _esc_html(t) for t in txts) + "</td></tr></table>"
        return "\n".join(txts)
    rows = _cluster_rows(cells, H)
    if not rows:
        return ""

    if not as_html:
        # md/txt：逐行按原始 x 顺序平铺，不做重排、不强加表头
        out = []
        for ri in range(len(rows)):
            cells_row = [c["text"].strip().replace("|", "丨")
                         for c in sorted(rows[ri]["cells"], key=lambda c: c["x0"])
                         if c["text"].strip()]
            if cells_row:
                out.append("| " + " | ".join(cells_row) + " |")
        return "\n".join(out)

    # ---- HTML：全页列网格（合并容差内的相邻左右边缘）----
    xs = sorted(set(round(v, 1) for c in cells for v in (c["x0"], c["x1"])))
    slots, cur = [], [xs[0]]
    col_gap = 0.015 * W
    for x in xs[1:]:
        if x - cur[-1] <= col_gap:
            cur.append(x)
        else:
            slots.append((cur[0], cur[-1]))
            cur = [x]
    slots.append((cur[0], cur[-1]))
    ncols = len(slots)

    def col_of(px):
        for i, (a, b) in enumerate(slots):
            if a - 1 <= px <= b + 1:
                return i
        return min(range(ncols), key=lambda i: abs((slots[i][0] + slots[i][1]) / 2 - px))

    row_centers = [r["cy"] for r in rows]

    def colspan_of(x0, x1):
        # 仅统计“列中心”落在该框 x 范围之内的列，避免框边缘微偏导致的过度合并
        n = sum(1 for i in range(ncols)
                if x0 <= (slots[i][0] + slots[i][1]) / 2 <= x1)
        return max(1, n)

    def rowspan_of(y0, y1):
        n = sum(1 for rc in row_centers if y0 <= rc <= y1)
        return max(1, n)

    grid = [[None] * ncols for _ in range(len(rows))]
    for ri, row in enumerate(rows):
        # 同行内按 x 升序，保持从左到右的原始阅读顺序，不做任何重排
        for c in sorted(row["cells"], key=lambda c: c["x0"]):
            ci = col_of(c["x0"])
            cur = grid[ri][ci]
            if cur is not None and not (isinstance(cur, tuple) and cur == ("occ",)):
                # 同格重叠（同一框被相邻列槽同时命中）：拼接文本，保留首个框的跨行/列属性
                grid[ri][ci] = (cur[0], cur[1], cur[2] + " " + c["text"].strip())
                continue
            cs = colspan_of(c["x0"], c["x1"])
            rs = rowspan_of(c["y0"], c["y1"])
            grid[ri][ci] = (cs, rs, c["text"].strip())
            for rr in range(ri, min(ri + rs, len(rows))):
                for cc in range(ci, min(ci + cs, ncols)):
                    if (rr, cc) != (ri, ci):
                        grid[rr][cc] = ("occ",)

    body = []
    for ri in range(len(rows)):
        # 仅保留“首个有效列→最后有效列”之间的单元格，去掉行首/行尾全空列，
        # 避免居中标题等产生大量空白 <td>；共享列的相对对齐仍保留。
        lo, hi = ncols, -1
        for ci in range(ncols):
            v = grid[ri][ci]
            if v is not None and not (isinstance(v, tuple) and v == ("occ",)):
                lo = min(lo, ci)
                hi = max(hi, ci)
            elif isinstance(v, tuple) and v == ("occ",):
                hi = max(hi, ci)  # 被跨列/跨行覆盖到的列也计入范围
        if hi < 0:
            continue
        tds = []
        for ci in range(lo, hi + 1):
            v = grid[ri][ci]
            if isinstance(v, tuple) and v == ("occ",):
                continue  # 已被上方跨列/跨行框覆盖
            if v is None:
                tds.append("<td></td>")
            else:
                cs, rs, txt = v
                attr = ""
                if cs > 1:
                    attr += f' colspan="{cs}"'
                if rs > 1:
                    attr += f' rowspan="{rs}"'
                tds.append(f"<td{attr}>{_esc_html(txt)}</td>")
        if tds:
            body.append("<tr>" + "".join(tds) + "</tr>")
    return "<table>" + "".join(body) + "</table>"


def _html_faithful_block(page):
    """无 schema 时的 HTML 片段：按 OCR 坐标尽量还原原图版面（标题/表头/脚注都在坐标里）。

    没有坐标数据（例如走 VLM 路径拿不到行框）时退化为按识别文本呈现 —— 此时"文本"
    就是本页的全部内容，不是追溯用的兜底噪声。
    """
    lines = page.get("lines") or []
    W, H = page.get("img_w") or 0, page.get("img_h") or 0
    if lines and W and H:
        tbl = _build_page_table(lines, W, H, as_html=True)
        if tbl:
            return f"<section><h2>按 OCR 坐标还原版面</h2>{tbl}</section>"
    raw = page.get("raw_text") or page.get("text") or ""
    return ("<section><h2>识别文本（本页无坐标数据，无法还原版面）</h2>"
            f"<pre>{_esc_html(raw)}</pre></section>")


def _render(data, fmt, out_path, input_path, prov, mode):
    """渲染总入口：按「本页有没有命中 schema」分流，**不再按 --mode 分流**。

    为什么要脱离 --mode：``--mode`` 是 pic2edit 的**流水线**参数（决定它怎么识别），
    与"结果以什么形态交付"是两件不同的事。此前用 mode 决定渲染，导致发票被当成
    通用表格 → 输出成一坨 OCR 坐标碎片；这是产物不可读的根因。

    分派规则：
      - ``json``：原样输出 data（schema + engine + 坐标），供 RAG / 资料库接入
      - ``html``：命中 schema → 严格版式；否则 → 坐标还原原图版面
      - ``md`` / ``docx``：命中 schema → 可读 schema 文本；否则 → 可读识别文本
      - ``txt``：同 md，用 ``标签：值`` + TAB 分隔
    原始 OCR 全文一律不进入以上任何输出，只由 log_store 落盘。
    """
    pages = data.get("pages", [])
    if fmt == "json":
        base = out_path or (os.path.splitext(input_path)[0] + ".json")
        if not base.lower().endswith(".json"):
            base = os.path.splitext(base)[0] + ".json"
        out_path = os.path.abspath(base)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return out_path

    blocks = []
    schema_pages = 0
    for p in pages:
        raw = p.get("raw_text") or p.get("text") or ""
        if not raw.strip():
            continue
        engine = p.get("engine", "?")
        # convert() 里已逐页抽过 schema 并存进 page；缓存命中的旧记录没有该键，兜底重抽
        doc = p.get("schema", None)
        if "schema" not in p:
            try:
                doc = _extract_invoice(raw, p.get("lines") or [],
                                       p.get("img_w") or 0, p.get("img_h") or 0)
            except Exception as e:
                sys.stderr.write(f"[warn] 凭证 schema 抽取异常(第{p.get('page', '?')}页): {e}\n")
                doc = None
        if doc:
            schema_pages += 1

        if fmt == "html":
            blk = (f"<section>{_invoice_fragment(doc)}</section>" if doc
                   else _html_faithful_block(p))
        elif fmt == "txt":
            blk = _render_txt_schema(doc) if doc else raw
        else:  # md / docx
            blk = _render_md_schema(doc) if doc else raw
        if prov:
            blk = f"<!-- engine:{engine} -->\n{blk}"
        blocks.append(blk)

    if fmt == "html":
        text = ("<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>"
                "<title>识别结果</title>"
                f"<style>{HTML_CSS}\n{INVOICE_CSS}</style></head>"
                "<body>\n" + "\n".join(blocks) + "\n</body></html>")
    else:
        text = "\n\n".join(blocks)

    if not out_path:
        stem = os.path.splitext(input_path)[0]
        out_path = f"{stem}.{fmt}"
    out_path = os.path.abspath(out_path)
    if fmt == "docx":
        md_to_docx(text, out_path)
    else:
        save_text(text, out_path)

    sys.stderr.write(
        f"[render] 命中凭证 schema {schema_pages}/{len(blocks)} 页 → "
        f"{'严格按 schema 输出' if schema_pages else '按原图版面还原'}\n")
    return out_path


def convert(input_path, fmt, mode, url, gpu_layers, out_path, stop_server,
            use_cache, prov, vlm_model=None):
    input_path = _safe_path(input_path)
    if not os.path.isfile(input_path):
        sys.stderr.write(f"[error] 输入文件不存在: {input_path}\n")
        sys.exit(2)

    # 可选的「密码区/防伪底纹掩膜」预处理（skill 层，不动 pic2edit）：
    # 送入 OCR 前把发票密码区矩形涂白，从源头消除防伪底纹噪声。
    #
    # 三态开关 ocr.mask_password：
    #   auto（默认）—— **不掩膜**。掩膜必须发生在 OCR 之前，此刻还不知道票种；
    #                  而只有增值税专用发票有密码区，普通发票/电子发票没有，
    #                  盲掩会把这些票面上的正常正文整块涂掉（比底纹噪声危害大得多）。
    #                  在两遍流程（先识别判票种、再掩膜重跑）做出来之前，auto 一律不掩。
    #   true        —— 强制掩膜，用于已确认是专票且底纹严重的场景。
    #   false       —— 明确不掩膜。
    # 若设了 PIC2EDIT_MASK_RECT（x0,y0,x1,y1 比例）则直接用手动矩形；
    # 否则智能定位：找"密码区"竖排标签 → 其右侧褐色矩形 → 区域内文字乱码验证。
    _mask_cfg = settings_store.get("ocr.mask_password", "auto")
    if isinstance(_mask_cfg, bool):          # 兼容旧版 true/false 配置
        _mask_cfg = "true" if _mask_cfg else "false"
    _mask_cfg = str(_mask_cfg).strip().lower()
    _env_mask = os.environ.get("PIC2EDIT_MASK_PASSWORD", "").lower() in ("1", "true", "yes")
    mask_enabled = _env_mask or _mask_cfg == "true"
    if _mask_cfg == "auto" and not _env_mask:
        sys.stderr.write(
            "[mask] 未掩膜（auto：专票才有密码区，自动模式下不做盲掩，"
            "避免把普票/电子发票的正文涂掉；确需掩膜设 ocr.mask_password=true）\n")
    mask_rect = None
    if mask_enabled:
        override = _parse_mask_rect(os.environ.get("PIC2EDIT_MASK_RECT"))
        if override is not None:
            mask_rect = override
            sys.stderr.write(
                f"[info] 使用手动指定的密码区掩膜 {tuple(round(v, 3) for v in mask_rect)}\n")
        else:
            try:
                mask_rect = _locate_password_region(input_path)
                sys.stderr.write(
                    f"[info] 智能定位密码区掩膜 {tuple(round(v, 3) for v in mask_rect)}\n")
            except Exception as e:
                sys.stderr.write(f"[warn] 智能定位密码区失败，回退默认预设: {e}\n")
                mask_rect = DEFAULT_VAT_RECT
    effective_input = input_path
    if mask_rect is not None:
        # 掩膜副本按「输入内容哈希」命名，避免不同输入同名文件跨进程互相覆盖污染。
        _mask_tmp = os.path.join(tempfile.gettempdir(),
                                "pic2edit_masked_" + _sha256_file(input_path)[:16] + ".png")
        try:
            _mask_copy(input_path, _mask_tmp, mask_rect)
            effective_input = _mask_tmp
            sys.stderr.write(
                f"[info] 已对密码区掩膜，用掩膜副本送入 OCR: {os.path.basename(_mask_tmp)}\n")
        except Exception as e:
            sys.stderr.write(f"[warn] 掩膜预处理失败，回退原图: {e}\n")
            effective_input = input_path

    # ---- P0 手写/印刷预筛（两层引擎选择的最前一道闸）----
    # 手写体 → 直接走 VLM，跳过 RapidOCR（判别式对手写几乎必出噪声）；
    # 印刷体 → 进入下方「判别式为主 + VLM 兜底」分支。
    # 任何失败 / PDF / 无 cv2 → 保守判为印刷，不影响既有行为。
    #
    # **默认关闭**。独立盲测（PrintType 20 张印刷 vs handwrite 19 张手写）显示：
    # 误报 13/20=65%、漏报 0/19=0%，且 SELF 判据类间区间完全重叠，二维穷尽搜索
    # 最优操作点仍有 50% 误报——不是调参能解决的。65% 误报意味着批量印刷件
    # 三分之二页面白跑 VLM（实测 RapidOCR 2.54s/页 vs VLM ≤10s/页，1000 页多花
    # 约 2.1 小时；早期"8 倍减速"的估算已作废），故默认关闭、改为显式指定。
    # 确认某批是手写件时：设 PIC2EDIT_HW_FORCE=1 强制启用本预筛，
    # 或直接 PIC2EDIT_SKIP_DISCRIMINATIVE=1 强制走 VLM（等价且更确定）。
    _hw, _hw_conf = False, 0.0
    _hw_force = os.environ.get("PIC2EDIT_HW_FORCE", "").lower() in ("1", "true", "yes")
    try:
        if not _hw_force and not bool(settings_store.get("ocr.handwriting_screen", False)):
            _hw = False  # settings.json 的 ocr.handwriting_screen=false 时整段跳过
            sys.stderr.write(
                "[screen] 手写预筛已关闭（默认；settings: ocr.handwriting_screen=false）。"
                "若本批确为手写件，设 PIC2EDIT_HW_FORCE=1 启用，"
                "或 PIC2EDIT_SKIP_DISCRIMINATIVE=1 强制走 VLM。\n")
        elif input_path.lower().endswith((".pdf", ".tif", ".tiff")):
            _hw = False  # 多页影像预筛不适用，保守按印刷
        else:
            if _hw_force:
                sys.stderr.write("[screen] PIC2EDIT_HW_FORCE=1 → 强制启用预筛\n")
            _hw, _hw_conf = detect_handwriting(effective_input)
    except Exception as e:
        sys.stderr.write(f"[warn] 手写预筛异常，回退印刷分支: {e}\n")
        _hw = False
    sys.stderr.write(f"[screen] 手写预筛 handwritten={_hw} confidence={_hw_conf}\n")
    if _hw:
        skip_disc = True
        sys.stderr.write("[route] 判定手写体 → 跳过 RapidOCR，直走 VLM 兜底\n")

    # ---- A2 缓存命中：直接复用结构化结果并按请求格式渲染 ----
    cache_key = None
    if use_cache:
        # 缓存键基于「原始输入文件哈希」而非掩膜副本，避免掩膜开关导致同图不同键；
        # 掩膜矩形参与键，确保不同掩膜参数命中不同缓存。
        cache_key = f"{_sha256_file(input_path)}|{mode}|{CACHE_VERSION}"
        if mask_rect is not None:
            cache_key += "|mask:" + ",".join(f"{v:.4f}" for v in mask_rect)
        cf = _cache_file(cache_key)
        if os.path.isfile(cf):
            try:
                with open(cf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 缓存可能写入于「重复护栏」修复之前（脏缓存含模型循环），命中时再过一遍护栏；
                # 护栏幂等，对已干净的文本无副作用，避免为修一行而重跑昂贵的 OCR。
                _n_trim = 0
                for _pg in data.get("pages") or []:
                    _rt = _pg.get("raw_text")
                    if _rt:
                        _clean, _r = _trim_repetition(_rt)
                        if _r:
                            _pg["raw_text"] = _clean
                            _n_trim += 1
                _msg = "[cache] 命中，直接复用识别结果"
                if _n_trim:
                    _msg += f"（对 {_n_trim} 页补做重复护栏截断）"
                sys.stderr.write(_msg + "\n")
                print(_render(data, fmt, out_path, input_path, prov, mode))
                return
            except Exception:
                pass

    # 自动规避沙箱内存限制：若本进程虚拟内存软限制受限（如 WorkBuddy 沙箱 4GB），
    # RapidOCR/onnxruntime 加载模型会被 OOM killer 连父进程一起 SIGKILL(137)。
    # 此时跳过判别式，直接走 VLM 兜底（依赖本机 :8080 的 llama-server）。
    # 也可用环境变量 PIC2EDIT_SKIP_DISCRIMINATIVE=1 强制跳过。
    _force = os.environ.get("PIC2EDIT_SKIP_DISCRIMINATIVE", "").lower() in ("1", "true", "yes")
    # 统一路由策略收敛到 ocr_app RoutingPolicy：页面类型(table/figure/html_table) + 手写预筛
    # + 沙箱内存受限 + 强制跳过，均跳过判别式直走 VLM 兜底；图表模式关闭幻觉守卫。
    # 与 GUI 使用同一套规则，避免两侧行为不一致（如防伪底纹发票 GUI 缺逃生口）。
    _policy = RoutingPolicy.for_page_type(
        mode, force_skip=_force, hw_detected=_hw, vmem_limited_gb=_vmem_limited_gb(),
    )
    skip_disc = _policy.skip_discriminative
    if skip_disc:
        sys.stderr.write("[info] 跳过判别式(RapidOCR)，仅用 VLM 兜底(需本机 :8080 的 llama-server)\n")

    # 判别式后端可用则无需 GPU 服务（纯 CPU、零幻觉、可无头运行）；
    # 仅当判别式不可用且未跳过时，才拉起 VLM 服务兜底。
    disc_ok = False
    if not skip_disc:
        try:
            DiscriminativeBackend()
            disc_ok = True
        except Exception:
            disc_ok = False

    proc = None
    vlm_probe = set()

    # 按本机显卡显存选模型档位：8GB 卡用 q8_0（精度优先），4GB 笔记本自动降到
    # Q4_K_M（q8_0 会 OOM），无独显/显存过小则退 CPU。只换权重文件名，
    # 不修改 pic2edit 任何代码（详见 model_select.py）。
    _models_dir = os.path.join(OCR_APP, "models")
    _sel = model_select.choose(
        _models_dir,
        vlm_model or settings_store.get("vlm.model", "auto"),
        settings_store.get("vlm.gpu_layers"),
    )
    sys.stderr.write(model_select.describe(_sel) + "\n")
    _model_files = (_sel.get("model"), _sel.get("mmproj")) if _sel.get("model") else None
    if gpu_layers is None and _sel.get("gpu_layers") is not None:
        gpu_layers = _sel["gpu_layers"]

    # A1 修复：凡 VLM 兜底可能被用到且（被跳过 或 判别式不可用）时，确保服务就绪。
    # 原逻辑在 skip_disc=True 时不拉服务，导致兜底调用失败。
    if skip_disc or not disc_ok:
        _, proc = ensure_server(url, gpu_layers, _model_files)
    # 注：disc_ok 且未跳过时，不预拉服务（判别式通常已覆盖；真需兜底会因无服务而
    # 降级为空结果，与原行为一致，避免无谓占用显存）。

    table_avail = _table_available() if mode == "table" else False

    work = tempfile.mkdtemp(prefix="pic2edit_")
    try:
        docs = prepare_input(effective_input, work)
        # prepare_input 返回 List[InputDocument]，流水线需要扁平的 InputItem 列表
        items = []
        for d in docs:
            if hasattr(d, "pages"):
                items.extend(d.pages)
            else:
                items.append(d)
        if not disc_ok:
            # 服务已就绪：用探针包装 VLM 后端，记录哪些页走了 VLM，得到逐页溯源。
            # 注意：必须按 skip_disc 透传 skip_discriminative，绝不能硬编码 False——
            # 否则即便用户用 PIC2EDIT_SKIP_DISCRIMINATIVE=1 强制跳过判别式，
            # 流水线仍会先跑 RapidOCR，而 RapidOCR 在复杂底纹发票上返回非空噪声，
            # 导致 _quality_ok 通过、VLM 兜底被门控掉、DeepSeek-OCR 根本没被调用。
            probe = _VlmProbe(url, "deepseek-ocr")
            pipeline_run(items, vlm_backend=probe, server_url=url, model="deepseek-ocr",
                         use_vlm_fallback=True, mode=mode, policy=_policy)
            vlm_probe = probe.called
        else:
            # disc 可用分支也用探针包装 VLM 后端：确保「判别式为主 + VLM 兜底」时，
            # 若内部真触发了兜底调用，也能被溯源标记（修复原 disc_ok 分支的虚假溯源）。
            probe = _VlmProbe(url, "deepseek-ocr")
            pipeline_run(items, vlm_backend=probe, server_url=url, model="deepseek-ocr",
                         use_vlm_fallback=True, mode=mode, policy=_policy)
            vlm_probe = probe.called
        # 先快照原始 OCR 文本：kv 后处理会改写 it.result，需在此之前保留“完整原文”
        # 全文重复护栏：VLM/判别式偶发「整段循环重复」（如重庆票样），截断后再入库，
        # 避免噪声污染。pic2edit 自带 trim 仅处理尾部，这里补全文检测。
        # 截断前原文先留一份：LOG 目录要能复现"模型到底吐了什么"，否则护栏不可审计
        pre_trim = {id(it): (it.result or "") for it in items}
        trimmed_total = 0
        for it in items:
            _t = it.result or ""
            _t, _reps = _trim_repetition(_t)
            if _reps:
                trimmed_total += _reps
                sys.stderr.write(
                    f"[warn] 检出模型输出重复循环({_reps}次)，已截断防止噪声入库\n")
            it.result = _t
        raw_results = {id(it): (it.result or "") for it in items}
        if mode == "table":
            for it in items:
                if it.result and it.result.strip():
                    it.result = kv_text_to_markdown_table(it.result)
        # 需要带坐标的 RapidOCR 行框的场景：
        #   - html/json 输出（还原版面 / 结构化接入都要坐标）
        #   - table 模式
        #   - 文本里出现凭证类关键词（可能是发票/证书，行项表要靠坐标重建）
        # 只在必要时再跑一次 _raw（仅读 pic2edit API，不改它），避免每次都付双份 OCR 开销。
        need_lines = (fmt in ("html", "json")) or (mode == "table") or any(
            re.search(r"发票|增值税|证书|凭证|营业执照", raw_results.get(id(it)) or "")
            for it in items
        )
        pages = []
        for i, it in enumerate(items):
            lines, W, H = ([], 0, 0)
            if need_lines:
                imgp = getattr(it, "image_path", None)
                if imgp and os.path.isfile(imgp):
                    lines, W, H = _rapidocr_lines(imgp)
            pages.append({
                "page": i + 1,
                "text": (it.result or ""),
                "raw_text": raw_results.get(id(it), ""),
                "engine": _classify_engine(it, mode, skip_disc, disc_ok, table_avail, vlm_probe),
                "lines": lines,
                "img_w": W,
                "img_h": H,
            })

        # 逐页抽取凭证 schema：一处抽取，同时满足「渲染严格按 schema」与「json 供 RAG 接入」。
        # 注意：schema 是**派生数据**，绝不写进缓存——否则改进抽取逻辑后，缓存命中的旧
        # schema 会一直生效，必须 bump CACHE_VERSION 重跑昂贵的 OCR 才能生效。
        # 缓存只保留识别结果（raw_text / lines / 尺寸），schema 在每次读取时现算。
        for p in pages:
            try:
                p["schema"] = _extract_invoice(
                    p.get("raw_text") or "", p.get("lines") or [],
                    p.get("img_w") or 0, p.get("img_h") or 0,
                )
            except Exception as e:
                sys.stderr.write(f"[warn] 凭证 schema 抽取异常(第{p.get('page')}页): {e}\n")
                p["schema"] = None
        schema_hits = sum(1 for p in pages if p.get("schema"))
        if schema_hits:
            sys.stderr.write(f"[schema] {schema_hits}/{len(pages)} 页命中凭证 schema（发票）\n")
    finally:
        if proc is not None and stop_server:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    data = {"source": os.path.abspath(input_path),
            "vlm_fallback_invoked": bool(vlm_probe),
            "pages": pages}

    # 原始 OCR 全文落到 LOG 目录（可追溯、可复现），**不进入任何交付物**。
    # 先按保留天数清理旧日志（静默，失败不阻断），再写本次记录。
    try:
        log_store.purge()
    except Exception:
        pass
    _log_originals = {i: (pre_trim.get(id(it), "") or "") for i, it in enumerate(items)}
    _log_path = log_store.save_ocr_log(
        input_path, pages,
        vlm_model=os.path.basename(_sel.get("model") or "") or None,
        vlm_fallback_invoked=bool(vlm_probe),
        trimmed_reps=trimmed_total,
        originals=_log_originals,
    )
    if _log_path:
        sys.stderr.write(f"[log] 原始 OCR 已记录: {_log_path}\n")

    # 写缓存（只存识别结果：raw_text / lines / 尺寸）。
    # schema 是派生数据，故意剔除——否则改进抽取逻辑后，缓存命中的旧 schema 会一直
    # 生效，必须 bump CACHE_VERSION 重跑 OCR 才能让修复可见。
    if use_cache and cache_key:
        try:
            _cached = dict(data)
            _cached["pages"] = [
                {k: v for k, v in (p or {}).items() if k != "schema"}
                for p in (data.get("pages") or [])
            ]
            with open(_cache_file(cache_key), "w", encoding="utf-8") as f:
                json.dump(_cached, f, ensure_ascii=False)
        except Exception:
            pass
    # 唯一 stdout 输出：最终文件路径，供 skill 解析后 present_files
    print(_render(data, fmt, out_path, input_path, prov, mode))


def main():
    ap = argparse.ArgumentParser(description="pic2edit: 图片/PDF -> 可编辑文件")
    ap.add_argument("input", help="输入图片或 PDF 路径")
    ap.add_argument("--format", choices=["md", "txt", "docx", "json", "html"], default="md")
    ap.add_argument("--mode",
                    choices=["markdown", "ocr", "figure", "table"], default="markdown")
    ap.add_argument("--url", default=f"http://localhost:{PORT}")
    ap.add_argument("--gpu-layers", type=int, default=None,
                    help="显存层数；容器无 GPU 时传 0 走 CPU。默认不传（自动用 GPU）")
    ap.add_argument("--out", default="", help="输出文件路径（默认与输入同名替换扩展名）")
    ap.add_argument("--stop-server", action="store_true",
                    help="若本次拉起了引擎，转换完成后退出引擎进程")
    ap.add_argument("--cache", action="store_true",
                    help="按内容哈希缓存识别结果（重复预处理秒回）")
    ap.add_argument("--prov", action="store_true",
                    help="输出中为每个页面加 engine 溯源注释（md/txt/docx）")
    ap.add_argument("--vlm-model", default=None,
                    choices=["auto", "q8", "q4", "bf16", "cpu"],
                    help="视觉模型档位；默认取 settings.json 的 vlm.model，"
                         "auto 按显存自动选择（≥6.5G 用 q8_0，3–6.5G 用 Q4_K_M，否则 CPU）")
    args = ap.parse_args()
    convert(args.input, args.format, args.mode, args.url,
            args.gpu_layers, args.out, args.stop_server, args.cache, args.prov,
            args.vlm_model)


if __name__ == "__main__":
    main()
