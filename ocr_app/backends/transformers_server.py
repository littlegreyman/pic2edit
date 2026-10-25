"""Persistent local OpenAI-compatible server for DeepSeek-OCR via Transformers.

Unlike the old per-call subprocess approach (which reloaded the model on every
request), this loads the model ONCE at startup and serves it through an
OpenAI-compatible HTTP API, so the model stays resident in VRAM between calls:

    GET  /health                  -> 200 "ok"
    POST /v1/chat/completions     -> OpenAI-style JSON (accepts image_url)

Run it inside the Python 3.11 venv that has torch/transformers (NOT the app's
3.13 venv), e.g. via run_transformers_server.bat:

    ./.venv_tf/Scripts/python.exe transformers_server.py \
        --model models_tf/deepseek-ocr --quant 8bit --port 8001

It intentionally uses only the Python standard library for the HTTP layer so no
extra dependency is added to requirements_tf.txt.
"""
import argparse
import base64
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# Make the repo root importable (config.RESOLUTION_MODES) regardless of CWD.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import RESOLUTION_MODES  # noqa: E402
from utils.logging_setup import get_logger  # noqa: E402

_logger = get_logger("server.transformers")


MODEL = None
TOKENIZER = None
MODE = None
MODEL_NAME = "deepseek-ocr-local"
INFER_LOCK = threading.Lock()  # torch inference is not reentrant across threads


def parse_args():
    p = argparse.ArgumentParser(description="DeepSeek-OCR Transformers server")
    p.add_argument("--model", required=True, help="local HF model directory")
    p.add_argument("--quant", default="8bit", choices=["none", "8bit", "4bit"])
    p.add_argument("--mode", default="Gundam", choices=list(RESOLUTION_MODES.keys()))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=8001, type=int)
    p.add_argument("--device", default="0")
    return p.parse_args()


def _pick_device():
    """Choose the best available torch backend: CUDA > MPS (macOS) > CPU."""
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(model_path: str, quant: str, mode: str, device: str):
    global MODEL, TOKENIZER, MODE
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", device)
    from transformers import AutoModel, AutoTokenizer
    import torch

    dev = _pick_device()
    m = RESOLUTION_MODES[mode]
    load_kwargs = dict(trust_remote_code=True, _attn_implementation="eager")

    # bitsandbytes (8/4-bit) is CUDA-only; on MPS/CPU we skip it and pick a dtype.
    use_quant = quant in ("8bit", "4bit") and dev == "cuda"
    if use_quant:
        if quant == "8bit":
            load_kwargs.update(load_in_8bit=True, device_map="cuda")
        else:
            load_kwargs.update(load_in_4bit=True, device_map="cuda")
    elif dev == "cpu":
        load_kwargs.update(torch_dtype=torch.float32)
    elif dev == "cuda":  # match official setup (cuda11.8+torch2.6.0, bfloat16)
        load_kwargs.update(torch_dtype=torch.bfloat16)
    else:  # mps without quantization
        load_kwargs.update(torch_dtype=torch.float16)

    tok = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    model = AutoModel.from_pretrained(model_path, **load_kwargs)

    # When not using device_map (i.e. not quant-on-cuda), place the model manually.
    if not use_quant:
        model = model.eval()
        if dev == "cuda":
            model = model.cuda()
        elif dev == "mps":
            model = model.to("mps")

    MODEL, TOKENIZER, MODE = model, tok, m
    _logger.info("[server] model loaded (quant=%s, device=%s, mode=%s)", quant, dev, mode)


def _decode_image_url(url: str) -> str:
    """Return a local file path for the image. For `data:` URLs a temp PNG is
    materialized; the caller deletes it based on the tempdir prefix in do_POST."""
    if url.startswith("data:"):
        _, _, b64 = url.partition(",")
        raw = base64.b64decode(b64)
        fd, path = tempfile.mkstemp(suffix=".png")
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return path
    # treat as a (possibly file://) path
    if url.startswith("file://"):
        url = url[len("file://"):]
    return url


def run_inference(prompt: str, image_path: str) -> str:
    # Serialize: torch inference is not safe to run concurrently.
    with INFER_LOCK:
        res = MODEL.infer(
            TOKENIZER,
            prompt=prompt,
            image_file=image_path,
            output_path=None,
            base_size=MODE["base_size"],
            image_size=MODE["image_size"],
            crop_mode=MODE["crop_mode"],
            save_results=False,
            test_compress=True,
        )
    if isinstance(res, dict):
        res = res.get("text", str(res))
    return res or ""


def extract_request(payload: dict):
    """Pull the user prompt + first image from an OpenAI-style messages body."""
    prompt = ""
    image_path = None
    for msg in payload.get("messages", []):
        content = msg.get("content")
        if isinstance(content, str):
            if msg.get("role") == "user":
                prompt = content
            continue
        for part in content:
            t = part.get("type")
            if t == "text" and msg.get("role") == "user":
                prompt += part.get("text", "")
            elif t == "image_url":
                image_path = _decode_image_url(part["image_url"]["url"])
    return prompt.strip(), image_path


def build_response(text: str, model: str) -> dict:
    return {
        "id": "chatcmpl-local",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            self._send_json({"status": "ok"})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/v1/chat/completions":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            prompt, image_path = extract_request(payload)
            if not image_path:
                raise ValueError("no image_url found in request")
            try:
                text = run_inference(prompt, image_path)
            finally:
                # clean up the temp PNG we created for data: URLs
                if isinstance(image_path, str) and image_path.startswith(tempfile.gettempdir()):
                    try:
                        os.remove(image_path)
                    except OSError:
                        pass
            self._send_json(build_response(text, payload.get("model", MODEL_NAME)))
        except Exception as e:  # surface a clean OpenAI-style error
            _logger.exception("transformers server error while handling request")
            self._send_json({"error": {"message": str(e)}}, status=500)

    def log_message(self, *args):
        pass  # quiet


def main():
    args = parse_args()
    load_model(args.model, args.quant, args.mode, args.device)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[server] listening on http://{args.host}:{args.port}/v1/chat/completions", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
