"""原始 OCR 记录落盘与过期清理（纯 stdlib，零 pic2edit 依赖）。

为什么要单独落盘
----------------
交付物（md / html / json）给人看、给资料库接入，**不该夹带原始 OCR 全文**——那会把
正文撑成一坨噪声。但"无幻觉"要求每个字都能回溯到识别原文，所以原始全文需要一个
**可追溯但不污染正文**的落点：本模块把它写到 skill 目录下的 ``logs/``。

目录结构
--------
::

    <skill>/logs/
    └── 2026-11-01/
        └── 重庆增值税发票样.a1b2c3d4.ocr.json

按日期分目录，便于按保留天数整目录清理；文件名带源文件哈希前缀，同一张图多次识别
不会互相覆盖（可对比不同模型/参数的结果）。

清理策略
--------
只删除**文件名形如* ``<日期目录>/…*.ocr.json`` **的文件**，目录空了才顺手删目录；
**绝不使用递归删除**，即使 ``log.dir`` 被配置到 skill 之外也不会误伤别的数据。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

import settings_store

# 单页原始文本上限：模型循环时可能吐出几十万字符，日志里没必要留全量
_MAX_RAW_CHARS = 200_000
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def log_root() -> str:
    """返回日志根目录的绝对路径（``log.dir`` 相对路径按 skill 根目录解析）。"""
    configured = str(settings_store.get("log.dir", "logs"))
    if os.path.isabs(configured):
        return os.path.abspath(configured)
    return os.path.abspath(os.path.join(settings_store.SKILL_ROOT, configured))


def _sha8(path: str) -> str:
    """取源文件内容的 sha256 前 8 位；读不到文件时退化为路径哈希。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except Exception:
        h.update(os.path.abspath(path).encode("utf-8", "ignore"))
    return h.hexdigest()[:8]


def save_ocr_log(
    source: str,
    pages: List[Dict[str, Any]],
    *,
    vlm_model: Optional[str] = None,
    vlm_fallback_invoked: bool = False,
    trimmed_reps: int = 0,
    originals: Optional[Dict[int, str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """把本次识别的原始文本写入日志目录。

    Args:
        source: 输入文件路径。
        pages: 逐页结果，每项至少含 ``page`` / ``engine`` / ``raw_text``。
        vlm_model: 实际使用的视觉模型文件名（便于事后对比 q8_0 与 Q4_K_M 的差异）。
        vlm_fallback_invoked: VLM 是否真的被调用过。
        trimmed_reps: 重复护栏截断的次数（0 表示未触发）。
        originals: ``{id(item): 截断前原文}``，保留模型原始输出便于复现问题。
        extra: 额外想记的键值（如耗时、命令参数）。

    Returns:
        写入的文件路径；写失败返回 ``None``（**不抛异常**，日志失败不该阻断转换）。
    """
    try:
        root = log_root()
        day = datetime.date.today().isoformat()
        out_dir = os.path.join(root, day)
        os.makedirs(out_dir, exist_ok=True)

        stem = os.path.splitext(os.path.basename(source))[0] or "input"
        safe_stem = re.sub(r'[\\/:*?"<>|\s]+', "_", stem)[:60]
        out_path = os.path.join(out_dir, f"{safe_stem}.{_sha8(source)}.ocr.json")

        rec_pages: List[Dict[str, Any]] = []
        for i, p in enumerate(pages):
            raw = p.get("raw_text") or ""
            orig = (originals or {}).get(i, "")
            rec_pages.append({
                "page": p.get("page", i + 1),
                "engine": p.get("engine"),
                "raw_text": raw[:_MAX_RAW_CHARS],
                # 截断前原文：复现"模型循环"这类问题必须看原始输出
                "raw_text_original": (orig or "")[:_MAX_RAW_CHARS],
                "raw_chars": len(raw),
                "original_chars": len(orig or ""),
                "img_w": p.get("img_w"),
                "img_h": p.get("img_h"),
            })

        record: Dict[str, Any] = {
            "schema": "pic2edit-ocr/log@1",
            "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": os.path.abspath(source),
            "vlm_model": vlm_model,
            "vlm_fallback_invoked": bool(vlm_fallback_invoked),
            "trimmed_reps": int(trimmed_reps),
            "pages": rec_pages,
        }
        if extra:
            record["extra"] = extra

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return out_path
    except Exception as e:
        sys.stderr.write(f"[warn] 写入 OCR 日志失败（不影响转换结果）: {e}\n")
        return None


def purge(retention_days: Optional[int] = None) -> int:
    """删除超过保留天数的日志文件。

    只删除 ``<YYYY-MM-DD>/*.ocr.json``，目录空了才删目录 —— 不用 rmtree，避免误伤。

    Args:
        retention_days: 保留天数；None 时取 ``settings.json`` 的 ``log.retention_days``。

    Returns:
        删除的文件数。
    """
    if retention_days is None:
        try:
            retention_days = int(settings_store.get("log.retention_days", 10))
        except Exception:
            retention_days = 10
    if retention_days <= 0:  # 0/负数 = 不清理，避免误配置把日志全删了
        return 0

    root = log_root()
    if not os.path.isdir(root):
        return 0

    cutoff = datetime.date.today() - datetime.timedelta(days=retention_days)
    removed = 0
    try:
        for name in os.listdir(root):
            if not _DATE_DIR_RE.match(name):
                continue
            try:
                day = datetime.date.fromisoformat(name)
            except ValueError:
                continue
            if day >= cutoff:
                continue
            day_dir = os.path.join(root, name)
            if not os.path.isdir(day_dir):
                continue
            for fn in os.listdir(day_dir):
                if not fn.endswith(".ocr.json"):
                    continue
                fp = os.path.join(day_dir, fn)
                try:
                    if os.path.isfile(fp):
                        os.unlink(fp)
                        removed += 1
                except Exception:
                    pass
            try:
                if not os.listdir(day_dir):
                    os.rmdir(day_dir)
            except Exception:
                pass
    except Exception as e:
        sys.stderr.write(f"[warn] 清理过期 OCR 日志失败: {e}\n")
    return removed


def latest(source: str) -> Optional[str]:
    """查找某源文件最近一次的日志路径（便于"这张图当时识别成什么"的回溯）。

    Args:
        source: 源文件路径（按文件名主干匹配）。

    Returns:
        命中则返回日志文件绝对路径，否则 ``None``。
    """
    root = log_root()
    if not os.path.isdir(root):
        return None
    stem = os.path.splitext(os.path.basename(source))[0] or "input"
    safe_stem = re.sub(r'[\\/:*?"<>|\s]+', "_", stem)[:60]
    hits: List[str] = []
    for name in sorted(os.listdir(root), reverse=True):
        if not _DATE_DIR_RE.match(name):
            continue
        day_dir = os.path.join(root, name)
        if not os.path.isdir(day_dir):
            continue
        for fn in os.listdir(day_dir):
            if fn.startswith(safe_stem + ".") and fn.endswith(".ocr.json"):
                hits.append(os.path.join(day_dir, fn))
        if hits:
            break
    return hits[0] if hits else None
