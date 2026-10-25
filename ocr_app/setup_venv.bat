@echo off
REM Create venv and install dependencies for GUI + Ollama backend
cd /d %~dp0
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo ===== Install finished =====
echo For Ollama backend: install Ollama from https://ollama.com, then run:
echo     ollama pull deepseek-ocr
echo.
echo To start the app:
echo     .venv\Scripts\python.exe main.py
echo.
echo For Transformers backend (local HF model, optional 4/8bit):
echo create a separate Python 3.11 venv and install requirements_tf.txt,
echo then set interpreter and model path in the app Settings dialog.
pause
