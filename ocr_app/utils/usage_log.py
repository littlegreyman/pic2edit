"""使用记录：把每次转换作业记录到 usage_log.jsonl（追加式 JSON 行）。

设计原则（见任务 #41 锁定决策）：仅做“记录 + 预设 + 上次成功复用”，
不引入任何自主调参 / 机器学习逻辑——参数仍由用户掌控。

- record(): 追加一条作业记录（时间、后端、输出格式、模型参数、成功/失败计数）。
- last_success(): 返回最近一条“全部成功”的记录（用于复用）。
- iter_entries(): 倒序/正序遍历历史。
"""
import os
import json
import time
from typing import List, Dict, Optional

from utils.app_home import writable_app_home


def _log_path() -> str:
    return os.path.join(writable_app_home(), "usage_log.jsonl")


def record(entry: Dict) -> None:
    """追加一条记录（entry 应为可 JSON 序列化的 dict）。"""
    try:
        path = _log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = dict(entry)
        entry.setdefault("ts", time.time())
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # 记录失败不应影响主流程
        pass


def iter_entries() -> List[Dict]:
    path = _log_path()
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def last_success() -> Optional[Dict]:
    """返回最近一条成功记录（n_fail==0 且 n_done>0），没有则返回 None。"""
    found = None
    for e in iter_entries():
        if e.get("n_fail", 1) == 0 and e.get("n_done", 0) > 0:
            found = e
    return found


def last_success_snapshot() -> Optional[Dict]:
    """抽取上次成功记录里的可复用快照：{prompt_template, model_params}。"""
    e = last_success()
    if not e:
        return None
    return {
        "prompt_template": e.get("prompt_template"),
        "model_params": e.get("model_params"),
    }
