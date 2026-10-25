#!/usr/bin/env bash
# ============================================================================
# Create the app venv and install GUI + Ollama/llama.cpp dependencies (Linux/macOS).
# For the optional Transformers backend, see README (needs a separate Py3.11 env).
# ============================================================================
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[!] python3 not found. Install Python 3.10+ first."
  exit 1
fi

python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo
echo "===== Install finished ====="
echo "To start the app:     ./.venv/bin/python main.py"
echo "To set up llama.cpp:  ./download_models.sh  then  ./run_llamacpp_server.sh"
echo "For Ollama backend:   install Ollama, then:  ollama pull deepseek-ocr"
