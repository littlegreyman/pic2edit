"""集中式日志配置（pic2edit）。

目标：让程序在出错时能记录足够的环境信息，便于准确定位问题。

- setup_logging()：配置名为 "pic2edit" 的 logger，同时输出到：
    * 控制台（stderr）
    * 日志文件 ~/.pic2edit/pic2edit.log（自动轮转，最多 3 个备份，单文件 5MB）
- env_summary(cfg=None)：收集 OS / Python / 关键依赖版本 / 后端配置，返回文本，
  用于出错时附在日志里。
- log_env_on_error(logger, cfg, extra)：记录一条 ERROR 级别的环境快照。

注意：本模块不依赖 PyQt，可在无图形环境下导入（便于测试）。GUI 的状态面板由
gui.py 自行用 Qt 实现并挂到同一个 logger 上（见 M1.5）。
"""
import logging
import logging.handlers
import os
import platform
import sys

LOG_DIR = os.path.join(os.path.expanduser("~"), ".pic2edit")
LOG_FILE = os.path.join(LOG_DIR, "pic2edit.log")
LOGGER_NAME = "pic2edit"

# 关键依赖（尽力获取版本，失败记为 n/a）
_DEP_MODULES = (
    "PySide6", "pypdfium2", "PIL", "requests", "markdown",
    "docx", "torch", "transformers", "huggingface_hub",
)


def _dependency_versions():
    versions = {}
    for mod in _DEP_MODULES:
        try:
            m = __import__(mod)
            versions[mod] = getattr(m, "__version__", "unknown")
        except Exception:
            versions[mod] = "n/a"
    return versions


def env_summary(cfg=None):
    """Return a human-readable environment snapshot (OS/Python/deps/backend cfg)."""
    lines = [
        f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {sys.version.split()[0]}",
    ]
    deps = _dependency_versions()
    lines.append(
        "Dependencies: " + ", ".join(f"{k}={v}" for k, v in deps.items())
    )
    if cfg is not None:
        def g(attr, default="?"):
            return str(getattr(cfg, attr, default))
        lines.append("Backend (selected): " + g("backend"))
        lines.append(f"  llama_cpp: url={g('llama_cpp_url')} model={g('llama_cpp_model')}")
        lines.append(f"  ollama:    url={g('ollama_url')} model={g('ollama_model')}")
        lines.append(
            f"  transformers: url={g('tf_url')} python={g('tf_python')} model={g('tf_model_path')}"
        )
    return "\n".join(lines)


def setup_logging(level=logging.INFO):
    """Configure the pic2edit logger with console + rotating file handlers."""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    # Avoid duplicate handlers if called more than once.
    for h in list(logger.handlers):
        logger.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        # If the file cannot be created (e.g. permission), fall back to console only.
        pass

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def get_logger(name=None):
    """Get a child logger under the pic2edit namespace."""
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)


def log_env_on_error(logger, cfg=None, extra=None):
    """Log an ERROR with the environment snapshot for easier diagnosis."""
    logger.error("=== ENVIRONMENT SNAPSHOT ===\n%s", env_summary(cfg))
    if extra:
        logger.error("Extra context: %s", extra)


def log_exception(logger, msg, cfg=None, extra=None):
    """Log an ERROR with the environment snapshot and the current traceback."""
    logger.error("%s", msg, exc_info=True)
    log_env_on_error(logger, cfg=cfg, extra=extra)
