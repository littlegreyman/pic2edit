#!/usr/bin/env bash
# Start the local llama.cpp server for DeepSeek-OCR (Linux / macOS).
# Delegates discovery/launch to run_llamacpp_server.py (cross-platform).
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "[!] .venv not found. Run ./setup_venv.sh first."
  exit 1
fi
./.venv/bin/python run_llamacpp_server.py
