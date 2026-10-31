#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_backend.py — llama.cpp / DeepSeek-OCR 后端契约验证 + 真机验证。

目的（对应用户要求：先查文档、按格式严格调用与拆包、用真实样例验证）：
1) 沙箱默认（无 GPU / 无权重）：启动一个【严格按 docs/llamacpp_deepseek_ocr_api.md
   实现】的 mock llama-server，用真实样例 clinic_form.jpg 走完整流程
   （prepare_input → 构建请求 → 发送 → 按返回包拆包），逐字段断言请求体符合契约，
   并打印拆包得到的文本、核对送出的正是 clinic 图片的字节。
2) 真机模式（在装有模型 + GPU 的机器上运行）：
       python verify_backend.py --live http://localhost:8080
   把 clinic_form.jpg 真正发给运行中的 llama-server，打印模型输出的【真实 OCR 文本】，
   用于确认“能真正输出正确的文本”。

用法：
    python verify_backend.py                 # 沙箱契约验证（mock 服务端）
    python verify_backend.py --live URL      # 真机：URL 为 llama-server 地址
    python verify_backend.py --image 别的.jpg # 指定其它图片
"""
import argparse
import base64
import json
import os
import re
import sys
import tempfile
import threading
import http.server

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backends.llamacpp_backend import LlamaCppBackend
from utils.input_prep import prepare_input
from config import Config, PROMPT_TEMPLATES

# 真实样例图片的默认路径：使用中性相对名，不在源码中硬编码任何个人路径/用户名。
# 开发者本机可用环境变量 PIC2EDIT_CLINIC 指向真实样例（例如
# C:\Users\<你>\Downloads\clinic_form.jpg）来启用真机回归；其它机器该文件不存在则跳过。
CLINIC_DEFAULT = os.environ.get("PIC2EDIT_CLINIC", "clinic_form.jpg")
FAILS = []

# 真实服务端（原生 deepseek-ocr 模板）会自动注入 media marker；客户端若在文本里
# 再写一个 marker，就会出现“2 markers vs 1 bitmap”而 400。所以契约上：文本里
# 绝不能出现任何 media marker。本 mock 用它来判定请求是否合规。
_MARKER_RE = re.compile(r"<image>|<__media_")


def check(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


# --------------------------------------------------------------------------- #
# 沙箱：严格按文档实现的 mock llama-server
# --------------------------------------------------------------------------- #
class _MockHandler(http.server.BaseHTTPRequestHandler):
    post_count = 0
    last_request = None
    last_image_bytes = b""

    def log_message(self, *a):  # 静默
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/props":
            # 注意：真实服务端会回一个随机化的 media_marker；但本客户端【不再】读取
            # 它——原生 deepseek-ocr 模板会自动注入 marker。这里仍返回以示接口存在，
            # 仅作探测可用性的探测点，不影响请求构造。
            self._send(200, {"media_marker": "<__media_mock__>",
                             "model_path": "DeepSeek-OCR-Q4_K_M.gguf"})
        elif self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": "deepseek-ocr"}]})
        elif self.path == "/health":
            self._send(200, {})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        _MockHandler.post_count += 1
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) or b"{}"
        try:
            payload = json.loads(raw)
        except Exception:
            self._send(400, {"error": {"code": 400, "message": "Failed to tokenize prompt"}})
            return
        _MockHandler.last_request = payload

        # ---- 严格契约校验（不符合则按真实服务端返回 400 + 真实响应体）----
        problems = []
        if not isinstance(payload.get("model"), str) or not payload["model"]:
            problems.append("model 缺失/非法")
        msgs = payload.get("messages")
        if not isinstance(msgs, list) or len(msgs) != 1:
            problems.append("messages 必须是长度为 1 的列表")
        else:
            m0 = msgs[0]
            if m0.get("role") != "user":
                problems.append("messages[0].role 必须是 'user'")
            content = m0.get("content")
            if not isinstance(content, list):
                problems.append("messages[0].content 必须是多模态 parts 列表")
            else:
                text_parts = [p for p in content if p.get("type") == "text"]
                img_parts = [p for p in content if p.get("type") == "image_url"]
                if len(text_parts) != 1 or len(img_parts) != 1:
                    problems.append("必须恰好有 1 个 text part + 1 个 image_url part")
                else:
                    text = text_parts[0].get("text", "")
                    # 关键契约规则：文本里绝不能出现 media marker（原生模板会自动注入，
                    # 客户端多写会“2 markers vs 1 bitmap”而 400）
                    if _MARKER_RE.search(text):
                        problems.append("文本里出现了 media marker（应交给原生模板自动注入）")
                    url = img_parts[0].get("image_url", {}).get("url", "")
                    if not url.startswith("data:image/png;base64,"):
                        problems.append("image_url 必须是 data:image/png;base64,<B64>")
                    else:
                        try:
                            b64 = url.split(",", 1)[1]
                            img_bytes = base64.b64decode(b64)
                            if img_bytes[:8] != b"\x89PNG\r\n\x1a\n":
                                problems.append("解码后的图片不是 PNG")
                            elif len(img_bytes) < 100:
                                problems.append("解码后的图片过小")
                            else:
                                _MockHandler.last_image_bytes = img_bytes
                        except Exception as ex:
                            problems.append(f"base64 解码失败: {ex}")

        if problems:
            # 真实服务端 400 响应体（注意：不含任何 "media markers" 字样）
            self._send(400, {"error": {"code": 400, "message": "Failed to tokenize prompt"}})
            return

        # ---- 成功：标准 OpenAI chat.completion 返回包 ----
        text_part = next(p for p in payload["messages"][0]["content"] if p.get("type") == "text")
        resp_content = (
            "[MOCK OCR OUTPUT]\n"
            f"prompt_received: {text_part['text']!r}\n"
            f"image_bytes: {len(_MockHandler.last_image_bytes)}\n"
            "contract-compliant round trip OK"
        )
        self._send(200, {
            "id": "chatcmpl-mock", "object": "chat.completion", "created": 0,
            "model": payload["model"],
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": resp_content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })


def _start_mock():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _resolve_sandbox_image(path):
    """沙箱契约测试只需“任意合法 PNG”来验证请求结构与拆包，不依赖具体图片内容。

    若默认样例不存在（例如克隆到其它机器），用 Pillow 生成一张最小合法 PNG（48x48）
    写入临时文件，让契约测试开箱即用；开发者仍可用 --image 或环境变量 PIC2EDIT_CLINIC
    指定真实样例。
    """
    if os.path.isfile(path):
        return path
    # 运行时用 Pillow 生成（Pillow 为本项目依赖，必然可用），避免手写 base64 转录出错；
    # 48x48 满足 mock 契约“解码图片不能过小（<100 字节）”的校验。
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), (240, 240, 240)).save(buf, "PNG")
    fd, tmp = tempfile.mkstemp(suffix=".png")
    with os.fdopen(fd, "wb") as f:
        f.write(buf.getvalue())
    print("   （默认样例不存在，已用合成 48x48 PNG 跑契约测试；"
          "如需真机回归请用 --image 或设置 PIC2EDIT_CLINIC）")
    return tmp


def main():
    ap = argparse.ArgumentParser(description="llama.cpp / DeepSeek-OCR 后端验证")
    ap.add_argument("--live", default=os.environ.get("PIC2EDIT_LIVE_URL", ""),
                    help="真机模式：llama-server 地址，例如 http://localhost:8080")
    ap.add_argument("--image", default=CLINIC_DEFAULT, help="用于验证的图片路径")
    args = ap.parse_args()

    if args.live:
        # 真机模式必须基于真实图片才有意义，缺失则明确报错（不用合成图冒充）
        if not os.path.isfile(args.image):
            print(f"[ABORT] 真机模式需要真实图片，但找不到: {args.image}")
            print("   请用 --image 指定，或设置环境变量 PIC2EDIT_CLINIC 指向本机真实样例。")
            sys.exit(2)
        image = args.image
    else:
        # 沙箱契约测试：缺省样例时自动合成最小 PNG，保证开箱即用
        image = _resolve_sandbox_image(args.image)

    cfg = Config()
    prompt = cfg.prompt()  # 例如 "<|grounding|>Convert the document to markdown."

    # 1) 准备输入（真实走 PDF/TIF/图片预处理，落为临时 PNG）
    docs = prepare_input(image, os.path.dirname(os.path.abspath(__file__)))
    check("prepare_input 产出文档项", len(docs) >= 1, f"共 {len(docs)} 项")
    item = docs[0].pages[0]
    print(f"   使用图像项: {item.name} -> {item.image_path}")
    with open(item.image_path, "rb") as f:
        prepared_png = f.read()
    check("预处理产物是 PNG", prepared_png[:8] == b"\x89PNG\r\n\x1a\n")

    if args.live:
        # ---------------- 真机模式 ----------------
        print(f"\n=== 真机模式：直连 {args.live} ===")
        backend = LlamaCppBackend(args.live.rstrip("/"), cfg.llama_cpp_model)
        if not backend.available():
            print("[ABORT] 服务端不可达（available() 返回 False）。请确认 llama-server 已启动。")
            sys.exit(3)
        check("available() 为真", True)
        print("   正在向真实 llama-server 发送 clinic_form.jpg ...")
        try:
            text = backend.ocr_image(item.image_path, prompt)
        except Exception as e:
            print(f"[ERROR] 调用失败: {e}")
            sys.exit(4)
        print("\n========== 真实 OCR 文本（模型输出）==========")
        print(text)
        print("=============================================")
        check("真实输出非空", bool(text and text.strip()))
        sys.exit(0 if not FAILS else 1)

    # ---------------- 沙箱模式（mock 服务端）----------------
    print("\n=== 沙箱模式：严格契约验证（mock llama-server）===")
    srv, port = _start_mock()
    backend = LlamaCppBackend(f"http://127.0.0.1:{port}", cfg.llama_cpp_model)

    check("available() 探测成功（/health 或 /v1/models）", backend.available() is True)

    text = backend.ocr_image(item.image_path, prompt)
    check("ocr_image 返回非空文本", bool(text and text.strip()))

    # 请求体逐字段断言
    req = _MockHandler.last_request
    msgs = req["messages"]
    content = msgs[0]["content"]
    text_part = next(p for p in content if p.get("type") == "text")["text"]
    img_url = next(p for p in content if p.get("type") == "image_url")["image_url"]["url"]

    check("请求 messages[0].role == 'user'", msgs[0]["role"] == "user")
    check("请求含 1 个 text + 1 个 image_url part",
          sum(p.get("type") == "text" for p in content) == 1 and
          sum(p.get("type") == "image_url" for p in content) == 1)
    check("文本不含 media marker（关键修复：交给原生模板自动注入）",
          not _MARKER_RE.search(text_part))
    check("文本含提示词指令", prompt in text_part)
    check("image_url 为 data:image/png;base64", img_url.startswith("data:image/png;base64,"))
    # 送出的正是 clinic 预处理后的 PNG 字节
    sent_b64 = img_url.split(",", 1)[1]
    check("送出的图片字节 == 预处理 PNG 字节", base64.b64decode(sent_b64) == prepared_png)
    # 首次请求即带 marker，无需重试
    check("首次请求即成功（POST 次数 == 1）", _MockHandler.post_count == 1)

    # 拆包正确性：返回文本应包含我们 mock 的字段
    check("拆包得到 mock 输出文本", "[MOCK OCR OUTPUT]" in text and "round trip OK" in text)
    check("拆包文本回显了送出的 prompt", prompt in text)

    print("\n---- 实际送给服务端的请求文本（无 marker，交给原生模板注入）----")
    print(text_part)
    print("\n---- 拆包得到的返回文本 ----")
    print(text)

    srv.shutdown()
    print("\n==== VERIFY SUMMARY ====")
    if FAILS:
        print(f"{len(FAILS)} FAILURE(S): {FAILS}")
        sys.exit(1)
    print("ALL PASS — 契约合规，请求格式与返回包拆包均正确。")


if __name__ == "__main__":
    main()
