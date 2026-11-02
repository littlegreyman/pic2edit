"""pytest 共享配置。

- 无显示环境（CI / 沙箱）下启用 Qt 的 offscreen 平台插件，使 GUI 测试可导入 PySide6。
- 把 ocr_app 根目录加入 sys.path，使测试可用 `import config` / `import backends` 等。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
