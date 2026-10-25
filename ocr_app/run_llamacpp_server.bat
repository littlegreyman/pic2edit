@echo off
REM Start the local llama.cpp server for DeepSeek-OCR, then run the GUI separately.
REM This delegates discovery/launch to run_llamacpp_server.py (auto-finds the exe,
REM the GGUF model and the mmproj in models/, or auto-pulls from HF on first run).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run setup_venv.bat first.
  pause & exit /b 1
)
.\.venv\Scripts\python.exe run_llamacpp_server.py
pause
