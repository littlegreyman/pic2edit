@echo off
REM ============================================================================
REM  One-time setup for the llama.cpp backend (Windows). RUN THIS ON YOUR OWN PC.
REM  The real, cross-platform logic lives in download_models.py (it picks the
REM  correct official llama.cpp server for your OS/ARCH and the DeepSeek-OCR GGUF).
REM ============================================================================
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run setup_venv.bat first.
  pause & exit /b 1
)
.\.venv\Scripts\python.exe download_models.py
pause
