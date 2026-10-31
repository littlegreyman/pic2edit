#!/bin/sh
# 跨平台一键启动 pic2edit GUI（Linux / macOS；Windows 请用 launch_gui.bat）。
# 直接调用项目自带的 venv 解释器，无需系统 PATH 上有 python。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 按操作系统选择 venv 解释器（Windows Git Bash 也走 Scripts\python.exe）
case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*|*_NT*) VENV_PY="$SCRIPT_DIR/ocr_app/.venv/Scripts/python.exe" ;;
    *) VENV_PY="$SCRIPT_DIR/ocr_app/.venv/bin/python3" ;;
esac
LAUNCH_PY="$SCRIPT_DIR/launch_gui.py"

if [ ! -x "$VENV_PY" ]; then
    echo "[error] venv interpreter not found: $VENV_PY"
    echo "         Run ocr_app/setup_venv.sh (Linux/macOS) first to install dependencies."
    exit 1
fi

echo "[launch] starting pic2edit GUI ..."
exec "$VENV_PY" "$LAUNCH_PY" "$@"
