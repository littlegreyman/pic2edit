#!/usr/bin/env bash
# ============================================================================
# One-time setup for the llama.cpp backend (Linux / macOS).
# The real, cross-platform logic lives in download_models.py.
# ============================================================================
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "[!] .venv not found. Run ./setup_venv.sh first."
  exit 1
fi
./.venv/bin/python download_models.py
