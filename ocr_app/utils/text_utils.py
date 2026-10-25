"""Text post-processing helpers for OCR output.

DeepSeek-OCR (and greedy-decoded LLMs in general) can fall into a *degenerate
repetition loop* on dense pages: it emits the real content once, then fixates on
a short block and repeats it until max_tokens is exhausted.

This module detects and removes that trailing loop. Several signals are used:

  Signal A (primary, very reliable for this model): the model switches its year
  notation to SQUARE BRACKETS (``[2021]``) only once it enters the loop, while
  real content uses full-width parens (``（2021）``). The first ``[20xx]`` is
  therefore almost exactly the loop onset.

  Signal B (fallback for same-notation loops): when the loop reuses the *same*
  notation as real content, detect a periodic tail (the text ends with >=3 exact
  repetitions of a cycle) and cut at the cycle's start.

  Signal C (fallback for a big block repeated once): the model sometimes emits
  the real content once, then regurgitates an entire *block* (hundreds of chars)
  exactly once at the end (e.g. re-lists the whole license block after the real
  ending). This has no bracket-year switch and isn't a tight periodic loop, so
  A/B miss it. Signal C detects a long duplicated tail (verbatim) and cuts it.

  Signal D (fallback for a *paraphrased* restart): the model sometimes restarts
  the whole document but paraphrases the opening field, so the block is no longer
  verbatim and C's anchored match misses it. Signal D
  detects a *document restart* by re-appearing "key：" anchors (a key that shows
  up 2-3 times overall, with one in the head and the last in the tail) and cuts
  from the last occurrence.

  Either way we cut at the loop's true start, so the entire loop -- including its
  first cycle -- is discarded and the real content is preserved.
"""
import re
from collections import Counter

_BRACKET_YEAR = re.compile(r"\[20\d\d\]")
_LICENSE_MARK = "生产企业卫生许可证号："
_WS = re.compile(r"\s+")
# 一个“键：”模式：中文/字母/数字/括号/斜杠连成的 1-20 字符，后接全角冒号。
# 用于重启检测——价值文本里的“（代码：F）”之类也会被误抓成“代码”，但那种键
# 出现极频繁（>=4 次）会被当成通用键排除。
_KEY_RE = re.compile(r"([\u4e00-\u9fffA-Za-z0-9（）()／/\-]{1,20})：")


def _norm_to_raw(text):
    """Map normalized (whitespace-stripped) index -> raw string index."""
    norm = _WS.sub("", text)
    raw_of_norm = [0] * len(norm)
    ni = 0
    for ri, ch in enumerate(text):
        if not ch.isspace():
            if ni < len(norm):
                raw_of_norm[ni] = ri
                ni += 1
    return norm, raw_of_norm


def _periodic_tail(norm, min_reps=3, max_period=1500):
    """Return (period, reps) if the tail is a >=min_reps exact repetition.

    Scans candidate periods from smallest upward; the first period for which the
    last `period*min_reps` chars equal `chunk*min_reps` (chunk = last `period`
    chars) is returned, together with how many trailing cycles are present.
    Returns (0, 0) if none.
    """
    n = len(norm)
    for p in range(12, max_period):
        if n < p * min_reps:
            break
        chunk = norm[-p:]
        if norm[-(p * min_reps):] == chunk * min_reps:
            reps = 0
            pos = n
            while pos >= p and norm[pos - p:pos] == chunk:
                reps += 1
                pos -= p
            if reps >= min_reps:
                return p, reps
    return 0, 0


def _dup_tail(text, anchor_len=48, min_len=120, min_ratio=0.12, max_len=4000, probe_mult=3):
    """Detect a *repeated block* at the tail, tolerant of small end differences.

    DeepSeek-OCR sometimes regurgitates a whole block once at the end, but the
    final entry of the copy can differ from the original (e.g. the last license
    shows a different province), so it is NOT an exact duplicate. We detect it
    by its *block-start signature*: the tail's first ``anchor_len`` chars must
    also appear earlier in the text, and a longer ``probe`` stretch (the block
    start) must match exactly there too -- this rules out coincidental short
    overlaps while still catching blocks that differ only near the end.

    Returns the length of the duplicated tail (to be cut) or 0 if none qualifies.

    Guards:
      * block length >= ``min_len`` and >= ``min_ratio`` of the whole text;
      * the matching earlier copy must start before the tail begins (no overlap).
    """
    n = len(text)
    upper = min(n // 2, max_len)
    for k in range(upper, min_len - 1, -1):
        if k < min_ratio * n:
            break
        suffix = text[-k:]
        sig = suffix[:anchor_len]
        idx = text.find(sig, 0, n - k)
        if idx == -1:
            continue
        # require a longer exact match of the block start to avoid coincidences
        probe = min(anchor_len * probe_mult, k)
        if text[idx:idx + probe] == suffix[:probe]:
            return k
    return 0


def trim_trailing_repetition(text, bracket_year_min=3, min_reps=3):
    """Remove a degenerate trailing repetition loop from OCR text.

    Returns the cleaned text (loop removed). If no loop is detected the original
    text is returned unchanged.
    """
    if not text:
        return text

    # --- Signal A: square-bracket year marks the loop onset -------------------
    brackets = list(_BRACKET_YEAR.finditer(text))
    if len(brackets) >= bracket_year_min:
        first = brackets[0].start()
        # back up to the start of the license block that contains this bracket,
        # so we don't leave a dangling half-block of real content.
        block = text.rfind(_LICENSE_MARK, 0, first)
        cut = block if block != -1 else first
        return text[:cut].rstrip()

    # --- Signal B: periodic tail (loop reuses the same notation) ---------------
    norm, raw_of_norm = _norm_to_raw(text)
    n = len(norm)
    if n < 200:
        return text
    period, reps = _periodic_tail(norm, min_reps)
    if period:
        loop_start_norm = n - reps * period
        if loop_start_norm > 0:
            cut_raw = raw_of_norm[loop_start_norm] if loop_start_norm < n else len(text)
            return text[:cut_raw].rstrip()

    # --- Signal C: a large block regurgitated once at the tail ----------------
    dup_len = _dup_tail(text)
    if dup_len:
        return text[: len(text) - dup_len].rstrip()

    # --- Signal D: 模型“重启”重列（开头字段被改写，导致块首不再逐字一致）--------
    # 这种退化 C 的精确锚点匹配抓不到；改用“键”重出现来识别：
    # 若某个“键”在全文恰好又出现（2~3 次，排除通用高频键如 地址），
    # 且最后一次落在尾部，即可判定模型从头重列，从最后一次出现处裁掉。
    restarted = _trim_restart_by_key(text)
    if restarted is not text:
        return restarted
    return text


def _trim_restart_by_key(text, head_frac=0.25, tail_frac=0.5, generic_max=4, boundary_gap=17):
    """Cut a *document restart* where the model begins re-listing from near the
    top but paraphrases the opening field, so the block is no longer verbatim.

    Strategy: scan "key：" anchors. A key that appears exactly 2-3 times overall
    (so it is not a generic high-frequency key like 地址/许可证号) AND appears in
    the head region AND whose last occurrence sits in the tail is a strong signal
    the model restarted. Once proven, walk backward from that last occurrence to
    the nearest *block boundary* (a gap to the previous key larger than the local
    in-cluster gaps) -- that boundary is the genuine restart seam -- and cut from
    the first key after it, so the whole regurgitated block (including any
    paraphrased header) is removed.
    """
    n = len(text)
    if n < 200:
        return text
    keys = [(m.start(), m.group(1)) for m in _KEY_RE.finditer(text)]
    if not keys:
        return text
    cnt = Counter(k for _, k in keys)
    head_end = int(n * head_frac)
    tail_start = int(n * tail_frac)
    for k, c in cnt.items():
        if c < 2 or c > generic_max:
            continue  # 只在“又出现 2~3 次、非通用高频键”时考虑
        positions = [p for p, kk in keys if kk == k]
        last = positions[-1]
        if last >= tail_start and any(p < head_end for p in positions):
            sp = sorted(p for p, _ in keys)
            try:
                i = sp.index(last)
            except ValueError:
                continue
            s = last
            prev = None
            for j in range(i, -1, -1):
                if prev is not None:
                    if prev - sp[j] > boundary_gap:
                        s = prev  # 边界后的第一个键才是重列起点
                        break
                prev = sp[j]
            return text[:s].rstrip()
    return text
