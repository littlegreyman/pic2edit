"""全局配置：加载/保存用户设置（后端选择、模型路径、分辨率、提示词等）。

设置保存在用户主目录下的 ~/.pic2edit/config.json，保证离线、持久化。
"""
import json
import os
from dataclasses import dataclass, field, asdict

from utils.app_home import writable_app_home

# 配置目录：冻结态跟随安装盘（dirty install root），开发态为 ~/.pic2edit。
CONFIG_DIR = writable_app_home()
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

RESOLUTION_MODES = {
    "Tiny":   {"base_size": 512,  "image_size": 512,  "crop_mode": False},
    "Small":  {"base_size": 640,  "image_size": 640,  "crop_mode": False},
    "Base":   {"base_size": 1024, "image_size": 1024, "crop_mode": False},
    "Large":  {"base_size": 1280, "image_size": 1280, "crop_mode": False},
    "Gundam": {"base_size": 1024, "image_size": 640,  "crop_mode": True},
}

# NOTE: how each backend binds the input image (see docs/llamacpp_deepseek_ocr_api.md):
#   - Transformers: prepends "<image>\n" before model.infer() (the transformers path needs it).
#   - llama.cpp: the native deepseek-ocr template AUTO-INJECTS the media marker and binds
#     the image_url block to the vision encoder — so LlamaCppBackend sends NO marker in the
#     text. (Forcing --chat-template deepseek-ocr breaks this; do NOT do it.) Adding a marker
#     here would cause "2 markers vs 1 bitmap" HTTP 400.
#   - Ollama: uses its multimodal `images` field and must NOT see <image> in the text.
# So the templates here must NOT contain <image> (or any media marker).
# `<|grounding|>` is a model token that returns layout/bounding-box aware output and is
# valid across all backends; only the markdown template uses it (per official examples).
PROMPT_TEMPLATES = {
    "markdown": "<|grounding|>Convert the document to markdown.",
    "ocr":      "Free OCR.",
    "figure":   "Parse the figure.",
    # “表格”模式：模型对真表格会原生输出 Markdown 表格，但对“键：值”类
    # 版式（包装标签/证照）任何提示词都无法让它输出表格（实测），所以该
    # 模式用 markdown 提示词正常 OCR，再由 utils/table_utils.py 做确定性
    # 键值→表格 后处理（worker 按 prompt_template == "table" 触发）。
    "table":    "<|grounding|>Convert the document to markdown.",
    # “合并单元格”表格（发票等复杂表格）：让 VLM 输出带 rowspan/colspan 的 HTML 表格，
    # 再由 utils/table_html.py 解析、export_docx 用 cell.merge() 还原合并单元格。
    # 注意：仍需 VLM 真正吐出 HTML（普通“键：值”版式仍是 Markdown 后处理更合适）。
    "html_table": "<|grounding|>Convert complex tables (e.g. invoices with merged cells) to HTML with rowspan/colspan.",
}

# 给 GUI 显示用的中文标签（value 为 PROMPT_TEMPLATES 的 key）
PROMPT_LABELS = {
    "markdown": "Markdown（默认）",
    "ocr":      "纯文本 OCR",
    "figure":   "图表/公式",
    "table":    "表格（键值→表）",
    "html_table": "表格（HTML/合并单元格）",
}


@dataclass
class ModelParams:
    """模型采样参数（主要作用于 llama.cpp 本地引擎）。

    默认值即已用真机反复验证的“不退化”组合：
    - temperature=0    贪心/确定性（temp>0 会让 DeepSeek-OCR 随机退化、整篇重列）；
    - repeat_penalty=1.10  比默认 1.0 强、又不会像 1.15+ 那样提前吐 EOS/英文乱入；
    - repeat_last_n=4096   关键：默认 64 只往后看 64 token，对“整篇重列”长程退化无效，
                          放大到 4096 才真正抑制；
    - max_tokens=16384   给完整文档留余量（贪心不会 runaway）。
    repeat_last_n=0 表示交给引擎默认（不强制）。
    """

    temperature: float = 0.0
    repeat_penalty: float = 1.10
    repeat_last_n: int = 4096
    max_tokens: int = 16384


@dataclass
class Config:
    backend: str = "llama_cpp"         # "ollama" | "transformers" | "llama_cpp"
    # Ollama 后端
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "deepseek-ocr"
    # llama.cpp 后端（本地 llama-server 的 OpenAI 兼容 API）
    llama_cpp_url: str = "http://localhost:8080"
    llama_cpp_model: str = "deepseek-ocr"
    # Transformers 后端（需要独立的 Python 3.11 环境，模型常驻本地服务）
    tf_url: str = "http://localhost:8001"   # transformers_server.py 的 OpenAI 兼容地址
    tf_python: str = ""                # 指向 transformers 环境(3.11)的 python 解释器
    tf_model_path: str = ""            # 本地 HF 模型目录，如 deepseek-ai/DeepSeek-OCR 的本地缓存
    tf_quant: str = "8bit"             # none | 8bit | 4bit（仅用于启动服务）
    # 通用
    resolution: str = "Gundam"
    prompt_template: str = "markdown"
    output_dir: str = ""               # 导出目录，空则用输入文件同目录
    # 模型采样参数（GUI 设置面板可改；默认值见 ModelParams 说明）
    model_params: ModelParams = field(default_factory=ModelParams)
    # 上次成功参数快照（仅记录，便于“应用上次成功 / 自动复用”）
    last_success: dict = field(default_factory=dict)
    # 启动时是否自动套用上次成功参数（模型参数 + 输出格式）
    auto_reuse_last_success: bool = True

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> "Config":
        if not os.path.exists(CONFIG_PATH):
            return cls()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = cls()
            for k, v in data.items():
                if hasattr(cfg, k):
                    if k == "model_params" and isinstance(v, dict):
                        cfg.model_params = ModelParams(**v)
                    else:
                        setattr(cfg, k, v)
            return cfg
        except Exception:
            return cls()

    def prompt(self) -> str:
        return PROMPT_TEMPLATES.get(self.prompt_template, PROMPT_TEMPLATES["markdown"])


# 模型参数预设（GUI 设置面板一键套用；不引入任何自动调参/ML 逻辑）
MODEL_PARAM_PRESETS = {
    "默认（稳定，推荐）": ModelParams(),
    "强化抗退化": ModelParams(repeat_penalty=1.15, repeat_last_n=4096),
    "大上下文长文档": ModelParams(max_tokens=32768),
    "探索采样（不推荐）": ModelParams(temperature=0.2, repeat_penalty=1.10),
}
