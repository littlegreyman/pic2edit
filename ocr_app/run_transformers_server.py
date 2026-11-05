"""Auto-start the local Transformers server for DeepSeek-OCR.

Discovers, in this folder:
  - the Python 3.11 interpreter: config.tf_python or .venv_tf/Scripts/python.exe
  - the HF model dir:            config.tf_model_path or the first dir under
                                 models_tf/ that contains a config.json
  - quant:                       config.tf_quant (8bit/4bit/none)

Then launches backends/transformers_server.py (which loads the model ONCE and
keeps it resident in VRAM). Run from the app venv:

    ./.venv/Scripts/python.exe run_transformers_server.py
"""
import os
import sys
import glob
import subprocess
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS_TF_DIR = os.path.join(HERE, "models_tf")
SERVER = os.path.join(HERE, "backends", "transformers_server.py")
PORT = "8001"
DEFAULT_MODE = "Gundam"


def _arg_value(argv, flag):
    """极简 ``--flag value`` 取值。

    本脚本由 GUI 的启动器与批处理调用，参数只有 --port / --mode 两个，
    不值得为此引入 argparse 的完整用法（也便于从外部覆盖默认值）。
    """
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def build_cmd(py, model, quant, port=None, mode=None):
    """拼出真正启动模型服务的命令。

    ``--mode`` 是**模型加载期**分辨率档位（Tiny/Small/Base/Large/Gundam），
    此前这里从不传，于是 GUI 的分辨率下拉选了也没用。``--port`` 同理：
    调用方指定的服务地址必须与监听端口一致。
    """
    return [py, SERVER, "--model", model, "--quant", quant,
            "--port", str(port or PORT), "--mode", mode or DEFAULT_MODE]


def _find_model(model_path: str):
    if model_path:
        return model_path  # honor explicit setting; validated by main()
    # auto: first dir under models_tf/ that looks like a HF model
    if os.path.isdir(MODELS_TF_DIR):
        hits = glob.glob(os.path.join(MODELS_TF_DIR, "**", "config.json"), recursive=True)
        if hits:
            return os.path.dirname(sorted(hits)[0])
    return None


def _venv_tf_python():
    if os.name == "nt":
        return os.path.join(HERE, ".venv_tf", "Scripts", "python.exe")
    return os.path.join(HERE, ".venv_tf", "bin", "python")


def discover(tf_python: str, tf_model_path: str):
    py = tf_python or _venv_tf_python()
    if not os.path.isfile(py):
        py = shutil.which("python") or shutil.which("python3")
    model = _find_model(tf_model_path)
    return py, model


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # late import so this launcher runs under the 3.13 app venv without torch
    sys.path.insert(0, HERE)
    from config import Config, RESOLUTION_MODES
    cfg = Config.load()

    # 取值优先级：命令行 > settings.json > 默认。
    mode = _arg_value(argv, "--mode") or getattr(cfg, "resolution", DEFAULT_MODE)
    if mode not in RESOLUTION_MODES:
        sys.stderr.write(
            "WARN: 未知分辨率模式 %r，可选 %s；已回退 %s。\n"
            % (mode, "/".join(RESOLUTION_MODES), DEFAULT_MODE))
        mode = DEFAULT_MODE
    port = _arg_value(argv, "--port") or PORT

    py, model = discover(cfg.tf_python, cfg.tf_model_path)
    if not py or not os.path.isfile(py):
        sys.stderr.write(
            "ERROR: Python 3.11 interpreter not found.\n"
            "  Run download_models_tf.bat first (creates .venv_tf), or set tf_python in settings.\n"
        )
        sys.exit(1)
    if not model or not os.path.isdir(model):
        sys.stderr.write(
            "ERROR: DeepSeek-OCR model directory not found or invalid.\n"
            "  Run download_models_tf.bat to download it, or set a valid tf_model_path in settings.\n"
        )
        sys.exit(1)
    if not os.path.isfile(SERVER):
        sys.stderr.write(f"ERROR: {SERVER} not found.\n")
        sys.exit(1)

    cmd = build_cmd(py, model, cfg.tf_quant, port=port, mode=mode)
    print("[launch] " + " ".join(cmd))
    try:
        subprocess.run(cmd, cwd=HERE)
    except KeyboardInterrupt:
        print("\nserver stopped.")


if __name__ == "__main__":
    main()
