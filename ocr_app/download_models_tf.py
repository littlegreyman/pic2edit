"""跨平台一键准备 Transformers 后端（Python 3.11 虚拟环境 + DeepSeek-OCR 模型）。

本脚本是唯一逻辑来源，可在 Windows / Linux / macOS 运行：
  1) .venv_tf/  —— 独立的 Python 3.11 虚拟环境，安装 torch/transformers/bitsandbytes
                   （这些在 3.13 上缺预编译 wheel，故需要独立的 3.11 环境）
  2) models_tf/ —— DeepSeek-OCR 模型（deepseek-ai/DeepSeek-OCR），通过 huggingface_hub
                   下载；hf.co 不通时自动切到 hf-mirror.com 镜像。

*.bat / *.sh 只是调用本脚本的薄壳。
"""
import os
import sys
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_TF = os.path.join(HERE, ".venv_tf")
MODELS_TF_DIR = os.path.join(HERE, "models_tf")
HF_REPO = "deepseek-ai/DeepSeek-OCR"
MODEL_LOCAL = os.path.join(MODELS_TF_DIR, "deepseek-ocr")
# HuggingFace 端点回退（hf.co 不通时切到国内镜像）。
HF_MIRRORS = ["https://huggingface.co", "https://hf-mirror.com"]


def _venv_tf_python():
    if os.name == "nt":
        return os.path.join(VENV_TF, "Scripts", "python.exe")
    return os.path.join(VENV_TF, "bin", "python")


def find_base_python():
    """Find a Python 3.11 (or 3.10) interpreter to bootstrap .venv_tf."""
    if os.name == "nt":
        candidates = [["py", "-3.11"], ["py", "-3.10"]]
    else:
        candidates = [["python3.11"], ["python3.10"]]
    for c in candidates:
        try:
            out = subprocess.run(c + ["--version"], capture_output=True, text=True, timeout=15)
            if out.returncode == 0:
                return c
        except Exception:
            continue
    # last resort: the interpreter running this script
    return [sys.executable]


def create_venv():
    py = _venv_tf_python()
    if os.path.isfile(py):
        return py
    base = find_base_python()
    print(f"[setup] creating .venv_tf with {base} ...")
    try:
        subprocess.check_call(base + ["-m", "venv", VENV_TF])
    except subprocess.CalledProcessError as e:
        print(f"[!] failed to create venv with {base}: {e}")
        return None
    return _venv_tf_python()


def install_requirements():
    py = _venv_tf_python()
    subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "pip"])
    req = os.path.join(HERE, "requirements_tf.txt")
    subprocess.check_call([py, "-m", "pip", "install", "-r", req])
    return py


def download_model(py):
    os.makedirs(MODELS_TF_DIR, exist_ok=True)
    last = None
    for ep in HF_MIRRORS:
        try:
            env = dict(os.environ, HF_ENDPOINT=ep)
            print(f"[dl] model via HF endpoint {ep} (repo={HF_REPO})")
            # pass paths through env to avoid shell-quoting issues on Windows
            env["_P2E_MODEL"] = HF_REPO
            env["_P2E_LOCAL"] = MODEL_LOCAL
            subprocess.check_call(
                [py, "-m", "pip", "install", "--upgrade", "huggingface_hub"], env=env
            )
            subprocess.check_call(
                [py, "-c",
                 "import os; from huggingface_hub import snapshot_download; "
                 "snapshot_download(os.environ['_P2E_MODEL'], local_dir=os.environ['_P2E_LOCAL']); "
                 "print('download OK')"],
                env=env,
            )
            return True
        except Exception as e:
            last = e
            print(f"[!] HF endpoint {ep} failed: {e}")
    print(f"[!] model download failed: {last}")
    return False


def main():
    print("[info] Setting up Transformers backend (Python 3.11 venv + DeepSeek-OCR)")
    py = create_venv()
    if not py or not os.path.isfile(py):
        print("[!] could not create .venv_tf. Ensure Python 3.11 is installed (`py -3.11`).")
        sys.exit(1)
    install_requirements()
    if os.path.isdir(MODEL_LOCAL) and os.path.isfile(os.path.join(MODEL_LOCAL, "config.json")):
        print(f"[skip] model already present: {MODEL_LOCAL}")
    else:
        if not download_model(py):
            print("[!] model download failed. You can place the model into models_tf/deepseek-ocr/ manually.")
            sys.exit(1)
    print("\nDONE. Next steps (fully local afterwards):")
    print("  1) run_transformers_server.py  (or .bat / .sh)  <- local server on :8001")
    print("  2) main.py  <- GUI; in Settings set backend=transformers, then convert.")


if __name__ == "__main__":
    main()
