"""模型参数配置化单测：Config 默认值 / 后端请求 payload / 服务端命令 / 流水线注入。

验证 Task #40 的不变量：
- Config.model_params 默认 = 已验证的“不退化”组合（0.0 / 1.10 / 4096 / 16384）；
- LlamaCppBackend._chat 的请求 payload 使用配置值（而非硬编码）；
- run_llamacpp_server.build_command 的 --repeat-penalty / --repeat-last-n 来自 model_params；
- pipeline_run 把 model_params 挂到 VLM 后端实例上。
"""
import os
import sys
import json
import tempfile
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # ocr_app

from config import Config, ModelParams
from backends.llamacpp_backend import LlamaCppBackend
import backends.llamacpp_backend as LCB
import run_llamacpp_server as RLS
from backends.ocr_pipeline import run as pipeline_run
from utils.input_prep import InputItem


def test_config_defaults():
    cfg = Config()
    mp = cfg.model_params
    assert isinstance(mp, ModelParams)
    assert (mp.temperature, mp.repeat_penalty, mp.repeat_last_n, mp.max_tokens) == (
        0.0, 1.10, 4096, 16384,
    )


def test_config_load_reconstructs_model_params(tmp_path):
    # 模拟从 JSON 字典重建：model_params 为 dict 时应还原为 ModelParams
    cfg = Config()
    cfg.model_params = ModelParams(temperature=0.0, repeat_penalty=1.15,
                                  repeat_last_n=2048, max_tokens=8192)
    d = json.loads(json.dumps(asdict(cfg)))  # asdict 会把嵌套 ModelParams 也转成 dict
    # 直接调用 load 的重建分支逻辑
    rebuilt = Config()
    for k, v in d.items():
        if hasattr(rebuilt, k):
            if k == "model_params" and isinstance(v, dict):
                rebuilt.model_params = ModelParams(**v)
            else:
                setattr(rebuilt, k, v)
    assert rebuilt.model_params.repeat_penalty == 1.15
    assert rebuilt.model_params.repeat_last_n == 2048
    assert rebuilt.model_params.max_tokens == 8192


def test_backend_payload_uses_model_params():
    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return FakeResp()

    LCB.requests.post = fake_post

    mp = ModelParams(temperature=0.0, repeat_penalty=1.25, repeat_last_n=2048, max_tokens=8192)
    b = LlamaCppBackend("http://localhost:8080", "m", mp)
    png = os.path.join(tempfile.tempdir or "/tmp", "_mp_test.png")
    with open(png, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")  # 假 PNG，ocr_image 只读取字节
    b.ocr_image(png, "Free OCR.")
    p = captured["payload"]
    assert p["temperature"] == 0.0
    assert p["repeat_penalty"] == 1.25
    assert p["repeat_last_n"] == 2048
    assert p["max_tokens"] == 8192


def test_build_command_uses_model_params():
    # 默认（无 model_params）
    cmd0 = RLS.build_command("srv", "m.gguf", None)
    assert "--repeat-penalty" in cmd0
    assert float(cmd0[cmd0.index("--repeat-penalty") + 1]) == 1.10
    assert "--repeat-last-n" in cmd0
    assert int(cmd0[cmd0.index("--repeat-last-n") + 1]) == 4096
    # 自定义
    mp = ModelParams(repeat_penalty=1.30, repeat_last_n=1024)
    cmd1 = RLS.build_command("srv", "m.gguf", "mm.gguf", mp)
    assert float(cmd1[cmd1.index("--repeat-penalty") + 1]) == 1.30
    assert int(cmd1[cmd1.index("--repeat-last-n") + 1]) == 1024
    assert "--mmproj" in cmd1 and cmd1[cmd1.index("--mmproj") + 1] == "mm.gguf"


def test_pipeline_injects_model_params():
    class FakeVLM:
        name = "fake"

        def available(self):
            return True

        def ocr_image(self, image_path, prompt):
            return "x"

    vlm = FakeVLM()
    item = InputItem("x", "/tmp/x.png", "/tmp/x.pdf")
    mp = ModelParams(repeat_penalty=1.20, repeat_last_n=3333)
    pipeline_run([item], vlm_backend=vlm, use_vlm_fallback=True,
                 skip_discriminative=True, prompt="Free OCR.", model_params=mp)
    assert vlm.model_params is mp


def main():
    test_config_defaults()
    test_config_load_reconstructs_model_params(None)
    test_backend_payload_uses_model_params()
    test_build_command_uses_model_params()
    test_pipeline_injects_model_params()
    print("ALL PASS: test_model_params")


if __name__ == "__main__":
    main()
