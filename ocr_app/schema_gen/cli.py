# -*- coding: utf-8 -*-
"""生成器唯一入口：``python -m schema_gen``（在 ``ocr_app/`` 下运行）。

与 ``schemas.cli`` 是**两个入口**：后者校验/分发已经做好的包，本入口从外部
标准原件造包。产物默认落 ``generated/<id>``，**不自动安装**——调用方按
C10/C12 自行拷到 ``$PIC2EDIT_SCHEMA_DIR`` / ``~/.pic2edit/schemas/`` 覆盖安装。

stdout 只有产物绝对路径（便于脚本消费），诊断一律走 stderr。
退出码：0 成功且自检通过；1 自检未通过；2 用法/输入/输出错误。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from typing import Any, Dict, Optional, Sequence

from . import emit, ir, readers, sources, verify

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _utc(ts: Optional[str], path: Optional[str] = None) -> str:
    if ts:
        return ts
    if path and os.path.isfile(path):
        return _dt.datetime.fromtimestamp(
            os.path.getmtime(path), _dt.timezone.utc).isoformat(timespec="seconds")
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m schema_gen",
        description="从标准原件生成「数据格式齐全、定位策略待补」的 schema 包")
    ap.add_argument("--source", choices=("xsd",), default="xsd",
                    help="输入源（当前只支持官网 XSD 包）")
    ap.add_argument("--member", default="PPE.xsd",
                    help="XSD 包里的成员文件名（大小写不敏感）")
    ap.add_argument("--id", default="ppe_card", help="包 id（1–32 位小写标识符）")
    ap.add_argument("--title", default="固定资产卡片数据格式骨架")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--standard", default=sources.STANDARD_PPE)
    ap.add_argument("--detect-hint", default=None,
                    help="保守 detect 关键词，默认用标准号（票面不会出现）")
    ap.add_argument("--out", default=None, help="输出目录，默认 generated/<id>")
    ap.add_argument("--cache-dir", default=None, help="原件缓存目录")
    ap.add_argument("--offline", action="store_true", help="只读已校验缓存，不联网")
    ap.add_argument("--frozen-time", default=None,
                    help="冻结 generated_at，使两次生成逐字节一致")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的非空输出目录")
    ap.add_argument("--no-verify", action="store_true", help="跳过生成后自检")
    return ap


def generate(args: argparse.Namespace) -> Dict[str, Any]:
    hint = args.detect_hint or args.standard
    out = args.out or os.path.join(_APP_ROOT, "generated", args.id)
    emit.safe_id(args.id)

    # 先 fetch 拿到原件路径，provenance 的 retrieved_at 用文件真实 mtime
    # （缓存命中时反映的是当初下载时间，不是本次运行时间）。
    path = sources.fetch("xsd", cache_dir=args.cache_dir, offline=args.offline)
    data = sources.read_member("xsd", args.member,
                               cache_dir=args.cache_dir, offline=args.offline)
    model = readers.read_xsd(data, member=args.member)
    prov = sources.provenance("xsd", _utc(None, path))   # 原件下载时间取文件 mtime
    generated_at = _utc(args.frozen_time)                # 生成时间取现在（可冻结）

    gen_ir = ir.build_ir(
        model, schema_id=args.id, title=args.title, version=args.version,
        standard=args.standard, detect_hint=hint,
        sources=[prov], generated_at=generated_at)
    result = emit.write_package(gen_ir, out, force=args.force)
    return {"ir": gen_ir, "model": model, **result}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = generate(args)
    except (sources.SourceError, readers.ReaderError, emit.EmitError) as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    except Exception as e:                     # 不把栈丢给用户，但保留类型信息
        print(f"[error] 生成失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 2

    gen_ir = result["ir"]
    print(f"[gen] {gen_ir.xsd_member}: {len(result['model'].entities)} 实体 / "
          f"{gen_ir.field_count} 字段 / {len(gen_ir.sections)} 段",
          file=sys.stderr)
    print(f"[gen] 包目录：{result['pkg']}（未自动安装）", file=sys.stderr)
    if args.no_verify:
        print("[gen] 已跳过自检（--no-verify）", file=sys.stderr)
    else:
        report = verify.verify(result["pkg"], gen_ir)
        print(verify.format_report(report), file=sys.stderr)
        if not report["ok"]:
            print("[gen] 自检未通过，产物保留供排查，但不算交付", file=sys.stderr)
            return 1
    print(result["pkg"])
    return 0


__all__ = ["build_parser", "generate", "main"]
