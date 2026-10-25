"""把 OCR 输出的扁平“键：值”文本转换为 Markdown 表格。

背景：DeepSeek-OCR 只在源文档本身是表格时才会输出 Markdown 表格
（实测 clinic_form.jpg 这类真表格 → 原生 `|` 行）；而对包装标签、
证照这类“两列键值对”版式，模型按阅读顺序输出一行流式文本，
且任何提示词都无法让它改输出表格（官方 5 种格式提示词均实测无效）。

本模块做确定性的后处理：把 “键：值 键：值 …” 流式文本解析成
两列表格（项目 | 内容），配合 export_docx 已有的 Markdown 表格
支持，可导出真正可编辑的 Word 表格。

解析规则（针对中文 OCR 文本调优）：
- 键 = 不含括号、含汉字、长度 <= 20、以全角/半角冒号结尾的段；
- 值常紧跟冒号无空格（“产品名称：得宝牌…”是一整段），所以要在
  段内部按“括号深度为 0 的冒号”切分键；
- 括号内的冒号不算键（如 “（代码：F）” 保持为值的一部分）；
- 键之间的所有文本都归入前一个键的值（保序，重复键保留多行）；
- 首个键之前的文本（若有）作为“备注”行；
- 输入本身已是 Markdown 表格（>=2 行以 | 开头）则原样返回。
"""

import re

_KEY_COLONS = ("：", ":")
_OPEN_BRACKETS = "（([{"
_CLOSE_BRACKETS = "）)]}"
_BRACKET_CHARS = _OPEN_BRACKETS + _CLOSE_BRACKETS
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_key_core(core: str) -> bool:
    """判断冒号前的部分是否像键（不含冒号本身）。"""
    if not core or len(core) > 20:
        return False
    if any(ch in _BRACKET_CHARS for ch in core):
        return False
    return bool(_CJK_RE.search(core))


def _split_run(run: str):
    """把一个非空格连续段切成 [(key, ...), (text, ...)] 序列。

    只有括号深度为 0 且前缀像键的冒号才产生切分；切分后继续扫描
    （一个段理论上可含多个键，如 “A：B：C”）。
    """
    segments = []
    buf = ""
    depth = 0
    for ch in run:
        if ch in _OPEN_BRACKETS:
            depth += 1
        elif ch in _CLOSE_BRACKETS:
            depth = max(0, depth - 1)
        if ch in _KEY_COLONS and depth == 0 and _is_key_core(buf):
            segments.append(("key", buf))
            buf = ""
            continue
        buf += ch
    segments.append(("text", buf))
    return segments


def _cell(text: str) -> str:
    """Markdown 表格单元格转义。"""
    return text.replace("|", "\\|").strip()


def kv_text_to_markdown_table(text: str) -> str:
    """把扁平键值文本转成两列 Markdown 表格；无法解析时原样返回。"""
    if not text or not text.strip():
        return text

    # 已经是表格（如模型对真表格输出的原生 `|` 行）→ 不做转换
    bar_lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    if len(bar_lines) >= 2:
        return text

    # 换行当普通空白处理（模型输出常是整段流式文本）
    flat = text.replace("\r", " ").replace("\n", " ")

    pairs = []           # [(key, value), ...]
    preamble_parts = []  # 首个键之前的文本
    cur_key = None
    cur_val_parts = []

    def finalize():
        if cur_key is not None:
            pairs.append((cur_key, " ".join(cur_val_parts).strip()))

    for raw in flat.split(" "):
        run = raw.strip()
        if not run:
            continue
        for kind, seg in _split_run(run):
            if kind == "key":
                finalize()
                cur_key = seg
                cur_val_parts = []
            elif cur_key is not None:
                cur_val_parts.append(seg)
            elif seg:
                preamble_parts.append(seg)

    finalize()
    if not pairs:
        # 一个键都没解析出来 → 不适合转表格，原样返回
        return text

    rows = []
    if preamble_parts:
        rows.append(("备注", " ".join(preamble_parts).strip()))
    rows.extend(pairs)

    lines = ["| 项目 | 内容 |", "| --- | --- |"]
    for k, v in rows:
        lines.append(f"| {_cell(k)} | {_cell(v)} |")
    return "\n".join(lines)
