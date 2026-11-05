# -*- coding: utf-8 -*-
"""schema 制作侧的命令行入口。

用法（在 ``ocr_app`` 目录下）：::

    python -m schemas.cli list                     # 已发现哪些 schema
    python -m schemas.cli validate <目录|id>        # 结构与版式对账
    python -m schemas.cli test <目录|id>            # 用 samples/ 跑命中率
    python -m schemas.cli new <id> [--title 名称]   # 生成一个空壳
    python -m schemas.cli pack <目录|id>            # 打进一个可分发的 .zip

为什么单独做一个 CLI，而不是塞进 ``pic2edit_convert.py``
--------------------------------------------------------
转换流程的入口应该只有"把文件转成可编辑文本"这一件事。制作 schema 是另一条
工作流：它的产物是**数据**（yaml + html），不该反过来变成转换脚本的一个子命令。
两条路分开走，才能保证"新增一种凭证不用改转换流程"。

退出码：0 全部通过；1 有 error；2 用法错误。警告不影响退出码。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):  # 允许 `python schemas/cli.py` 直接跑
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "schemas"

from . import get_registry, list_schemas, reset_registry  # noqa: E402
from .lint import check_samples, lint_package  # noqa: E402
from .registry import BUILTIN_ROOT, USER_ROOT  # noqa: E402

#: schema id 同时是**目录名**与注册表的 key，必须是一个受约束的标识符。
#: 不校验它，``new ../../foo`` 会写到用户目录之外并覆盖同名文件。
_SAFE_ID_RE = re.compile(r"[a-z][a-z0-9_\-]{0,31}")

_TEMPLATE_MANIFEST = """{
  "id": "{id}",
  "title": "{title}",
  "version": "0.1.0",
  "kind": "user",
  "priority": 50,
  "schema": "schema.yaml",
  "layout": "layout.html"
}
"""

_TEMPLATE_SCHEMA = """# {title} —— 字段与定位策略
#
# 三条要点（详见 ocr_app/schemas/README.md）：
#   1. detect 只回答"像不像我"，用票面上一定出现的词；all 里放"必须同时出现"的确认词。
#   2. 字段按 regex → 标签同行 → 坐标锚点 的顺序取值，写得越具体越稳。
#   3. table 分段必须写 start/stop 边界，否则上半部分的键值行会被当成数据行。
id: {id}
title: {title}
version: 0.1.0
priority: 50
strict: true

detect:
  any:
    - 换成票面上一定会出现的标题词
  all:
    - 换成更强的确认词（删掉本行则只靠 any）
  none: []

sections:
  - key: header
    label: 一、基本信息
    mode: kv
    fields:
      - key: example_no
        label: 示例编号
        regex: '示例编号[：:\\s]*([A-Za-z0-9\\-_/]+)'
        anchor: right
        required: true

  - key: remarks
    label: 二、备注
    mode: text
    regex: '备注[：:]\\s*(.+)'
"""

_TEMPLATE_LAYOUT = """<!--
  版式模板：只支持 {{ 字段 }} 与 data-repeat="表格分段key" 两种语法。
  <style> 里的内容会被抽出去拼进导出 HTML。
-->
<style>
.doc-card { border: 1px solid #d8d8d8; padding: 12px; font-size: 12px; }
.doc-card > h2 { font-size: 15px; text-align: center; margin: 0 0 10px; }
.doc-kv { border-collapse: collapse; width: 100%; }
.doc-kv td { border: 1px solid #e0e0e0; padding: 4px 8px; }
.doc-kv td.k { background: #fafafa; width: 22%; color: #555; }
</style>
<div class="doc-card">
  <h2>{title}</h2>
  <table class="doc-kv">
    <tr><td class="k">示例编号</td><td>{{ example_no }}</td></tr>
  </table>
  <p>{{ remarks }}</p>
</div>
"""


def _find_package(target: str) -> Optional[str]:
    """把 ``id`` 或路径解析成 schema 目录。"""
    if os.path.isdir(target):
        return os.path.abspath(target)
    reg = get_registry()
    for p in reg.providers:
        if p.id == target:
            # 注册表写入的是 package_dir（不是 pkg_dir）。此前读错属性名，导致
            # PIC2EDIT_SCHEMA_DIR 里的包永远找不到，只能靠下面按 id 猜路径兜底。
            pkg = getattr(p, "package_dir", None) or getattr(p, "pkg_dir", None)
            if pkg and os.path.isdir(pkg):
                return pkg
            for root in (BUILTIN_ROOT, USER_ROOT):
                cand = os.path.join(root, p.id)
                if os.path.isdir(cand):
                    return cand
    return None


def _print_issues(res: Dict[str, Any]) -> None:
    issues: List[Dict[str, str]] = res.get("issues") or []
    if not issues:
        print("  无问题")
        return
    for i in issues:
        mark = {"error": "错误", "warning": "警告", "info": "提示"}.get(i["level"], i["level"])
        print(f"  [{mark}] {i['where']}: {i['message']}")


def cmd_list(args) -> int:
    items = list_schemas()
    if not items:
        print("（未发现任何 schema）")
        return 0
    print(f"{'id':<20}{'版本':<10}{'来源':<12}{'优先级':<8}标题")
    for it in items:
        # 打印 origin（实际从哪个根目录加载）而不是 kind（包自报的类型）：
        # 用户把内置包拷到 ~/.pic2edit/schemas 改过之后，kind 仍是 "builtin"，
        # 只有 origin 能回答"生效的是我改的那份吗"。
        origin = it.get("origin") or it.get("kind") or "?"
        print(f"{it['id']:<20}v{it['version']:<9}{origin:<12}{it['priority']:<8}{it['title']}")
    errs = get_registry().errors
    if errs:
        print(f"\n有 {len(errs)} 个包未能加载：")
        for e in errs:
            print(f"  {e['path']}: {e['error']}")
    return 0


def cmd_validate(args) -> int:
    pkg = _find_package(args.target)
    if not pkg:
        print(f"找不到 schema: {args.target}", file=sys.stderr)
        return 2
    res = lint_package(pkg)
    print(f"校验 {res.get('id') or os.path.basename(pkg)}（{pkg}）")
    _print_issues(res)
    if args.samples:
        s = check_samples(pkg)
        if not s.get("samples"):
            print("  （没有样张，跳过命中率检查）")
        else:
            print(f"  样张 {s['samples']} 份，命中率 {s['hit_rate']:.0%}")
            for r in s.get("results") or []:
                flag = "命中" if r["hit"] else "未命中"
                print(f"    {r['sample']}: {flag}，字段 {r['fields']} 个，表格 {r['tables']}")
    return 0 if res["ok"] else 1


def cmd_test(args) -> int:
    pkg = _find_package(args.target)
    if not pkg:
        print(f"找不到 schema: {args.target}", file=sys.stderr)
        return 2
    s = check_samples(pkg)
    if s.get("error"):
        print(f"加载失败: {s['error']}", file=sys.stderr)
        return 1
    if not s.get("samples"):
        print("没有样张：请在 samples/ 下放 .txt 样张（可附同名 .lines.json 提供行框）")
        return 0
    print(f"{s['samples']} 份样张，命中率 {s['hit_rate']:.0%}")
    for r in s.get("results") or []:
        print(f"  {r['sample']}: {'命中' if r['hit'] else '未命中'}，"
              f"字段 {r['fields']} 个，表格 {r['tables']}")
    ok = s["hit_rate"] >= args.min_rate
    print("通过" if ok else f"未达门槛（要求命中率 ≥ {args.min_rate:.0%}）")
    return 0 if ok else 1


def _fill(template: str, sid: str, title: str) -> str:
    return template.replace("{id}", sid).replace("{title}", title)


def cmd_new(args) -> int:
    # id 同时是目录名与注册表 key：不校验就会出现 `new ../../foo` 写到用户目录之外、
    # 覆盖同名文件，以及一个永远无法被 `--schema <id>` 引用的怪名字。
    if not _SAFE_ID_RE.fullmatch(args.id or ""):
        print("id 必须是 1–32 位的 [a-z0-9_-]，且以字母开头", file=sys.stderr)
        return 2
    root = os.path.abspath(os.path.expanduser(args.dir or USER_ROOT))
    pkg = os.path.join(root, args.id)
    try:
        inside = os.path.commonpath([os.path.realpath(pkg), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:              # Windows 跨盘符时 commonpath 会抛
        inside = False
    if not inside:
        print(f"拒绝写入根目录之外: {pkg}", file=sys.stderr)
        return 2
    if os.path.exists(pkg) and not args.force:
        print(f"目录已存在：{pkg}（加 --force 覆盖）", file=sys.stderr)
        return 2
    os.makedirs(os.path.join(pkg, "samples"), exist_ok=True)
    title = args.title or args.id
    files = {
        # 刻意用 replace 而不是 str.format：模板里既有 JSON 的花括号，也有
        # {{ 字段 }} 占位，用 format 会把它们当格式化字段吃掉。
        "manifest.json": _fill(_TEMPLATE_MANIFEST, args.id, title),
        "schema.yaml": _fill(_TEMPLATE_SCHEMA, args.id, title),
        "layout.html": _fill(_TEMPLATE_LAYOUT, args.id, title),
        "samples/README.md": "把样张识别文本存成 .txt 放这里，然后跑 `schema test %s`。\n" % args.id,
    }
    for name, content in files.items():
        with open(os.path.join(pkg, name), "w", encoding="utf-8") as f:
            f.write(content)
    reset_registry()
    print(f"已生成 {pkg}")
    print("下一步：改 schema.yaml 的 detect 与字段 → 放样张 → python -m schemas.cli test "
          f"{args.id}")
    return 0


def cmd_pack(args) -> int:
    """把一个做好的 schema 目录打成可分发的 .zip。

    为什么要打成一个包而不是"拷目录"：分发时最怕的是**半份**包——用户只拷了
    schema.yaml 忘了 layout.html，或改完 yaml 没同步 manifest 的 version。
    因此先跑一遍 lint（id/version/占位对账），不通过就不打包；打完后打印 sha256，
    接收方可以核对（与引擎下载校验同一套思路）。
    """
    pkg = _find_package(args.target)
    if not pkg:
        print(f"找不到 schema: {args.target}", file=sys.stderr)
        return 2
    res = lint_package(pkg)
    sid = res.get("id") or os.path.basename(pkg)
    if not res["ok"] and not args.force:
        print("校验未通过，未打包（修完再试，或加 --force）:")
        _print_issues(res)
        return 1

    out = args.out or f"{sid}.zip"
    out = os.path.abspath(out)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(pkg):
            dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git"))
            for fn in sorted(files):
                if fn.endswith((".pyc", ".pyo")) or fn in (".DS_Store", "Thumbs.db"):
                    continue
                full = os.path.join(root, fn)
                arc = os.path.join(sid, os.path.relpath(full, pkg)).replace(os.sep, "/")
                z.write(full, arc)
                n += 1
    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    print(f"已打包 {n} 个文件 → {out}")
    print(f"sha256: {digest}")
    print(f"安装：解压到 {USER_ROOT}/ （顶层目录须为 {sid}/）")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="schemas.cli", description="pic2edit 凭证 schema 制作工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出已发现的 schema")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("validate", help="结构与版式对账")
    p.add_argument("target", help="schema id 或目录")
    p.add_argument("--samples", action="store_true", help="同时跑样张命中率")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("test", help="用 samples/ 跑命中率")
    p.add_argument("target", help="schema id 或目录")
    p.add_argument("--min-rate", type=float, default=0.8, help="命中率门槛（默认 0.8）")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("new", help="生成一个 schema 空壳")
    p.add_argument("id", help="新 schema 的 id（同时作为目录名）")
    p.add_argument("--title", default=None, help="中文名称")
    p.add_argument("--dir", default=None, help=f"输出根目录（默认 {USER_ROOT}）")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("pack", help="校验后打成可分发 .zip，并打印 sha256")
    p.add_argument("target", help="schema id 或目录")
    p.add_argument("--out", default=None, help="输出 zip 路径（默认 <id>.zip）")
    p.add_argument("--force", action="store_true", help="校验不通过也打包")
    p.set_defaults(func=cmd_pack)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
