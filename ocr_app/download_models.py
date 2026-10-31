"""跨平台一键准备 llama.cpp 后端（llama.cpp 官方预编译服务 + DeepSeek-OCR GGUF）。

本脚本是唯一的下载逻辑来源，可在 Windows / Linux / macOS 上运行：
  1) bin/<exe>  —— 官方预编译 llama.cpp server
                    Windows/Linux 用 CUDA 版，macOS 用 Metal 版；
                    通过 GitHub Release API 按当前系统/架构挑选正确的资产，
                    并带“镜像/CDN 回退”与“确定性 URL 兜底”，避免卡死在某一个源。
  2) models/    —— DeepSeek-OCR 的 GGUF（Q4_K_M，适配 ~8GB 显存）+ mmproj，
                    通过 huggingface_hub 下载，hf.co 不通时自动切到 hf-mirror.com。

*.bat / *.sh 只是调用本脚本的薄壳，真正的可移植逻辑都在这里。
"""
import os
import sys
import glob
import json
import shutil
import tarfile
import zipfile
import platform
import urllib.request
import urllib.error

# 引擎/模型目录：冻结态跟随安装盘（dirty install root），开发态为 ocr_app/ 下。
# 注意：单文件夹冻结模式下 __file__ 指向临时解压目录，绝不能用 dirname(__file__)
# 来确定需要持久化的模型/引擎位置。
from utils.app_home import data_root

HERE = data_root()
BIN_DIR = os.path.join(HERE, "bin")
MODELS_DIR = os.path.join(HERE, "models")

# ---------------- 可调整的参数 ----------------
REPO = "ggml-org/llama.cpp"
TAG = "b11853"
# Windows 优先使用的 CUDA 大版本；Linux/macOS 由 Release 资产自动挑选。
WIN_CUDA = "12.4"
HF_REPO = "sabafallah/DeepSeek-OCR-GGUF"
MODEL_PATTERNS = ["*Q4_K_M*.gguf", "mmproj*.gguf"]
# GitHub Release 直链回退镜像（仅在 github.com 失败时使用，best-effort）。
GITHUB_MIRRORS = [
    "https://github.com",
    "https://ghfast.top/https://github.com",
    "https://ghproxy.net/https://github.com",
    "https://mirror.ghproxy.com/https://github.com",
]
# HuggingFace 端点回退（hf.co 不通时切到国内镜像）。
HF_MIRRORS = [
    "https://huggingface.co",
    "https://hf-mirror.com",
]
LIB_EXTS = (".dll", ".so", ".dylib")
UA = {"User-Agent": "pic2edit-setup/1.0"}


# ---------------- 平台识别 ----------------
def detect_platform():
    """Return (system, machine) normalized: system in Windows/Linux/Darwin."""
    system = platform.system()  # 'Windows' / 'Linux' / 'Darwin'
    machine = platform.machine().lower()
    # normalize common aliases
    if machine in ("x86_64", "amd64"):
        machine = "x64"
    elif machine in ("aarch64", "arm64"):
        machine = "arm64"
    return system, machine


# ---------------- GitHub Release 资产挑选 ----------------
def fetch_release_assets():
    """Query GitHub API for this tag's assets; raise on failure."""
    url = f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.load(r)
    return [(a["name"], a["browser_download_url"]) for a in data.get("assets", [])]


def fallback_engine_url(system, machine):
    """Deterministic GitHub URL when the API is unavailable (matches real assets)."""
    base = f"https://github.com/{REPO}/releases/download/{TAG}/"
    if system == "Windows":
        return base + f"llama-{TAG}-bin-win-cuda-{WIN_CUDA}-x64.zip"
    if system == "Linux":
        return base + f"llama-{TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz"
    if system == "Darwin":
        if machine == "arm64":
            return base + f"llama-{TAG}-bin-macos-arm64.tar.gz"
        return base + f"llama-{TAG}-bin-macos-x64.tar.gz"
    return None


def select_engine(candidates, system, machine):
    """Given [(name, url)] candidates, return (name, url) for the engine zip/tar."""
    # Priority-ordered CUDA versions to prefer (stable first).
    cuda_prefs = ["12.4", "12.8", "13.4"]

    def first(pred):
        for n, u in candidates:
            if pred(n):
                return n, u
        return None, None

    if system == "Windows":
        for cv in cuda_prefs:
            n, u = first(lambda x: x == f"llama-{TAG}-bin-win-cuda-{cv}-x64.zip")
            if u:
                return n, u
        return first(lambda x: x.startswith(f"llama-{TAG}-bin-win-cuda-") and x.endswith("-x64.zip"))
    if system == "Linux":
        for cv in cuda_prefs:
            n, u = first(lambda x: x == f"llama-{TAG}-bin-ubuntu-cuda-{cv}-x64.tar.gz")
            if u:
                return n, u
        return first(lambda x: x.startswith(f"llama-{TAG}-bin-ubuntu-cuda-") and x.endswith("-x64.tar.gz"))
    if system == "Darwin":
        if machine == "arm64":
            n, u = first(lambda x: x == f"llama-{TAG}-bin-macos-arm64.tar.gz")
            if u:
                return n, u
            return first(lambda x: x.startswith(f"llama-{TAG}-bin-macos-arm64"))
        return first(lambda x: x.startswith(f"llama-{TAG}-bin-macos-x64"))
    return None, None


def select_cudart(candidates, system):
    """Return the CUDA runtime archive (name, url) for win/linux (None on macOS)."""
    cuda_prefs = ["12.4", "12.8", "13.4"]
    if system == "Windows":
        for cv in cuda_prefs:
            for n, u in candidates:
                if n == f"cudart-llama-bin-win-cuda-{cv}-x64.zip":
                    return n, u
        for n, u in candidates:  # any win cuda build as fallback
            if n.startswith("cudart-llama-bin-win-cuda-") and n.endswith("-x64.zip"):
                return n, u
    if system == "Linux":
        for cv in cuda_prefs:
            for n, u in candidates:
                if (n.startswith(f"cudart-llama-{TAG}-bin-ubuntu-cuda-{cv}-")
                        and n.endswith("-x64.tar.gz")):
                    return n, u
        for n, u in candidates:  # any ubuntu cuda build as fallback
            if n.startswith("cudart-llama-") and "ubuntu-cuda" in n and n.endswith("-x64.tar.gz"):
                return n, u
    return None, None


# ---------------- 下载（带进度 + 镜像回退） ----------------
def _stream(url, dest, label):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", "0") or "0")
        downloaded = 0
        chunk = 1024 * 256
        with open(dest, "wb") as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                downloaded += len(buf)
                if total:
                    pct = downloaded * 100 // total
                    sys.stderr.write(f"\r{label}: {pct:3d}% ({downloaded//1024//1024}MB/{total//1024//1024}MB)")
                    sys.stderr.flush()
    if total:
        sys.stderr.write("\n")
        sys.stderr.flush()


def download_file(url, dest, label):
    """Try each GitHub mirror in turn; return True on success."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for mirror in GITHUB_MIRRORS:
        wrapped = url.replace("https://github.com", mirror, 1) if "github.com" in url else url
        try:
            print(f"[dl] {label} <- {wrapped}")
            _stream(wrapped, dest, label)
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                return True
        except Exception as e:  # network/HTTP error -> try next mirror
            print(f"[!] mirror {mirror} failed: {e}")
    return False


# ---------------- 解压 + 收集动态库 ----------------
def extract_archive(path, dest):
    if path.endswith(".zip"):
        with zipfile.ZipFile(path, "r") as z:
            z.extractall(dest)
    elif path.endswith(".tar.gz") or path.endswith(".tgz"):
        with tarfile.open(path, "r:gz") as t:
            t.extractall(dest)
    else:
        raise RuntimeError(f"unsupported archive: {path}")


def _find_exe_under(root):
    cands = [os.path.join(root, "llama-server.exe"), os.path.join(root, "llama-server")]
    for c in cands:
        if os.path.isfile(c):
            return c
    hits = glob.glob(os.path.join(root, "**", "llama-server.exe"), recursive=True)
    if not hits:
        hits = glob.glob(os.path.join(root, "**", "llama-server"), recursive=True)
    return sorted(hits)[0] if hits else None


def gather_libs(bin_dir):
    """Copy every .dll/.so/.dylib found anywhere under bin_dir next to the exe,
    so the dynamic loader finds them regardless of zip layout. On POSIX, make the
    exe executable (the tar.gz preserves the bit, but be defensive)."""
    exe = _find_exe_under(bin_dir)
    if not exe:
        return None
    exe_dir = os.path.dirname(exe)
    for ext in LIB_EXTS:
        for lib in glob.glob(os.path.join(bin_dir, "**", "*" + ext), recursive=True):
            try:
                target = os.path.join(exe_dir, os.path.basename(lib))
                if os.path.abspath(lib) != os.path.abspath(target):
                    shutil.copy2(lib, target)
            except Exception as e:
                print(f"[!] gather lib failed for {lib}: {e}")
    if platform.system() != "Windows":
        try:
            os.chmod(exe, 0o755)
        except Exception:
            pass
    return exe


# ---------------- 模型下载（HF，带镜像回退） ----------------
def ensure_hf_hub():
    try:
        import huggingface_hub  # noqa: F401
        return True
    except ImportError:
        print("[setup] installing huggingface_hub ...")
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "huggingface_hub"]
        )
        return True


def download_model():
    ensure_hf_hub()
    os.makedirs(MODELS_DIR, exist_ok=True)
    last_err = None
    for endpoint in HF_MIRRORS:
        try:
            os.environ["HF_ENDPOINT"] = endpoint
            print(f"[dl] model via HF endpoint {endpoint} (repo={HF_REPO})")
            from huggingface_hub import snapshot_download
            snapshot_download(
                HF_REPO,
                local_dir=MODELS_DIR,
                allow_patterns=MODEL_PATTERNS,
            )
            print("[ok] model files present in models/")
            return True
        except Exception as e:
            last_err = e
            print(f"[!] HF endpoint {endpoint} failed: {e}")
    print(f"[!] model download failed: {last_err}")
    return False


# ---------------- 引擎是否已就绪 ----------------
def engine_ready():
    return _find_exe_under(BIN_DIR) is not None


def model_ready():
    hits = glob.glob(os.path.join(MODELS_DIR, "**", "*Q4_K_M*.gguf"), recursive=True)
    return bool(hits)


# ---------------- 主流程 ----------------
def run():
    """下载官方 llama.cpp 引擎 + DeepSeek-OCR GGUF 到 data_root()。

    返回 0 表示成功，1 表示失败（不调用 sys.exit，便于被 GUI 线程复用）。
    """
    system, machine = detect_platform()
    print(f"[info] platform: system={system} machine={machine}")
    print(f"[info] REPO={REPO} TAG={TAG}")
    print(f"[info] install root (bin/models): {HERE}")

    os.makedirs(BIN_DIR, exist_ok=True)

    # ---- 1) 引擎 ----
    if engine_ready():
        print(f"[skip] engine already present: {_find_exe_under(BIN_DIR)}")
    else:
        print("\n[1/2] Downloading official llama.cpp server ...")
        # get candidate asset list (API), fall back to deterministic URL
        candidates = []
        try:
            candidates = fetch_release_assets()
            print(f"[info] GitHub API returned {len(candidates)} assets")
        except Exception as e:
            print(f"[!] GitHub API unavailable ({e}); using deterministic URL fallback")
            url = fallback_engine_url(system, machine)
            if url:
                candidates = [(os.path.basename(url), url)]

        name, url = select_engine(candidates, system, machine)
        if not url:
            print("[!] could not determine the engine archive for this platform.")
            print("    Please download it manually from:")
            print(f"      https://github.com/{REPO}/releases/tag/{TAG}")
            return 1

        archive = os.path.join(BIN_DIR, name)
        if not download_file(url, archive, name):
            print("[!] engine download failed from all mirrors.")
            print(f"    Manual: open {url} and extract llama-server into bin/")
            return 1

        extract_archive(archive, BIN_DIR)
        os.remove(archive)

        # cudart (win/linux) best-effort
        cname, curl = select_cudart(candidates, system)
        if curl:
            car = os.path.join(BIN_DIR, cname)
            if download_file(curl, car, cname):
                try:
                    extract_archive(car, BIN_DIR)
                    os.remove(car)
                except Exception as e:
                    print(f"[!] cudart extract skipped: {e}")

        exe = gather_libs(BIN_DIR)
        if not exe:
            print("[!] llama-server not found after extraction.")
            return 1
        print(f"[ok] engine ready: {exe}")

    # ---- 2) 模型 ----
    if model_ready():
        print(f"[skip] model already present in models/")
    else:
        print("\n[2/2] Downloading DeepSeek-OCR GGUF (Q4_K_M) + mmproj ...")
        if not download_model():
            print("[!] model download failed. You can place the GGUF + mmproj into models/ manually,")
            print("    then just run run_llamacpp_server.py (it only needs models/ to exist).")
            return 1

    print("\nDONE. Next steps (fully local afterwards):")
    print("  1) run_llamacpp_server.py  (or run_llamacpp_server.bat / .sh)  <- local OCR server on :8080")
    print("  2) main.py  (or the GUI launcher)  <- opens the GUI (backend=llama_cpp)")
    return 0


def download_all():
    """GUI 用：下载引擎+模型，返回是否成功（True/False）。"""
    return run() == 0


def main():
    sys.exit(run())


if __name__ == "__main__":
    main()
