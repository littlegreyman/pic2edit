@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%ocr_app\.venv\Scripts\python.exe"
set "LAUNCH_PY=%SCRIPT_DIR%launch_gui.py"

if not exist "%VENV_PY%" (
    echo [error] venv interpreter not found: %VENV_PY%
    echo          Run ocr_app\setup_venv.bat first to install dependencies.
    pause
    exit /b 1
)

echo [launch] starting pic2edit GUI ...
"%VENV_PY%" "%LAUNCH_PY%" %*
endlocal
