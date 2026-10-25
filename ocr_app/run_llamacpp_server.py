"""Smart launcher for the local llama.cpp server (DeepSeek-OCR).

Uses the official prebuilt llama.cpp server (bin/llama-server.exe), downloaded
by download_models.bat. The C++ server is OpenAI-compatible and supports
-m/--mmproj -- exactly what LlamaCppBackend expects.
This avoids any pip build / C++ compiler on Windows.

IMPORTANT (DeepSeek-OCR multimodal gotcha, verified on build b11853):
  **Do NOT pass --chat-template deepseek-ocr.** The bare template forced by that
  flag does NOT auto-insert the media marker and does NOT bind the image_url
  block to the vision encoder, so every request either 400s ("N markers vs M
  bitmaps") or returns meaningless boilerplate. Let llama.cpp select the model's
  NATIVE deepseek-ocr template instead -- it auto-inserts the marker and wires
  the image correctly. The client (LlamaCppBackend.ocr_image) therefore sends
  the image_url block + prompt with NO marker in the text.

Discovers, in this folder:
  - the server exe:  bin/llama-server.exe  (searched recursively under bin/)
  - the GGUF model:  models/*Q4_K_M*.gguf (or any *.gguf, excluding mmproj)
  - the mmproj:      models/*mmproj*.gguf
Then launches the server on http://127.0.0.1:8080 (the default backend URL).

Run:  ./.venv/Scripts/python.exe run_llamacpp_server.py
"""
import os
import sys
import glob
import time
import threading
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(HERE, "bin")
MODELS_DIR = os.path.join(HERE, "models")
PORT = "8080"
# Generous timeout: a 1.95GB Q4 model on first CUDA load can take ~30-60s.
READY_TIMEOUT = 180.0


def _find_exe():
    cands = [
        os.path.join(BIN_DIR, "llama-server.exe"),
        os.path.join(BIN_DIR, "llama-server"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    hits = glob.glob(os.path.join(BIN_DIR, "**", "llama-server.exe"), recursive=True)
    if not hits:
        hits = glob.glob(os.path.join(BIN_DIR, "**", "llama-server"), recursive=True)
    return sorted(hits)[0] if hits else None


def _find(root, patterns):
    for pat in patterns:
        hits = glob.glob(os.path.join(root, pat))
        if not hits:
            hits = glob.glob(os.path.join(root, "**", pat), recursive=True)
        if hits:
            return sorted(hits)[0]
    return None


def _first_model(root):
    cands = glob.glob(os.path.join(root, "*.gguf"))
    cands += glob.glob(os.path.join(root, "**", "*.gguf"), recursive=True)
    cands = [c for c in cands if "mmproj" not in os.path.basename(c).lower()]
    cands.sort()
    return cands[0] if cands else None


def discover():
    exe = _find_exe()
    # Prefer the Q4_K_M quant (best fit for ~8GB VRAM); fall back to the first
    # non-mmproj gguf so we never accidentally load the large bf16/f16 build.
    model = _find(MODELS_DIR, ["*Q4_K_M*.gguf"]) or _first_model(MODELS_DIR)
    # Prefer a mmproj that matches the chosen quant; otherwise any mmproj.
    mmproj = None
    if model and "Q4_K_M" in os.path.basename(model):
        mmproj = _find(MODELS_DIR, ["*mmproj*Q4_K_M*.gguf"])
    if not mmproj:
        mmproj = _find(MODELS_DIR, ["*mmproj*.gguf"])
    return exe, model, mmproj


def build_command(exe, model, mmproj):
    # NOTE: intentionally NOT passing --chat-template. The bare "deepseek-ocr"
    # template breaks multimodal (no auto marker / no vision binding). Let
    # llama.cpp pick the model's native template, which works correctly.
    cmd = [exe, "-m", model]
    if mmproj:
        cmd += ["--mmproj", mmproj]
    cmd += ["--host", "127.0.0.1", "--port", PORT]
    return cmd


def _probe_health():
    """Return True if the server answers a health/model probe on its port."""
    base = "http://127.0.0.1:{}".format(PORT)
    for path in ("/health", "/v1/models"):
        try:
            with urllib.request.urlopen(base + path, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
    return False


def main():
    exe, model, mmproj = discover()
    if not exe:
        sys.stderr.write(
            "ERROR: llama-server.exe not found in bin/.\n"
            "  Run download_models.bat first (it downloads the official prebuilt server).\n"
        )
        sys.exit(1)
    if not model:
        sys.stderr.write(
            "ERROR: no .gguf model found in models/. Run download_models.bat to download it.\n"
        )
        sys.exit(1)
    cmd = build_command(exe, model, mmproj)
    print("[launch] " + " ".join(cmd))
    print("[wait] starting server (loading model into VRAM, can take up to ~3 min)...")

    proc = subprocess.Popen(
        cmd,
        cwd=HERE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # Stream the server's own log lines live so CUDA/model errors are visible.
    def _reader():
        try:
            for line in proc.stdout:
                sys.stdout.write(line)
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()

    ready = False
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            # Process already exited (e.g. blocked by Smart App Control / bad args).
            break
        if _probe_health():
            ready = True
            break
        time.sleep(1)

    if ready:
        print(
            "\n[ok] llama.cpp server is ready at http://127.0.0.1:{}/".format(PORT)
        )
        print(
            "[ok] KEEP THIS WINDOW OPEN, then run the GUI in another terminal:\n"
            "       .venv\\Scripts\\python.exe main.py"
        )
        try:
            proc.wait()
        except KeyboardInterrupt:
            print("\n[stop] stopping server...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        return

    code = proc.poll()
    if code is None:
        # Still running but never answered on the port.
        print(
            "\n[warn] server is running but did not respond on port {} within {:.0f}s.".format(
                PORT, READY_TIMEOUT
            )
        )
        print("        Check the log lines above (CUDA / mmproj / model path issues).")
        print("        Press Ctrl+C to stop.")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        return

    # Process exited immediately -> most likely an OS-level block or bad invocation.
    print(
        "\n[error] server process exited immediately with code {}.".format(code)
    )
    print("        Most likely causes (in order of likelihood):")
    if os.name == "nt":  # Windows
        print("          1) Windows Smart App Control / SmartScreen blocked the unsigned exe.")
        print("             -> Fix: right-click bin\\llama-server.exe -> Properties -> Unblock;")
        print("                if Smart App Control still blocks it, turn it off (see README 排错).")
    else:  # Linux / macOS
        print("          1) The exe lacks execute permission or a shared library is missing.")
        print("             -> Fix: chmod +x the llama-server, and ensure CUDA/Metal libs are present;")
        print("                run 'ldd <exe>' (Linux) to spot missing .so, or check the log above.")
    print("          2) A startup argument was rejected (model/mmproj path wrong).")
    print("        Re-run after fixing; this window will then stay open while the server runs.")


if __name__ == "__main__":
    main()
