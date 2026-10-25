@echo off
REM ============================================================================
REM  One-time setup for the Transformers backend (Windows). RUN THIS ON YOUR OWN PC.
REM  The real, cross-platform logic lives in download_models_tf.py (creates the
REM  Python 3.11 venv + downloads DeepSeek-OCR into models_tf/).
REM  Prerequisite: Python 3.11 installed and available via `py -3.11`.
REM ============================================================================
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run setup_venv.bat first.
  pause & exit /b 1
)
.\.venv\Scripts\python.exe download_models_tf.py
pause
