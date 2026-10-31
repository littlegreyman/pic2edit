"""使用记录 / 参数预设 / 上次成功复用 单测（headless）。

验证 Task #41 的不变量：
- usage_log.record 追加 JSON 行；last_success 返回最近“全成功”记录；
- SettingsDialog 预设下拉一键套用模型参数；"应用上次成功参数" 从快照填充。
"""
import os
import sys
import json
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from PySide6.QtWidgets import QApplication

import utils.usage_log as UL
from config import Config, ModelParams, MODEL_PARAM_PRESETS
from gui import SettingsDialog


def test_usage_log_record_and_last_success(tmp_path):
    logf = os.path.join(tmp_path, "usage_log.jsonl")
    UL._log_path = lambda: logf  # 重定向到临时文件，避免污染真实配置
    # 先写一条失败，再写一条成功
    UL.record({"backend": "llama_cpp", "prompt_template": "markdown",
               "model_params": {"temperature": 0.0, "repeat_penalty": 1.10,
                                "repeat_last_n": 4096, "max_tokens": 16384},
               "n_total": 3, "n_done": 1, "n_fail": 2, "success": False})
    UL.record({"backend": "llama_cpp", "prompt_template": "ocr",
               "model_params": {"temperature": 0.0, "repeat_penalty": 1.10,
                                "repeat_last_n": 4096, "max_tokens": 16384},
               "n_total": 3, "n_done": 3, "n_fail": 0, "success": True})
    assert UL.last_success() is not None
    assert UL.last_success()["prompt_template"] == "ocr"
    snap = UL.last_success_snapshot()
    assert snap["prompt_template"] == "ocr"
    assert snap["model_params"]["max_tokens"] == 16384
    # 清空后再确认 last_success 为 None
    open(logf, "w").close()
    assert UL.last_success() is None


def test_settings_preset_applies():
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    dlg = SettingsDialog(cfg)
    # 模型参数控件已改为“每后端子页各一份”，取当前后端那一页的控件组
    w = dlg._mp_widgets[dlg.backend.currentText()]  # (temperature, rp, rln, max_tokens)
    dlg._apply_preset("强化抗退化", *w)
    assert w[1].value() == 1.15
    assert w[2].value() == 4096
    # 默认预设
    dlg._apply_preset("默认（稳定，推荐）", *w)
    assert w[0].value() == 0.0
    dlg.close()
    app.processEvents()


def test_settings_apply_last_success():
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    dlg = SettingsDialog(cfg)
    UL.last_success_snapshot = lambda: {
        "prompt_template": "ocr",
        "model_params": {"temperature": 0.0, "repeat_penalty": 1.20,
                         "repeat_last_n": 3333, "max_tokens": 8192},
    }
    w = dlg._mp_widgets[dlg.backend.currentText()]
    dlg._apply_last_success_to(*w)
    assert w[1].value() == 1.20
    assert w[2].value() == 3333
    assert w[3].value() == 8192
    dlg.close()
    app.processEvents()


def test_settings_get_config_writes_auto_reuse():
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    dlg = SettingsDialog(cfg)
    dlg.auto_reuse.setChecked(False)
    out = dlg.get_config()
    assert out.auto_reuse_last_success is False
    dlg.close()
    app.processEvents()


def main():
    import tempfile as _t
    d = _t.mkdtemp()
    test_usage_log_record_and_last_success(d)
    test_settings_preset_applies()
    test_settings_apply_last_success()
    test_settings_get_config_writes_auto_reuse()
    print("ALL PASS: test_usage_log")


if __name__ == "__main__":
    main()
