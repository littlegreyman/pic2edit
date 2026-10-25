@echo off
REM Start the local Transformers server for DeepSeek-OCR (model loads once, stays in VRAM).
REM Delegates discovery/launch to run_transformers_server.py (auto-finds the 3.11
REM venv and the model dir under models_tf/).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run setup_venv.bat first.
  pause & exit /b 1
)
.\.venv\Scripts\python.exe run_transformers_server.py
pause
