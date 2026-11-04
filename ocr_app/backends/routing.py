"""路由策略（共享内核）：决定何时跳过判别式后端、是否启用幻觉守卫。

前端（GUI / Skill）应基于「页面类型 + 用户意图 + 运行环境」构造 RoutingPolicy，
而非各自硬编码 ``skip_discriminative``。统一收敛到此处，杜绝两个前端路由不一致
（如 GUI 对表格/图表跳过判别式、Skill 却未跳过，导致同一份防伪底纹发票两侧行为不同）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# 页面类型：对应输出格式 / 输入形态
# markdown: 普通文本页；table: 表格页；figure: 图表/公式页；html_table: HTML 表格。
_PAGE_TYPES = {"markdown", "table", "figure", "html_table"}

# 这些页面类型下 RapidOCR 会压平或漏识别（表格被压平、图/公式无法识别），
# 应跳过判别式、直接走 VLM 原生输出。
_SKIP_DISC_PAGE_TYPES = {"table", "figure", "html_table"}

# 这些页面类型关闭幻觉守卫（正常公式/化学符号等含大量拉丁字符，不应被误判丢弃）。
_NO_GUARD_PAGE_TYPES = {"figure"}


@dataclass(frozen=True)
class RoutingPolicy:
    skip_discriminative: bool
    guard_hallucination: bool

    @classmethod
    def for_page_type(
        cls,
        page_type: str,
        *,
        force_skip: bool = False,
        hw_detected: bool = False,
        vmem_limited_gb: float | None = None,
    ) -> "RoutingPolicy":
        """按页面类型与运行环境生成统一路由策略。

        page_type: 见 ``_PAGE_TYPES``。
        force_skip: 环境变量 ``PIC2EDIT_SKIP_DISCRIMINATIVE=1`` 等强制跳过。
        hw_detected: 手写体预筛命中（手写 RapidOCR 也难处理，直走 VLM）。
        vmem_limited_gb: 本进程虚拟内存软限制（GB）；<=6 视为受限沙箱，
            跳过判别式避免 onnxruntime 加载模型被 OOM killer 连父进程 SIGKILL。
        """
        skip = (
            force_skip
            or hw_detected
            or (vmem_limited_gb is not None and 0 < vmem_limited_gb <= 6)
            or (page_type in _SKIP_DISC_PAGE_TYPES)
        )
        guard = page_type not in _NO_GUARD_PAGE_TYPES
        return cls(skip_discriminative=skip, guard_hallucination=guard)


def vmem_limited_gb() -> float:
    """返回本进程虚拟内存软限制（GB）；无限制 / 不可用返回 0。

    沙箱检测：WorkBuddy 沙箱 ``ulimit -v=4GB`` 会让 onnxruntime 加载模型时
    被 OOM killer 连父进程一起 SIGKILL(137)。``<=6GB`` 视为受限，应跳过判别式。
    GUI 与 Skill 共用此实现，确保两侧在受限环境下路由一致。
    """
    try:
        import resource as _rs
        soft, _ = _rs.getrlimit(_rs.RLIMIT_AS)
    except Exception:
        return 0.0
    if soft == _rs.RLIM_INFINITY:
        return 0.0
    return soft / (1024 ** 3)


def env_force_skip() -> bool:
    """环境变量 ``PIC2EDIT_SKIP_DISCRIMINATIVE=1/true/yes`` 强制跳过判别式。"""
    return os.environ.get("PIC2EDIT_SKIP_DISCRIMINATIVE", "").lower() in ("1", "true", "yes")
