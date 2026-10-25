"""Text post-processing helpers for OCR output.

DeepSeek-OCR (and greedy-decoded LLMs in general) can fall into a *degenerate
repetition loop* on dense pages: it emits the real content once, then fixates on
a short block and repeats it until max_tokens is exhausted (the symptom the user
saw: one license line printed ~200 times).

This module detects and removes that trailing loop. Two signals are used:

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
  A/B miss it. Signal C detects a long duplicated tail and cuts it. Guards
  (min length + min ratio + the earlier copy must sit fully before the tail)
  keep it from trimming legitimate short repeats.

  Either way we cut at the loop's true start, so the entire loop -- including its
  first cycle -- is discarded and the real content is preserved.
"""
import re

_BRACKET_YEAR = re.compile(r"\[20\d\d\]")
_LICENSE_MARK = "生产企业卫生许可证号："
_WS = re.compile(r"\s+")


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
    return text
