"""Headless end-to-end smoke test for pic2edit (no GUI, no model needed).

Covers the offline processing pipeline that does NOT require a model:
- PDF whole-page render + embedded-image extraction
- Multi-frame TIF split
- Markdown -> docx / txt export
- Backend wiring + default backend
Run: ./.venv/Scripts/python.exe _smoke_test.py
"""
import os
import sys
import json
import tempfile
import threading
import http.server
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config, RESOLUTION_MODES, PROMPT_TEMPLATES
from backends import OllamaBackend, TransformersBackend, LlamaCppBackend
from utils.input_prep import prepare_pdf, prepare_tif, prepare_input, InputItem
from utils.export_docx import md_to_docx, save_text
from utils.logging_setup import setup_logging, get_logger, env_summary, log_env_on_error

WORK = tempfile.mkdtemp(prefix="pic2edit_smoke_")
fails = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        fails.append(name)


# 1. Backend wiring + default backend
c = Config()
check("default backend is llama_cpp", c.backend == "llama_cpp")
check("LlamaCppBackend importable", LlamaCppBackend is not None)
check("OllamaBackend importable", OllamaBackend is not None)
check("TransformersBackend importable", TransformersBackend is not None)
b = LlamaCppBackend(c.llama_cpp_url, c.llama_cpp_model)
check("LlamaCppBackend.name == llama_cpp", b.name == "llama_cpp")
# Probe a guaranteed-closed port to verify graceful failure (the default :8080 may
# be occupied by another service on some machines).
b_closed = LlamaCppBackend("http://127.0.0.1:9", c.llama_cpp_model)
check("LlamaCppBackend.available() graceful when no server", b_closed.available() is False)
tb = TransformersBackend(c.tf_url, c.llama_cpp_model)
check("TransformersBackend.name == transformers", tb.name == "transformers")
check("TransformersBackend.available() graceful when no server", tb.available() is False)
check("config has llama_cpp_url/model", bool(c.llama_cpp_url) and bool(c.llama_cpp_model))
check("resolution modes present", set(RESOLUTION_MODES) >= {"Tiny", "Gundam"})
check("prompt templates present", set(PROMPT_TEMPLATES) >= {"markdown", "ocr", "figure"})


# 2. PDF: whole-page render + embedded image extraction
try:
    import fitz  # pymupdf
except Exception:
    try:
        import pymupdf as fitz
    except Exception:
        fitz = None

if fitz is not None:
    pdf_path = os.path.join(WORK, "sample.pdf")
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 60), "Hello DeepSeek OCR", fontsize=24)
    # embed a small image so extraction path is exercised
    import io
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (120, 80), (200, 30, 30))
    d = ImageDraw.Draw(img)
    d.text((10, 30), "EMB", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    rect = fitz.Rect(50, 400, 170, 480)
    page.insert_image(rect, stream=buf.getvalue())
    doc.save(pdf_path)
    doc.close()

    items = prepare_pdf(pdf_path, WORK)
    check("prepare_pdf returns items", len(items) >= 1)
    # whole-page render + embedded image extraction both produce PNG image files
    check("prepare_pdf produced page/embedded image(s)",
          any(it.image_path.lower().endswith(".png") for it in items))
    for it in items:
        check(f"pdf item image exists: {it.name}", os.path.isfile(it.image_path))
else:
    check("pymupdf available", False)


# 3. Multi-frame TIF split
try:
    from PIL import Image, ImageDraw
    tif_path = os.path.join(WORK, "multi.tif")
    frames = []
    for i in range(3):
        im = Image.new("RGB", (100, 60), (i * 40, 100, 200))
        ImageDraw.Draw(im).text((5, 5), f"F{i}", fill=(0, 0, 0))
        frames.append(im)
    frames[0].save(tif_path, save_all=True, append_images=frames[1:], format="TIFF")
    items = prepare_tif(tif_path, WORK)
    check("prepare_tif splits 3 frames", len(items) == 3)
    for it in items:
        check(f"tif frame image exists: {it.name}", os.path.isfile(it.image_path))
except Exception as e:
    check("prepare_tif runs", False)
    print("   tif error:", e)


# 4. Markdown -> docx / txt export
md = "# Title\n\n- item 1\n- item 2\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n**bold** text\n"
docx_path = os.path.join(WORK, "out.docx")
try:
    md_to_docx(md, docx_path)
    check("md_to_docx produces file", os.path.isfile(docx_path) and os.path.getsize(docx_path) > 0)
except Exception as e:
    check("md_to_docx runs", False)
    print("   docx error:", e)

txt_path = os.path.join(WORK, "out.md")
try:
    save_text(md, txt_path)
    check("save_text produces file", os.path.isfile(txt_path))
except Exception as e:
    check("save_text runs", False)
    print("   save_text error:", e)


# 5. llama.cpp launcher discovery + command building (no real server launch)
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rls", os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_llamacpp_server.py"))
    rls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rls)
    tmp = tempfile.mkdtemp(prefix="pic2edit_launch_")
    rls.HERE = tmp
    rls.BIN_DIR = os.path.join(tmp, "bin")
    rls.MODELS_DIR = os.path.join(tmp, "models")
    os.makedirs(rls.BIN_DIR)
    os.makedirs(rls.MODELS_DIR)
    exe = os.path.join(rls.BIN_DIR, "llama-server.exe")
    open(exe, "w").close()
    mdl = os.path.join(rls.MODELS_DIR, "DeepSeek-OCR-Q4_K_M.gguf")
    mp = os.path.join(rls.MODELS_DIR, "mmproj-DeepSeek-OCR-Q4_K_M.gguf")
    open(mdl, "w").close()
    open(mp, "w").close()
    exe_r, model, mmproj = rls.discover()
    cmd = rls.build_command(exe_r, model, mmproj)
    check("llama launcher finds exe", exe_r == exe)
    check("llama launcher finds model", model == mdl)
    check("llama launcher finds mmproj", mmproj == mp)
    check("llama launcher builds exe -m cmd",
          cmd[:2] == [exe, "-m"]
          and "--mmproj" in cmd
          and "--chat_template" not in cmd and "--chat-template" not in cmd
          and "--port" in cmd)
    # fallback: no exe -> main() should refuse
    os.remove(exe)
    exe_r2, model2, mmproj2 = rls.discover()
    check("llama launcher no-exe when bin empty", exe_r2 is None)
except Exception as e:
    check("llama launcher discovery test runs", False)
    print("   llama launcher error:", repr(e))


# 6. Transformers launcher discovery (no real launch / no torch needed)
try:
    spec2 = importlib.util.spec_from_file_location(
        "rts", os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_transformers_server.py"))
    rts = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(rts)
    tmp = tempfile.mkdtemp(prefix="pic2edit_tf_")
    rts.HERE = tmp
    rts.MODELS_TF_DIR = os.path.join(tmp, "models_tf")
    os.makedirs(rts.MODELS_TF_DIR)
    mdir = os.path.join(rts.MODELS_TF_DIR, "deepseek-ocr")
    os.makedirs(mdir)
    open(os.path.join(mdir, "config.json"), "w").close()
    venv_py = os.path.join(tmp, ".venv_tf", "Scripts", "python.exe")
    os.makedirs(os.path.dirname(venv_py))
    open(venv_py, "w").close()
    py, model = rts.discover("", "")
    check("tf launcher finds 3.11 python", py is not None and "python" in os.path.basename(py))
    check("tf launcher finds model dir", model == mdir)
    # explicit override via tf_model_path
    py2, model2 = rts.discover("", "/custom/model")
    check("tf launcher honors explicit tf_model_path", model2 == "/custom/model")
except Exception as e:
    check("tf launcher discovery test runs", False)
    print("   tf launcher error:", repr(e))


# 7. Prompt transforms per backend (mock requests.post; no server needed)
def _make_tiny_png(path):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (40, 24), (255, 255, 255))
    ImageDraw.Draw(im).text((2, 2), "OCR", fill=(0, 0, 0))
    im.save(path, format="PNG")


def _fake_post(url, json=None, timeout=None, **kw):
    _fake_post.captured = {"url": url, "json": json}
    class _R:
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": "X"}}]}
    return _R()


import requests as _requests
_tiny = os.path.join(WORK, "tiny.png")
_make_tiny_png(_tiny)
_sample_prompt = PROMPT_TEMPLATES["markdown"]  # "<|grounding|>Convert the document to markdown."

with mock.patch.object(_requests, "post", _fake_post):
    tb = TransformersBackend("http://x:1", "deepseek-ocr-local")
    tb.ocr_image(_tiny, _sample_prompt)
    tf_text = _fake_post.captured["json"]["messages"][0]["content"][0]["text"]
    check("transformers prompt gets <image> prefix",
          tf_text.startswith("<image>\n"))
    check("transformers prompt keeps grounding token",
          "<|grounding|>" in tf_text)

with mock.patch.object(_requests, "post", _fake_post):
    ob = OllamaBackend("http://x:1", "deepseek-ocr")
    ob.ocr_image(_tiny, _sample_prompt)
    ol = _fake_post.captured["json"]
    check("ollama uses images field", isinstance(ol["messages"][0].get("images"), list))
    check("ollama text has no <image> placeholder", "<image>" not in ol["messages"][0]["content"])

with mock.patch.object(_requests, "post", _fake_post):
    lb = LlamaCppBackend("http://x:1", "deepseek-ocr")
    lb.ocr_image(_tiny, _sample_prompt)
    lc = _fake_post.captured["json"]["messages"][0]["content"][0]["text"]
    # 修复后：原生 deepseek-ocr 模板会自动注入 media marker，客户端【绝不】在文本里
    # 写 marker（写了反而 2 markers vs 1 bitmap -> 400）。
    check("llama.cpp text 不含任何 media marker（交给原生模板注入）",
          "<image>" not in lc and "<__media_" not in lc)


# 8. llama.cpp 请求契约（原生模板：自动注入 marker，客户端不得写 marker）
REAL_400 = {"error": {"code": 400, "message": "Failed to tokenize prompt"}}


def _make_llama_handler():
    class _H(http.server.BaseHTTPRequestHandler):
        post_count = 0

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/v1/models":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            _H.post_count += 1
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = ""
            for m in payload.get("messages", []):
                c = m.get("content")
                if isinstance(c, list):
                    for p in c:
                        if p.get("type") == "text":
                            text += p.get("text", "")
            # 原生模板会自动注入 marker；若客户端又写了 marker -> 2 markers vs 1 bitmap -> 400
            if "<image>" in text or "<__media_" in text:
                self.send_response(400)
                body = json.dumps(REAL_400).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({"choices": [{"message": {"content": "MOCK_OCR_OK"}}]}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _H


def _start_mock():
    H = _make_llama_handler()
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, H


try:
    # 原生 deepseek-ocr 模板：自动注入 marker；客户端不得写 marker，否则 400。
    # ocr_image 现在发送「image_url 块 + 纯提示词」，不含任何 marker -> 首次即成功。
    srv, port, H = _start_mock()
    lb = LlamaCppBackend(f"http://127.0.0.1:{port}", "deepseek-ocr")
    res = lb.ocr_image(_tiny, "Free OCR.")
    check("native-template 服务端: 无 marker 请求即成功", res == "MOCK_OCR_OK")
    check("native-template 服务端: 仅 1 次 POST（无需重试）", H.post_count == 1)
    srv.shutdown()
except Exception as e:
    check("llama.cpp native-template mock test runs", False)
    print("   mock error:", repr(e))


# 9. Logging setup (env snapshot + handlers) does not raise
try:
    setup_logging()
    lg = get_logger("smoke_test")
    check("get_logger returns logger", lg is not None)
    s = env_summary(Config())
    check("env_summary returns non-empty string", isinstance(s, str) and len(s) > 0)
    log_env_on_error(lg, Config(), extra="smoke")
    check("log_env_on_error does not raise", True)
except Exception as e:
    check("logging setup runs", False)
    print("   logging error:", repr(e))


# 10. Real-file regression on a sample image (skipped unless present on this machine).
# 不使用硬编码的个人路径/用户名：默认中性相对名 clinic_form.jpg，可用环境变量
# PIC2EDIT_CLINIC 指向本机真实样例以启用真机回归，其它机器文件不存在则自动跳过。
clinic = os.environ.get("PIC2EDIT_CLINIC", "clinic_form.jpg")
if os.path.isfile(clinic):
    try:
        items = prepare_input(clinic, WORK)
        check("clinic_form.jpg -> at least one image item", len(items) >= 1)
        for it in items:
            check(f"clinic item image exists: {it.name}", os.path.isfile(it.image_path))
    except Exception as e:
        check("clinic_form.jpg prepare_input runs", False)
        print("   clinic error:", repr(e))
else:
    check("clinic_form.jpg present (skipped on this machine)", True)
    print("   (clinic_form.jpg not found here; real-file regression will run on the dev PC)")


print("\n==== SMOKE SUMMARY ====")
if fails:
    print(f"{len(fails)} FAILURE(S): {fails}")
    sys.exit(1)
else:
    print("ALL PASS")