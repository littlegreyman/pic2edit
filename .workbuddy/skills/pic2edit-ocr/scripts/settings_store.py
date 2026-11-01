"""pic2edit-ocr skill 的设置读取（纯 stdlib，零 pic2edit 依赖）。

设计原则
--------
1. **单一事实来源**：所有可调参数集中在 skill 根目录的 ``settings.json``，代码里只留
   默认值兜底，避免"参数散落在源码各处"。
2. **环境变量优先级最高**：同名 ``PIC2EDIT_*`` 环境变量可临时覆盖文件值，便于一次性
   调试（如 ``PIC2EDIT_VLM_MODEL=q4 python pic2edit_convert.py ...``）。
3. **绝不因配置异常而崩溃**：文件缺失 / JSON 损坏 / 类型不符一律回退默认值，只打 warn。

对外接口
--------
- :func:`load` — 读取合并后的完整设置字典（带缓存）
- :func:`get` — 按点号路径取单个值，如 ``get("log.retention_days")``
- :func:`reload` — 清缓存后重新读取（长驻进程内改了配置时用）
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

# skill 根目录 = scripts/ 的上一级
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(SKILL_ROOT, "settings.json")

# 默认值：settings.json 缺失或缺键时用它兜底。改这里等于改"出厂设置"。
DEFAULTS: Dict[str, Any] = {
    "log": {
        "dir": "logs",            # 相对 skill 根目录；也可写绝对路径
        "retention_days": 10,     # 原始 OCR 日志保留天数
    },
    "vlm": {
        "model": "auto",          # auto | q8 | q4 | bf16 | cpu
        "gpu_layers": None,       # None = 按所选档位自动决定；整数则强制
    },
    "ocr": {
        # auto | true | false。auto＝不盲掩（普票/电子发票根本没有密码区，
        # 盲掩会把正常正文涂掉）；true＝强制掩膜；false＝明确不掩膜。
        "mask_password": "auto",
        "handwriting_screen": False,  # 是否启用 P0 手写/印刷预筛
    },
}

# 环境变量 → 设置点号路径。值统一按字符串处理后按目标类型转换。
_ENV_MAP = {
    "PIC2EDIT_LOG_DIR": "log.dir",
    "PIC2EDIT_LOG_RETENTION_DAYS": "log.retention_days",
    "PIC2EDIT_VLM_MODEL": "vlm.model",
    "PIC2EDIT_GPU_LAYERS": "vlm.gpu_layers",
    "PIC2EDIT_MASK_PASSWORD": "ocr.mask_password",
    "PIC2EDIT_HW_DISABLE": "ocr.handwriting_screen",  # 取反语义，见 _apply_env
}

_cache: Dict[str, Any] | None = None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """把 override 递归合并进 base 的副本；只覆盖同名键，保留 base 的其余默认值。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _coerce(value: str, like: Any) -> Any:
    """按默认值的类型把环境变量字符串转成目标类型；转换失败原样返回字符串。"""
    if isinstance(like, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        try:
            return int(value)
        except ValueError:
            return like
    if like is None:  # gpu_layers: None 表示"自动"，允许显式设成数字
        v = value.strip()
        if v.lower() in ("", "auto", "none", "null"):
            return None
        try:
            return int(v)
        except ValueError:
            return None
    return value


def _set_path(cfg: Dict[str, Any], dotted: str, value: Any) -> None:
    """按点号路径写入嵌套字典（中间层不存在则创建）。"""
    parts = dotted.split(".")
    cur = cfg
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _get_default(dotted: str) -> Any:
    cur: Any = DEFAULTS
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _apply_env(cfg: Dict[str, Any]) -> None:
    """把环境变量覆盖写进 cfg。PIC2EDIT_HW_DISABLE 语义取反。"""
    for env, dotted in _ENV_MAP.items():
        if env not in os.environ:
            continue
        raw = os.environ[env]
        if env == "PIC2EDIT_HW_DISABLE":
            # 设了 PIC2EDIT_HW_DISABLE=1 → 关闭手写预筛
            cfg_hw_off = raw.strip().lower() in ("1", "true", "yes", "on")
            _set_path(cfg, dotted, not cfg_hw_off)
        else:
            _set_path(cfg, dotted, _coerce(raw, _get_default(dotted)))


def load(force: bool = False) -> Dict[str, Any]:
    """读取完整设置（默认值 ← settings.json ← 环境变量）。

    Args:
        force: True 时忽略缓存重新读盘。

    Returns:
        三层合并后的设置字典。任何读取/解析异常都回退默认值，不抛异常。
    """
    global _cache
    if _cache is not None and not force:
        return _cache

    cfg = _deep_merge({}, DEFAULTS)
    if os.path.isfile(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
            if isinstance(file_cfg, dict):
                file_cfg.pop("_doc", None)  # 注释键不参与配置
                cfg = _deep_merge(cfg, file_cfg)
        except Exception as e:  # JSON 损坏 / 编码问题：不阻断主流程
            sys.stderr.write(f"[warn] 读取 {SETTINGS_PATH} 失败，改用默认设置: {e}\n")
    else:
        sys.stderr.write(f"[info] 未找到 settings.json，使用默认设置（可创建 {SETTINGS_PATH} 自定义）\n")

    _apply_env(cfg)
    _cache = cfg
    return cfg


def get(dotted: str, default: Any = None) -> Any:
    """按点号路径取设置值，如 ``get("log.retention_days", 10)``。"""
    cur: Any = load()
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return default if cur is None else cur


def reload() -> Dict[str, Any]:
    """清缓存后重新读取（长驻进程内改过配置文件时调用）。"""
    return load(force=True)
