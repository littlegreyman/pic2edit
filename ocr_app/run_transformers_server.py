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


def main():
    # late import so this launcher runs under the 3.13 app venv without torch
    sys.path.insert(0, HERE)
    from config import Config
    cfg = Config.load()

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

    cmd = [py, SERVER, "--model", model, "--quant", cfg.tf_quant, "--port", PORT]
    print("[launch] " + " ".join(cmd))
    try:
        subprocess.run(cmd, cwd=HERE)
    except KeyboardInterrupt:
        print("\nserver stopped.")


if __name__ == "__main__":
    main()
