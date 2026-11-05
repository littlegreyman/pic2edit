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
  7) 按 --format 导出 md / txt / docx / json / html，落到输出路径并打印。

新增能力（相对原版，均在 skill 层、不修改 pic2edit 项目）：
  --cache       按「文件内容哈希 + mode + 版本」缓存识别结果，重复预处理秒回；
  --prov        在 md/txt/docx 输出中为每个页面加 `<!-- engine:xxx -->` 溯源注释；
  --format json 输出结构化 JSON（含每页 text 与 engine），便于入库一致性校验；
  --format md/docx 无凭证 schema 时，若该页有明显表格版式（如「现行 | 修正」两栏
                对照表），用**共享** table_layout 还原成真表格（与 GUI 同源）。

用法：
  python pic2edit_convert.py <输入图片/PDF> [--format md|docx|txt|json|html]
                              [--mode markdown|ocr|figure|table]
                              [--schema none|auto|<id>]
                              [--url http://localhost:8080]
                              [--gpu-layers N] [--out OUT_PATH] [--stop-server]
                              [--cache] [--prov]
  python pic2edit_convert.py --list-schemas     # 查看本机可用的凭证 schema

凭证 schema（Phase 3.2 起）：本脚本**不认识任何具体票种**。发票的 1300 行字段知识
已搬到 ``ocr_app/schemas/invoice/``，由 --schema 的三态决定用不用：
  none  不启用，产出与"从来没有过 schema"完全一致；
  auto  按页自动判定（默认）；
  <id>  强制指定（未知 id 在任何昂贵动作之前就报错退出）。
新增一种凭证 = 往 ``ocr_app/schemas/`` 或 ``~/.pic2edit/schemas/`` 丢一个目录，
不需要改本脚本、也不需要重新发版。

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
sys.path.insert(0, HERE)  # 同目录下的 skill 基础设施（settings/log/model_select）可被导入


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

# 只经共享内核门面访问后端能力（Phase 2.1）：不再直连 utils.* / backends.* /
# run_llamacpp_server 等内部模块，内部签名变化由契约测试拦截，而非运行期静默断。
from backend_api import (  # noqa: E402
    LlamaCppBackend,
    DiscriminativeBackend,
    pipeline_run,
    RoutingPolicy,
    EngineLauncher,
    vmem_limited_gb,
    env_force_skip,
    prepare_input,
    save_text,
    md_to_docx,
    kv_text_to_markdown_table,
    build_table_from_lines,
    discover_engine,
    build_engine_command,
    list_schemas,
    apply_schemas,
    render_schema,
    schema_css,
    resolve_schema,
    MODE_AUTO,
    MODE_NONE,
    locate_password_region,
    apply_password_mask,
    parse_mask_rect,
    DEFAULT_VAT_RECT,
    detect_handwriting,
    choose_vlm,
    describe_vlm_choice,
    page_with_image,
    faithful_css,
)

# 手写/印刷预筛（P0 路由闸，纯 numpy + cv2 无模型；MIT 友好，独立重新实现）
# skill 层基础设施：设置 / 原始 OCR 日志（按显存选模型已下沉到内核，见 backend_api）
import settings_store  # noqa: E402
import log_store  # noqa: E402

PORT = "8080"
READY_TIMEOUT = 240.0
# 缓存只存"识别结果"（raw_text/lines/engine），渲染与 schema 抽取在读取时进行，
# 因此渲染主干重构无需清缓存；命中时会补做重复护栏与 schema 抽取。
#
# 但**识别层**的改动（换模型、改预处理）无法在读取时重算——那些已经是 OCR 的
# 输入而非产物。故缓存键必须含识别层参数（见 convert 里的 cache_key），
# 否则 `--vlm-model q4` 跑完再 `--vlm-model q8` 会直接命中 Q4 的结果。
CACHE_VERSION = "v3"

#: 写缓存时剔除的键：全部是**派生数据**，必须在读取时重算。
#: ``schema_id`` 与 ``schema_version`` 必须一起剔除——只删 ``schema`` 会留下
#: 与 ``schema=None`` 矛盾的 id，让产物元数据自相矛盾。
_CACHE_DROP_KEYS = ("schema", "schema_id", "schema_version")


# --------------------------------------------------------------------------
# 新增工具函数（skill 层，无 ocr_app 依赖）
# --------------------------------------------------------------------------
def _sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def _cache_root():
    """按用户隔离的缓存目录（权限 0700）。

    ``<tmp>/pic2edit_cache`` 是所有用户共享的名字：多用户机器上，另一个本地用户可以
    预置 ``sha256(输入哈希).json``，让本次转换读到**别人伪造的识别结果**——票据场景
    下这是实打实的数据完整性风险（金额被改写而无人察觉）。目录名带 uid + 0700 即可
    挡住跨用户写入，Windows 无 getuid 时退回用户名。
    """
    if hasattr(os, "getuid"):                 # POSIX
        who = str(os.getuid())
    else:                                     # Windows
        try:
            import getpass
            who = getpass.getuser()
        except Exception:
            who = "default"
    return os.path.join(tempfile.gettempdir(), f"pic2edit_cache_{who}")


def _cache_key_for(input_path, mode, vlm_model, skip_disc, mask_rect):
    """计算缓存键。

    **识别层参数必须进键**（模型档位 / 是否跳过判别式）：它们决定的是识别结果本身，
    读取时无法重算，缺了它们换模型后会读到上一次的 OCR 文本。
    而 schema 与渲染参数**不进键**——那些是派生数据，命中时现算（见 _apply_schema_stats）。
    """
    key = (f"{_sha256_file(input_path)}|{mode}|{CACHE_VERSION}"
           f"|vlm:{vlm_model or settings_store.get('vlm.model', 'auto')}"
           f"|skip:{1 if skip_disc else 0}")
    if mask_rect is not None:
        # 掩膜矩形参与键，确保不同掩膜参数命中不同缓存。
        key += "|mask:" + ",".join(f"{v:.4f}" for v in mask_rect)
    return key


def _cache_payload(data):
    """构造写缓存的内容。

    只存**识别结果**（raw_text / lines / engine / 尺寸）。派生数据（schema 文档 /
    schema_id / schema_version）一律剔除：否则改进抽取逻辑后，缓存命中的旧 schema 会
    一直生效，必须 bump CACHE_VERSION 重跑 OCR 才能让修复可见。
    三个键必须**一起**剔——只删 schema 会留下 schema_id 与 schema=None 自相矛盾的页。
    """
    out = dict(data)
    out["pages"] = [{k: v for k, v in (p or {}).items() if k not in _CACHE_DROP_KEYS}
                    for p in (data.get("pages") or [])]
    return out


def _cache_file(key):
    d = _cache_root()
    os.makedirs(d, mode=0o700, exist_ok=True)
    if hasattr(os, "chmod"):                  # 目录已存在时不生效，补齐一次
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    # key 可能含 Windows 非法文件名字符（如 |），用其哈希作文件名，避免 OSError
    return os.path.join(d, hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")


# --------------------------------------------------------------------------
# 密码区 / 防伪底纹掩膜预处理（skill 层，不动 pic2edit）
# --------------------------------------------------------------------------
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
        from backend_api import TableRecognizer
        tr = TableRecognizer()
        return bool(tr.available())
    except Exception:
        return False




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
    exe, model, mmproj = discover_engine()
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
    cmd = build_engine_command(exe, model, mmproj)
    if gpu_layers is not None:
        cmd += ["--n-gpu-layers", str(gpu_layers)]
    print(f"[launch] {' '.join(cmd)}", file=sys.stderr)
    # 用 EngineLauncher 拉起：置于独立进程组，关停时可整组回收，
    # 消除 llama-server worker 子进程变孤儿持续占显存的隐患（等价原 Popen，仅更稳健）。
    _el = EngineLauncher()
    proc = _el.start(cmd, cwd=OCR_APP,
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
    # 「自拉起则负责关停」必须覆盖超时退出路径：此处 proc 尚未返回给调用方，
    # 调用方的 finally 拿不到它（proc 仍为 None），不在这里回收就会留下常驻引擎占显存。
    try:
        _el.stop(proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            _el.kill(proc)
    except Exception:
        pass
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
def _rapidocr_lines(image_path):
    """用 pic2edit 的判别式后端取带坐标的逐行结果。

    走**公开**方法 ``ocr_image_with_lines`` 而不是私有的 ``_raw``：
    ``_raw`` 是内部实现，改名/改签名时不会有任何告警，只会在运行期静默断掉。

    返回 (lines, W, H)：lines 每项含 text/x0/x1/y0/y1；失败返回 (None,0,0)。
    """
    try:
        backend = DiscriminativeBackend()
        if not backend.available():
            return None, 0, 0
        _text, data = backend.ocr_image_with_lines(image_path)
    except Exception as e:
        sys.stderr.write(f"[warn] 判别式取带坐标行框失败: {e}\n")
        return None, 0, 0
    if not data:
        return None, 0, 0
    return data.get("lines") or [], data.get("img_w") or 0, data.get("img_h") or 0


# --------------------------------------------------------------------------
# 凭证 schema 已整体搬出转换流程（Phase 3.2.1）。
# 抽取/渲染在 ocr_app/schemas/invoice/ 内，经 backend_api 门面按需调用；
# 本脚本不再包含任何发票字段知识——它只管「识别 + 交付」。
# --------------------------------------------------------------------------

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


def _build_page_table(cells, W, H):
    """按 OCR 坐标“原样”重建整页表格：不套用任何固定表头/列顺序，仅依据文本块
    坐标做行列聚类与（保守的）合并单元格推断。页面所有文字——标题、表头
    （发票代码/号码/日期）、购销方信息、明细表、脚注（收款人/复核/开票人）等——
    一并纳入，零丢弃、零重排。

    只产出 HTML ``<table>``：按全页自然列网格对齐，跨列/跨行以“几何覆盖”推断
    （colspan = 文本块 x 范围覆盖的列中心数；rowspan = y 范围覆盖的行中心数），
    天然抑制旧逻辑里 colspan=3 之类的过度合并。

    Markdown 表格**不走本函数**，统一由 ``_layout_table_md`` 调共享
    ``backend_api.build_table_from_lines``（与 GUI 同源，内核 utils/table_layout）。原先的
    ``as_html=False`` 平铺分支既无调用方（死代码），产出的行也缺 ``| --- |``
    分隔行（多数渲染器不成表），已删除。
    """
    if len(cells) < 2:
        txts = [c["text"].strip() for c in cells if c["text"].strip()]
        return "<table><tr><td>" + "<br>".join(
            _esc_html(t) for t in txts) + "</td></tr></table>"
    rows = _cluster_rows(cells, H)
    if not rows:
        return ""

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
    """无 schema 时的 HTML 片段：尽量按**原件版式**复原这一页。

    三档，从保真度高的往下退：
      1. 原图垫底 + 按坐标叠透明文字层（``page_with_image``）——位置是 OCR 给的，
         不是猜的，文字仍可选中/复制；这就是"可搜索 PDF"的做法。
      2. 有坐标但没有原图（例如缓存来自别处）：按坐标聚成表格。
      3. 连坐标都没有（走 VLM 路径）：直接呈现识别文本。

    注意 1 需要 ``page["image"]``；只在导出 html 时才会被填进来（见 convert）。
    """
    overlay = page_with_image(page, uid=f"p{page.get('page', 0)}",
                              title=f"第 {page.get('page', '?')} 页")
    if overlay:
        return f"<section>{overlay}</section>"
    lines = page.get("lines") or []
    W, H = page.get("img_w") or 0, page.get("img_h") or 0
    if lines and W and H:
        tbl = _build_page_table(lines, W, H)
        if tbl:
            return f"<section><h2>按 OCR 坐标还原版面</h2>{tbl}</section>"
    raw = page.get("raw_text") or page.get("text") or ""
    return ("<section><h2>识别文本（本页无坐标数据，无法还原版面）</h2>"
            f"<pre>{_esc_html(raw)}</pre></section>")


def _layout_table_md(p):
    """用**共享** table_layout 把带坐标的页面还原成 Markdown 表格（与 GUI 同源）。

    为什么要共享而不是在 skill 里再写一套：GUI 的表格能力来自
    ``backend_api.build_table_from_lines``（内核实现 ocr_app/utils/table_layout，中部 gutter 优先切两栏 +
    端点聚类回退，产出带 ``| --- |`` 分隔行的合法 md 表）。skill 此前只有 ``html``
    走坐标重建，md/txt/docx 直接吐原始 OCR 文本，于是"两栏对照表"在 md 里排不出表——
    同一份输入，GUI 出表、skill 不出表。这里直接复用它，从此两侧同源。

    仅在页面**明显是表格版式**时返回非空（≥50% 视觉行含 ≥2 格）；单栏正文、
    双栏文章每行 1 格 → 返回 ""，由调用方回退为识别原文，避免把文章误塞进表格。
    """
    lines = p.get("lines") or []
    W = p.get("img_w") or 0
    H = p.get("img_h") or 0
    if not lines or not W or not H:
        return ""
    try:
        return build_table_from_lines(lines, W, H)
    except Exception as e:      # 表格重建失败绝不能拖垮整篇导出
        sys.stderr.write(
            f"[warn] 表格版式还原失败(第{p.get('page', '?')}页): {e}\n")
        return ""


def _schema_stamp(pages):
    """``"invoice@1.0.0"``；没有任何命中则返回空串。

    产物必须能回答「按哪一版规则抽的」。只记 id 时，schema 从 1.0.0 改到 1.1.0
    （比如补了一个字段），历史产物就无法解释两次导出为何不同。
    """
    seen = {}
    for p in pages or []:
        sid = p.get("schema_id")
        if sid:
            seen[sid] = p.get("schema_version") or ""
    return "、".join(f"{k}@{v}" for k, v in sorted(seen.items()) if v)


def _render(data, fmt, out_path, input_path, prov, mode):
    """渲染总入口：按「本页有没有命中 schema」分流，**不再按 --mode 分流**。

    为什么要脱离 --mode：``--mode`` 是 pic2edit 的**流水线**参数（决定它怎么识别），
    与"结果以什么形态交付"是两件不同的事。此前用 mode 决定渲染，导致发票被当成
    通用表格 → 输出成一坨 OCR 坐标碎片；这是产物不可读的根因。

    分派规则：
      - ``json``：原样输出 data（schema + engine + 坐标），供 RAG / 资料库接入
      - ``html``：命中 schema → 严格版式；否则 → 坐标还原原图版面
      - ``md`` / ``docx``：命中 schema → 可读 schema 文本；否则 → 表格版面还原为
        Markdown 表（与 GUI 共用 ``table_layout``），非表格页回退识别文本
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
        # convert() 里已逐页抽过 schema 并存进 page。
        # 只有「本次就没启用 schema」（--schema none）时 page 里才没有 schema 键；
        # 此时绝不重抽——那等于用户明确说了不要，却又偷偷跑一遍。
        doc = p.get("schema", None)
        if doc:
            schema_pages += 1

        if fmt == "html":
            blk = (f"<section>{render_schema(p, 'html')}</section>" if doc
                   else _html_faithful_block(p))
        elif fmt == "txt":
            blk = render_schema(p, "txt") if doc else raw
        elif doc:
            # md / docx 且命中凭证 schema：严格按 schema 输出（字段化，最可读）
            blk = render_schema(p, "md")
        else:
            # md / docx 且无 schema：**表格版式优先**还原成真 Markdown 表
            # （两栏对照表/明细表在此成表；docx 经 md_to_docx 会转成真实 Word 表格），
            # 非表格页面 _layout_table_md 返回 "" → 回退识别原文。
            blk = _layout_table_md(p) or raw
        if prov:
            blk = f"<!-- engine:{engine} -->\n{blk}"
        blocks.append(blk)

    if fmt == "html":
        # 附加样式按「本次真正命中的 schema」取，而不是永远拼发票的 CSS：
        # 没有命中任何 schema 时，导出的 html 不该带着一套用不上的票面样式。
        # 忠实版式（原图垫底）的样式是通用的，无论有没有命中 schema 都可能用到
        extra_css = "\n".join(c for c in (schema_css(pages), faithful_css()) if c)
        stamp = _schema_stamp(pages)
        meta = f'<meta name="pic2edit-schema" content="{stamp}">' if stamp else ""
        text = ("<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>"
                f"{meta}<title>识别结果</title>"
                f"<style>{HTML_CSS}\n{extra_css}</style></head>"
                "<body>\n" + "\n".join(blocks) + "\n</body></html>")
    else:
        text = "\n\n".join(blocks)
        stamp = _schema_stamp(pages)
        if stamp and fmt in ("md", "docx"):
            # md/docx 用注释行记录版本：渲染后不可见，但保留了溯源信息。
            # txt 不加——纯文本里的 HTML 注释是可见噪声。
            text = f"<!-- schema: {stamp} -->\n\n{text}"

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
            use_cache, prov, vlm_model=None, schema_mode=MODE_AUTO):
    input_path = _safe_path(input_path)
    if not os.path.isfile(input_path):
        sys.stderr.write(f"[error] 输入文件不存在: {input_path}\n")
        sys.exit(2)

    # schema 选择在**任何昂贵动作之前**校验：写错 id 不该让用户等完 OCR 才报错。
    try:
        resolve_schema(schema_mode)
    except KeyError as e:
        sys.stderr.write(f"[error] {e.args[0]}\n")
        sys.exit(2)
    # 万一后面的流程抛异常，data 里仍要有一个形态完整的 schema 统计
    schema_stats = {"mode": schema_mode, "hits": 0, "pages": 0,
                    "applied": {}, "errors": []}

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
        override = parse_mask_rect(os.environ.get("PIC2EDIT_MASK_RECT"))
        if override is not None:
            mask_rect = override
            sys.stderr.write(
                f"[info] 使用手动指定的密码区掩膜 {tuple(round(v, 3) for v in mask_rect)}\n")
        else:
            try:
                mask_rect = locate_password_region(input_path)
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
            apply_password_mask(input_path, _mask_tmp, mask_rect)
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
        sys.stderr.write("[route] 判定手写体 → 跳过 RapidOCR，直走 VLM 兜底\n")

    def _apply_schema_stats(target_pages):
        """逐页抽 schema 并写回顶层统计。

        抽成函数是为了让「首次识别」与「缓存命中」两条路径**共用同一段代码**。
        此前缓存命中分支直接把 page 交给渲染、没走这一步，于是 page["schema"] 恒为
        None——同一份发票第一次转出票面版式、第二次（--cache）转出 OCR 原文，JSON 里的
        ``schema.hits`` 还是缓存里的旧值。这类"两条路径各写一遍"的漏做无法靠阅读发现，
        只能靠共用一个入口 + 测试守住。
        """
        st = apply_schemas(target_pages, schema_mode)
        if st["mode"] == MODE_NONE:
            # 「没启用」与「启用了但一个都没命中」必须能区分，否则事后无法解释
            # 同一批文件两次导出为什么长得不一样。
            st["note"] = "schema 未启用（--schema none）"
        elif st["hits"]:
            detail = "、".join(f"{k}×{v}" for k, v in st["applied"].items())
            sys.stderr.write(
                f"[schema] {st['hits']}/{len(target_pages)} 页命中凭证 schema（{detail}）\n")
        else:
            sys.stderr.write(f"[schema] 未命中任何凭证 schema（mode={st['mode']}）\n")
        return st

    # ---- 统一路由策略（必须在缓存之前算好：缓存键要含「是否跳过判别式」）----
    # 自动规避沙箱内存限制：若本进程虚拟内存软限制受限（如 WorkBuddy 沙箱 4GB），
    # RapidOCR/onnxruntime 加载模型会被 OOM killer 连父进程一起 SIGKILL(137)。
    # 此时跳过判别式，直接走 VLM 兜底（依赖本机 :8080 的 llama-server）。
    # 也可用环境变量 PIC2EDIT_SKIP_DISCRIMINATIVE=1 强制跳过。
    # 统一路由策略收敛到 ocr_app RoutingPolicy：页面类型(table/figure/html_table) + 手写预筛
    # + 沙箱内存受限 + 强制跳过，均跳过判别式直走 VLM 兜底；图表模式关闭幻觉守卫。
    # 与 GUI 使用同一套规则，避免两侧行为不一致（如防伪底纹发票 GUI 缺逃生口）。
    _force = env_force_skip()
    _policy = RoutingPolicy.for_page_type(
        mode, force_skip=_force, hw_detected=_hw, vmem_limited_gb=vmem_limited_gb(),
    )
    skip_disc = _policy.skip_discriminative

    # ---- A2 缓存命中：直接复用结构化结果并按请求格式渲染 ----
    cache_key = None
    if use_cache:
        # 缓存键基于「原始输入文件哈希」而非掩膜副本，避免掩膜开关导致同图不同键。
        cache_key = _cache_key_for(input_path, mode, vlm_model, skip_disc, mask_rect)
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
                # schema 是派生数据：写缓存时被剔除，命中时**必须重新抽取**。
                # 这正是缓存的设计目的——改进抽取逻辑后不用 bump CACHE_VERSION
                # 重跑昂贵的 OCR，命中页立刻按新逻辑重新抽取。
                data["schema"] = _apply_schema_stats(data.get("pages") or [])
                _msg = "[cache] 命中，直接复用识别结果"
                if _n_trim:
                    _msg += f"（对 {_n_trim} 页补做重复护栏截断）"
                sys.stderr.write(_msg + "\n")
                print(_render(data, fmt, out_path, input_path, prov, mode))
                return
            except Exception as e:
                # 不静默：坏缓存悄悄退化成全量识别，会让人以为"缓存根本没生效"。
                sys.stderr.write(f"[warn] 缓存读取失败，回退完整识别: {e}\n")

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
    # 不修改 pic2edit 任何代码（实现已下沉到内核 utils/model_select.py）。
    _models_dir = os.path.join(OCR_APP, "models")
    _sel = choose_vlm(
        _models_dir,
        vlm_model or settings_store.get("vlm.model", "auto"),
        settings_store.get("vlm.gpu_layers"),
    )
    sys.stderr.write(describe_vlm_choice(_sel) + "\n")
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
        # 用探针包装 VLM 后端，记录哪些页走了 VLM，得到逐页溯源。
        # 注意：必须按 skip_disc 透传 policy（含 skip_discriminative），绝不能硬编码 False——
        # 否则即便用户用 PIC2EDIT_SKIP_DISCRIMINATIVE=1 强制跳过判别式，
        # 流水线仍会先跑 RapidOCR，而 RapidOCR 在复杂底纹发票上返回非空噪声，
        # 导致 _quality_ok 通过、VLM 兜底被门控掉、DeepSeek-OCR 根本没被调用。
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
            page = {
                "page": i + 1,
                "text": (it.result or ""),
                "raw_text": raw_results.get(id(it), ""),
                "engine": _classify_engine(it, mode, skip_disc, disc_ok, table_avail, vlm_probe),
                "lines": lines,
                "img_w": W,
                "img_h": H,
            }
            # 只有导出 html 才带原图：垫底要用它内嵌成 data URI。别的格式带上是白占
            # 内存，json 导出更会顺手把临时路径写进产物。
            if fmt == "html":
                imgp = getattr(it, "image_path", None)
                if imgp and os.path.isfile(imgp):
                    page["image"] = imgp
            pages.append(page)

        # 逐页抽取凭证 schema：一处抽取，同时满足「渲染严格按 schema」与「json 供 RAG 接入」。
        # 注意：schema 是**派生数据**，绝不写进缓存——否则改进抽取逻辑后，缓存命中的旧
        # schema 会一直生效，必须 bump CACHE_VERSION 重跑昂贵的 OCR 才能生效。
        # 缓存只保留识别结果（raw_text / lines / 尺寸），schema 在每次读取时现算。
        #
        # 三态由 --schema 决定（none / auto / <id>）：
        #   none → apply_schemas 直接返回，连 schema 键都不写；
        #   其余 → 由注册表按 priority 逐个 detect/extract，前端不认识任何具体票种。
        schema_stats = _apply_schema_stats(pages)
    finally:
        if proc is not None and stop_server:
            # 经 EngineLauncher 整组回收（POSIX killpg / Windows CTRL_BREAK_EVENT），
            # 避免只杀直接子进程而留下 llama-server worker 孤儿；等价原 terminate+wait+kill。
            _el = EngineLauncher()
            _el.stop(proc)
            try:
                proc.wait(timeout=5)
            except Exception:
                _el.kill(proc)

    data = {"source": os.path.abspath(input_path),
            "vlm_fallback_invoked": bool(vlm_probe),
            "schema": schema_stats,          # 本次用了哪个 schema、命中几页（可溯源）
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
    # 派生数据由 _cache_payload 统一剔除，避免"写一处、读一处"各写一遍又漏掉。
    if use_cache and cache_key:
        try:
            with open(_cache_file(cache_key), "w", encoding="utf-8") as f:
                json.dump(_cache_payload(data), f, ensure_ascii=False)
        except Exception:
            pass
    # 唯一 stdout 输出：最终文件路径，供 skill 解析后 present_files
    print(_render(data, fmt, out_path, input_path, prov, mode))


def main():
    ap = argparse.ArgumentParser(description="pic2edit: 图片/PDF -> 可编辑文件")
    ap.add_argument("input", nargs="?", help="输入图片或 PDF 路径")
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
    ap.add_argument("--schema", default=MODE_AUTO, metavar="{none|auto|<id>}",
                    help="凭证 schema 三态：none=不启用（产出与从未有过 schema 的世界一致）；"
                         "auto=按页自动判定（默认）；<id>=强制指定（如 invoice）。"
                         "用 --list-schemas 查看本机可用的 id")
    ap.add_argument("--list-schemas", action="store_true",
                    help="列出本机可用的凭证 schema 后退出")
    args = ap.parse_args()

    if args.list_schemas:
        items = list_schemas()
        if not items:
            print("（未发现任何凭证 schema）")
        for it in items:
            print(f"{it['id']:<16} v{it['version']:<8} {it['kind']:<10} {it['title']}")
        return
    if not args.input:
        ap.error("需要输入路径（或使用 --list-schemas 查看可用 schema）")

    convert(args.input, args.format, args.mode, args.url,
            args.gpu_layers, args.out, args.stop_server, args.cache, args.prov,
            args.vlm_model, args.schema)


if __name__ == "__main__":
    main()
