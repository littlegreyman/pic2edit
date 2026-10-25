#!/usr/bin/env bash
# ============================================================================
# One-time setup for the Transformers backend (Linux / macOS).
# The real, cross-platform logic lives in download_models_tf.py.
# Prerequisite: Python 3.11 available as `python3.11`.
# ============================================================================
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "[!] .venv not found. Run ./setup_venv.sh first."
  exit 1
fi
./.venv/bin/python download_models_tf.py
