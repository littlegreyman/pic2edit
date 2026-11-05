# -*- coding: utf-8 -*-
"""schema 注册表：目录 drop-in 发现，不靠代码注册表。

发现顺序（后者覆盖前者的同名 id）
--------------------------------
1. 内置：``ocr_app/schemas/<id>/manifest.json``（随包分发）
2. 环境变量 ``PIC2EDIT_SCHEMA_DIR``（``os.pathsep`` 分隔，测试与私有部署用）
3. 用户级：``~/.pic2edit/schemas/<id>/manifest.json``

为什么必须是目录发现而不是 ``_PROVIDERS = {"invoice": ...}`` 这样的字典：
只要新增一种凭证还得改 Python 再发版，"制作 schema" 与 "转换文档" 这两条工作流
就永远耦合。目录 drop-in 是它们分离的**技术保证**，不是风格偏好。

一个 schema 包最少只需要两个文件：

    <id>/
      manifest.json    # 元信息 + 检测提示词（纯数据）
      schema.yaml      # 字段与定位策略（声明式，Phase 4 起支持）
      layout.html      # 版式（可选）

需要更复杂的抽取时，才额外放 ``provider.py``（暴露 ``get_provider()``）。

坏包降级：任何一个包解析失败都只记一条告警并跳过，绝不让整个注册表崩掉。
理由是转换流程的底线是「没有 schema 也能跑」——一个用户写错的 yaml
不该让别人的发票转换失效。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

from .base import SchemaProvider, info_of

try:  # 内核内可用时接统一日志；脱离内核单独用也不至于 ImportError
    from utils.logging_setup import get_logger

    _log = get_logger("schemas")
except Exception:  # pragma: no cover - 独立运行兜底
    import logging

    _log = logging.getLogger("pic2edit.schemas")

BUILTIN_ROOT = os.path.dirname(os.path.abspath(__file__))
USER_ROOT = os.path.join(os.path.expanduser("~"), ".pic2edit", "schemas")
ENV_DIRS = "PIC2EDIT_SCHEMA_DIR"

MODE_NONE = "none"
MODE_AUTO = "auto"


def _read_manifest(pkg_dir: str) -> Optional[Dict[str, Any]]:
    p = os.path.join(pkg_dir, "manifest.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data.get("id"):
        raise ValueError("manifest.json 缺少 id")
    return data


def _load_by_package(name: str) -> Any:
    """内置包：按子包导入，相对 import 正常工作。"""
    return importlib.import_module(f"{__package__}.{name}")


def _load_by_path(py_path: str, mod_name: str) -> Any:
    """用户包：按路径加载，无需在 sys.path 上。"""
    spec = importlib.util.spec_from_file_location(mod_name, py_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {py_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


class SchemaRegistry:
    """schema 提供者的发现与缓存。线程内复用；``reload()`` 可强制重扫。"""

    def __init__(self, extra_dirs: Optional[Sequence[str]] = None) -> None:
        self._extra = list(extra_dirs or [])
        self._providers: Dict[str, SchemaProvider] = {}
        self.errors: List[Dict[str, str]] = []
        self._loaded = False

    # ---------- 发现 ----------
    def ensure(self) -> "SchemaRegistry":
        if not self._loaded:
            self.reload()
        return self

    def reload(self) -> "SchemaRegistry":
        self._providers = {}
        self.errors = []
        for root, origin in self._roots():
            self._scan(root, origin)
        self._loaded = True
        return self

    def _roots(self):
        """按优先级从低到高产出 (目录, 来源)。后扫描的覆盖先扫描的同名 id。"""
        yield BUILTIN_ROOT, "builtin"
        env = os.environ.get(ENV_DIRS, "")
        for d in env.split(os.pathsep):
            if d.strip():
                yield os.path.expanduser(d.strip()), "user"
        for d in self._extra:
            yield os.path.expanduser(d), "user"
        yield USER_ROOT, "user"

    def _scan(self, root: str, origin: str) -> None:
        if not os.path.isdir(root):
            return
        for name in sorted(os.listdir(root)):
            pkg = os.path.join(root, name)
            if not os.path.isdir(pkg) or name.startswith(("_", ".")):
                continue
            # 内置包里只有带 manifest.json 的子目录才是 schema 包
            try:
                manifest = _read_manifest(pkg)
            except Exception as e:
                self._fail(pkg, f"manifest.json 解析失败: {e}")
                continue
            if manifest is None:
                continue
            try:
                provider = self._build(pkg, name, manifest, origin)
            except Exception as e:
                self._fail(pkg, f"加载失败: {e}")
                continue
            if provider is None:
                continue
            pid = provider.id
            if pid in self._providers:
                _log.info("schema 覆盖: %s（%s 覆盖前者）", pid, pkg)
            self._providers[pid] = provider

    def _build(self, pkg: str, name: str, manifest: Dict[str, Any],
               origin: str) -> Optional[SchemaProvider]:
        # 1) 显式 Python 插件
        entry = manifest.get("entry")
        # 2) 声明式
        decl = manifest.get("schema") or (
            "schema.yaml" if os.path.isfile(os.path.join(pkg, "schema.yaml")) else None
        )
        if entry:
            py = os.path.join(pkg, entry)
            if not os.path.isfile(py):
                raise FileNotFoundError(f"entry 指向的文件不存在: {entry}")
            mod = (_load_by_package(name) if origin == "builtin"
                   else _load_by_path(py, f"pic2edit_schema_{manifest['id']}"))
            build = getattr(mod, "get_provider", None)
            if build is None:
                raise AttributeError("provider.py 必须暴露 get_provider()")
            provider = build(pkg, manifest)
        elif decl:
            from .runtime import build_provider  # 延迟导入：无 yaml 依赖时不加载

            provider = build_provider(pkg, manifest)
        else:
            raise ValueError("既没有 entry 也没有 schema.yaml，无法构造提供者")

        # manifest 是元信息的权威来源，覆盖插件里的默认值
        provider.id = manifest.get("id", provider.id)
        provider.title = manifest.get("title", provider.title)
        provider.version = str(manifest.get("version", provider.version))
        provider.kind = manifest.get("kind", origin)
        # origin 记录「这个包实际是从哪个根目录加载的」，与 kind（包自报的类型）分开：
        # 用户把内置包拷到 ~/.pic2edit/schemas 改过之后，kind 仍是 manifest 里的
        # "builtin"，只有 origin 能说明"生效的是你改的那份"。覆盖机制的全部意义
        # 就是让人敢改，改完必须能确认——否则机制废掉一半。
        provider.origin = origin
        provider.package_dir = pkg
        provider.priority = int(manifest.get("priority", getattr(provider, "priority", 50)))
        # layout 只对**声明式**（HTML 版式）有意义；插件式的 HTML 由 render_html()
        # 现算，不该声明 layout。这里仍兼容旧包，但 lint 会提示插件不要写 layout。
        layout = manifest.get("layout")
        if layout and not entry:
            provider.layout = layout
        return provider

    def _fail(self, pkg: str, msg: str) -> None:
        self.errors.append({"path": pkg, "error": msg})
        _log.warning("schema 包已跳过 %s: %s", pkg, msg)

    # ---------- 查询 ----------
    @property
    def providers(self) -> List[SchemaProvider]:
        """按 priority 升序（小者先试），同优先级按 id 稳定排序。"""
        return sorted(self._providers.values(),
                      key=lambda p: (getattr(p, "priority", 50), p.id))

    def get(self, pid: str) -> Optional[SchemaProvider]:
        return self._providers.get(pid)

    def ids(self) -> List[str]:
        return [p.id for p in self.providers]

    def infos(self) -> List[Dict[str, Any]]:
        return [dict(info_of(p)) for p in self.providers]

    # ---------- 应用 ----------
    def resolve(self, mode: str) -> Any:
        """把三态选择解析为：``None``（none）| ``"auto"`` | 具体 provider。"""
        m = (mode or MODE_AUTO).strip()
        if m == MODE_NONE:
            return None
        if m == MODE_AUTO:
            return MODE_AUTO
        p = self.get(m)
        if p is None:
            known = ", ".join(self.ids()) or "（无）"
            raise KeyError(f"未知 schema: {m!r}；可用: {known}")
        return p

    def candidates(self, mode: str) -> List[SchemaProvider]:
        target = self.resolve(mode)
        if target is None:
            return []
        if target == MODE_AUTO:
            return self.providers
        return [target]

    def apply(self, pages: Sequence[Dict[str, Any]], mode: str = MODE_AUTO) -> Dict[str, Any]:
        """对每页尝试抽取并就地写入 ``schema`` / ``schema_id`` / ``schema_version``。

        ``mode="none"`` 时**完全不碰 pages**（连键都不写）：下游据此判断
        「本次导出根本没启用 schema」，而不是「启用了但没命中」。

        写 ``schema_version`` 是为了让产物能回答「这份数据是按哪一版规则抽的」。
        schema 从 1.0.0 改到 1.1.0（比如补了一个字段）之后，历史产物若只记 id，
        就没法解释同一批文件的两次导出为什么长得不一样。
        """
        cands = self.candidates(mode)
        stats = {"mode": mode or MODE_AUTO, "hits": 0, "pages": len(pages),
                 "applied": {}, "versions": {}, "errors": list(self.errors)}
        if (mode or MODE_AUTO) == MODE_NONE:
            return stats
        for page in pages:
            doc, pid, ver = self._apply_one(page, cands)
            page["schema"] = doc
            page["schema_id"] = pid
            page["schema_version"] = ver
            if pid:
                stats["hits"] += 1
                stats["applied"][pid] = stats["applied"].get(pid, 0) + 1
                stats["versions"][pid] = ver
        return stats

    def _apply_one(self, page: Dict[str, Any], cands: Sequence[SchemaProvider]):
        text = page.get("raw_text") or page.get("text") or ""
        lines = page.get("lines") or []
        w = int(page.get("img_w") or 0)
        h = int(page.get("img_h") or 0)
        for p in cands:
            try:
                if not p.detect(text, lines, w, h):
                    continue
                doc = p.extract(text, lines, w, h)
            except Exception as e:      # 单个 schema 出错不能拖垮整篇导出
                _log.warning("schema %s 抽取异常（第 %s 页）: %s",
                             p.id, page.get("page", "?"), e)
                continue
            if doc:
                return doc, p.id, str(getattr(p, "version", "") or "")
        return None, None, None

    # ---------- 渲染 ----------
    def render(self, page: Dict[str, Any], fmt: str) -> str:
        """按页命中的 schema 渲染；未命中或格式不支持时返回 ""（调用方自行兜底）。"""
        pid = page.get("schema_id")
        doc = page.get("schema")
        if not pid or not doc:
            return ""
        p = self.get(pid)
        if p is None:
            return ""
        fmt = (fmt or "").lower()
        try:
            if fmt in ("md", "markdown", "docx"):
                return p.render_md(doc)
            if fmt == "txt":
                return p.render_txt(doc)
            if fmt in ("html", "htm"):
                return p.render_html(doc)
        except Exception as e:
            _log.warning("schema %s 渲染失败（%s）: %s", pid, fmt, e)
        return ""

    def css_for(self, pages: Sequence[Dict[str, Any]]) -> str:
        seen, out = set(), []
        for page in pages:
            pid = page.get("schema_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            p = self.get(pid)
            if p is None:
                continue
            try:
                c = p.css()
            except Exception:
                c = ""
            if c:
                out.append(c)
        return "\n".join(out)
