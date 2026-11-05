"""按显卡显存自动选择视觉模型档位（纯 stdlib，零 pic2edit 依赖）。

背景
----
pic2edit 的 ``run_llamacpp_server.discover()`` 固定优先挑 ``*Q4_K_M*.gguf``——那是
"能在 8GB 显卡上跑"的保守默认，但在 8GB / 12GB 卡上白白浪费了精度。本模块的作用是
**只在启动命令里替换模型与 mmproj 两个文件名**，让同一份 pic2edit 代码在不同机器上
自动选到合适的量化：

===========  ==========================  ======================
显存          选择                        说明
===========  ==========================  ======================
≥ 6.5 GB     ``q8_0``（3.13 GB）          8GB 卡首选；精度明显优于 Q4
3–6.5 GB     ``Q4_K_M``（1.95 GB）        4GB 笔记本的唯一可行档
< 3 GB       ``Q4_K_M`` + ``--n-gpu-layers 0``  显存太小，退 CPU 推理
无独立显卡    ``Q4_K_M`` + ``--n-gpu-layers 0``  同上
===========  ==========================  ======================

``bf16``（5.88 GB + 0.83 GB mmproj）不给自动档用——8GB 卡再叠加 KV cache 极易 OOM，
只在 ``vlm.model`` 显式指定 ``bf16`` 时才启用。

**本模块不修改 pic2edit 任何文件**，只读取它的 ``models/`` 目录并产出启动参数。
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple

# 显存分档阈值（MB）。留足余量给 KV cache 与 CUDA 上下文，避免"加载成功但推理 OOM"。
_TIER_Q8_MIN_MB = 6500
_TIER_GPU_MIN_MB = 3000

# 档位 → (模型 glob, mmproj glob)。按顺序尝试，先精确匹配再退化。
_TIER_PATTERNS: Dict[str, Tuple[list, list]] = {
    "q8": (["*q8_0*.gguf"], ["*mmproj*q8_0*.gguf"]),
    "q4": (["*Q4_K_M*.gguf"], ["*mmproj*Q4_K_M*.gguf"]),
    "bf16": (["*bf16*.gguf"], ["*mmproj*bf16*.gguf"]),
}

_MODEL_ALIASES = {
    "": "auto", "auto": "auto",
    "q8": "q8", "q8_0": "q8", "8bit": "q8",
    "q4": "q4", "q4_k_m": "q4", "4bit": "q4",
    "bf16": "bf16", "fp16": "bf16", "full": "bf16",
    "cpu": "cpu",
}


def _nvidia_smi_paths() -> list:
    """返回候选 nvidia-smi 路径（PATH 优先，其次是 Windows 常见安装位置）。"""
    cands = ["nvidia-smi"]
    if os.name == "nt":
        cands += [
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe"),
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ]
    else:
        cands += ["/usr/bin/nvidia-smi", "/usr/local/bin/nvidia-smi"]
    return cands


def detect_gpu() -> Optional[Dict[str, Any]]:
    """探测 NVIDIA 显卡与显存。

    Returns:
        ``{"name": str, "vram_mb": int}``；无 NVIDIA 驱动/显卡时返回 ``None``。
    """
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        # 不弹黑窗（CREATE_NO_WINDOW），否则每次调用都在用户桌面闪一下
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for exe in _nvidia_smi_paths():
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, **kwargs
            )
            if out.returncode != 0 or not out.stdout.strip():
                continue
            first = out.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first.split(",")]
            if len(parts) < 2:
                continue
            name = parts[0]
            vram_mb = int(float(parts[1]))
            return {"name": name, "vram_mb": vram_mb}
        except Exception:
            continue
    return None


def _pick(models_dir: str, patterns: list, want_mmproj: bool) -> Optional[str]:
    """按 glob 模式在 models 目录里挑第一个匹配文件。

    Args:
        models_dir: 模型目录。
        patterns: 依次尝试的 glob 模式。
        want_mmproj: True 只保留 mmproj（视觉编码器）文件；False 只要主模型权重。
    """
    for pat in patterns:
        hits = glob.glob(os.path.join(models_dir, pat))
        hits += glob.glob(os.path.join(models_dir, "**", pat), recursive=True)
        hits = sorted({h for h in hits if os.path.isfile(h)})
        cands = [h for h in hits if ("mmproj" in os.path.basename(h).lower()) == want_mmproj]
        if cands:
            return cands[0]
    return None


def _any_mmproj(models_dir: str) -> Optional[str]:
    """兜底：任取一个 mmproj（模型自带视觉编码器，缺它整条 VLM 链路不可用）。"""
    return _pick(models_dir, ["*mmproj*.gguf"], want_mmproj=True)


def choose(models_dir: str, pref: str = "auto",
           gpu_layers_override: Optional[int] = None) -> Dict[str, Any]:
    """决定本次启动该用哪个模型文件。

    Args:
        models_dir: pic2edit 的模型目录（含 *.gguf）。
        pref: ``auto`` / ``q8`` / ``q4`` / ``bf16`` / ``cpu``；不认识的取值按 auto 处理。
        gpu_layers_override: 非 None 时强制该 GPU 层数（``0`` = 纯 CPU）。

    Returns:
        ``{"tier", "model", "mmproj", "gpu_layers", "reason", "gpu_name", "vram_mb"}``。
        ``model`` 为 ``None`` 表示目录里找不到可用权重，调用方应回退 pic2edit 的 discover()。
    """
    pref = _MODEL_ALIASES.get(str(pref or "").strip().lower(), "auto")
    gpu = detect_gpu()
    vram = int(gpu["vram_mb"]) if gpu else 0
    gpu_name = gpu["name"] if gpu else "（未检测到 NVIDIA 显卡）"

    if pref == "auto":
        if vram >= _TIER_Q8_MIN_MB:
            tier, reason = "q8", f"显存 {vram}MB ≥ {_TIER_Q8_MIN_MB}MB，自动选 q8_0（精度优先）"
        elif vram >= _TIER_GPU_MIN_MB:
            tier, reason = "q4", f"显存 {vram}MB 在 {_TIER_GPU_MIN_MB}–{_TIER_Q8_MIN_MB}MB 之间，选 Q4_K_M（q8_0 大概率 OOM）"
        elif vram > 0:
            tier, reason = "cpu", f"显存仅 {vram}MB，不足以承载模型，退 CPU 推理"
        else:
            tier, reason = "cpu", "未检测到 NVIDIA 显卡，退 CPU 推理"
    elif pref == "cpu":
        tier, reason = "cpu", "配置显式指定 cpu"
    else:
        tier = pref
        reason = f"配置显式指定 {pref}"

    base_tier = "q4" if tier == "cpu" else tier
    model = _pick(models_dir, _TIER_PATTERNS[base_tier][0], want_mmproj=False)
    if model is None:
        # 显式档位找不到权重时不要静默降级到别的精度，交给调用方回退 pic2edit 默认
        sys.stderr.write(f"[warn] models 目录未找到 {base_tier} 档权重，回退 pic2edit 默认模型\n")
        return {"tier": tier, "model": None, "mmproj": None, "gpu_layers": None,
                "reason": reason + "（未找到对应权重）", "gpu_name": gpu_name, "vram_mb": vram}

    mmproj = _pick(models_dir, _TIER_PATTERNS[base_tier][1], want_mmproj=True) or _any_mmproj(models_dir)

    if gpu_layers_override is not None:
        gpu_layers: Optional[int] = int(gpu_layers_override)
    elif tier == "cpu":
        gpu_layers = 0
    else:
        gpu_layers = None  # None = 交给 llama.cpp 自行决定全部 offload

    return {"tier": tier, "model": model, "mmproj": mmproj, "gpu_layers": gpu_layers,
            "reason": reason, "gpu_name": gpu_name, "vram_mb": vram}


def describe(sel: Dict[str, Any]) -> str:
    """把选择结果格式化成一行可读日志。"""
    model_name = os.path.basename(sel.get("model") or "") or "（pic2edit 默认）"
    return (f"[model] 档位={sel.get('tier')} 模型={model_name} "
            f"显卡={sel.get('gpu_name')} 显存={sel.get('vram_mb')}MB ｜ {sel.get('reason')}")


if __name__ == "__main__":
    # 自测：打印本机探测与选择结果
    d = os.path.dirname(os.path.abspath(__file__))
    demo_dir = os.environ.get("PIC2EDIT_MODELS_DIR", "")
    print("显存探测:", detect_gpu())
    if demo_dir:
        print(describe(choose(demo_dir)))


# --------------------------------------------------------------------------
# 公开别名：门面用不带歧义的名字转出。GUI 侧也按显存选模型时用它，
# 免得 "choose" 这种过于通用的名字在 `from backend_api import *` 时撞名。
# --------------------------------------------------------------------------
choose_vlm = choose
describe_vlm_choice = describe
