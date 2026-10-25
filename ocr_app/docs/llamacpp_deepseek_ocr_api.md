# llama.cpp (llama-server) + DeepSeek-OCR 精确调用契约

> 来源（已核对并**用真实服务端反复验证**，2026-10-25，llama.cpp build b11853）：
> - DeepSeek-OCR 官方 README / PR #17400：启动 llama-server 时**不要**加 `--chat-template deepseek-ocr`；
>   发送图片 + 指令即可，服务端模板会自动注入 media marker。
> - llama.cpp server / MTMD 指南（ggml-org/llama.cpp）：多模态走 OpenAI 兼容
>   `/v1/chat/completions`；图片以 `image_url` 的 base64 data URI 传入。
> - GitHub issue #29118：b10964+ 起 media marker **每进程随机化**并通过 `GET /props` 暴露；
>   原生模板会自动注入，客户端若自己再写一个 marker 会出现 "2 markers vs 1 bitmap" → 400。
> - llama-cpp-python 文档：拆包路径 `resp.json()["choices"][0]["message"]["content"]`。
>
> 本文档是 pic2edit 调用 llama.cpp 后端的唯一权威约定，**严格按此格式发送与拆包**。

---

## 1. 启动 llama-server（参考，不在本程序内）

```bash
llama-server -m DeepSeek-OCR-Q4_K_M.gguf \
             --mmproj mmproj-DeepSeek-OCR-Q4_K_M.gguf \
             --port 8080
```

**重点（已踩坑并修复）**：
- **不要**加 `--chat-template deepseek-ocr`！该裸模板既不自动注入 media marker，
  也不把 `image_url` 块绑定到视觉编码器——结果要么 HTTP 400（`N markers vs M bitmaps`），
  要么只回通用的废话（视觉没接到）。让 llama.cpp 按模型**自动选择原生 deepseek-ocr 模板**，
  它会正确注入 marker 并绑定图片。
- 必须同时加载语言模型 GGUF 和视觉投影 `mmproj-*.gguf`，否则不支持图片输入。
- 加载成功后服务端日志会出现 `loaded multimodal model ...`、`listening on ...`。

---

## 2. 请求格式（`POST {base_url}/v1/chat/completions`）

HTTP 头：`Content-Type: application/json`。

请求体（**完全按此结构**，字段顺序无关）：

```json
{
  "model": "deepseek-ocr",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text",      "text": "<|grounding|>Convert the document to markdown." },
        { "type": "image_url", "image_url": { "url": "data:image/png;base64,<BASE64>" } }
      ]
    }
  ],
  "temperature": 0.0,
  "max_tokens": 8192
}
```

### 关键规则（已用真实服务端验证）

1. **文本里不要写任何 media marker**：原生 deepseek-ocr 模板会**自动注入**当前进程的
   media marker（b10964+ 起随机化，存于 `GET /props` 的 `media_marker`）。客户端若在文本里
   再写一个 marker（无论是 `<image>` 还是 `/props` 的随机 marker），就会出现
   "2 markers vs 1 bitmap" → HTTP 400。**所以文本只放提示词本身。**
2. **顺序无关**：实测 `content` 里 text 在前、image_url 在后的结构可正确识别（真实 OCR 输出）。
3. **图片编码**：`image_url.url` 为 `data:image/png;base64,<B64>`，MIME 必须与图片实际格式一致
   （pic2edit 统一先落为 PNG，故用 `image/png`）。
4. **`stream` 不设置**（默认 false，返回完整对象）；不要开流式，否则拆包结构不同。
5. `temperature` 取 0 保证确定性；`max_tokens` 取 8192 避免长文档被截断。

官方提示词示例（DeepSeek-OCR README，注意官方示例里的 `<image>` 是给“自托管/非原生模板”
场景的占位；原生模板下**不要**写）：
```
# document: <|grounding|>Convert the document to markdown.
# without layouts: Free OCR.
# figures in document: Parse the figure.
# general: Describe this image in detail.
```

---

## 3. 返回包格式（`HTTP 200`）

标准 OpenAI `chat.completion` 结构，**严格按此拆包**：

```json
{
  "id": "chatcmpl-xxxx",
  "object": "chat.completion",
  "created": 1730000000,
  "model": "deepseek-ocr",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "<模型输出的 Markdown / 文本>" },
      "finish_reason": "stop",
      "logprobs": null
    }
  ],
  "usage": { "prompt_tokens": 1234, "completion_tokens": 567, "total_tokens": 1801 }
}
```

### 拆包（唯一正确路径）

```python
data = resp.json()
text = data["choices"][0]["message"]["content"]
```

- 文本直接就是 Markdown / 纯文本，**没有额外包裹层**，无需再解析。
- 健壮处理：若 `choices` 为空、`message`/`content` 缺失或为 `None`，应视为异常并给出清晰错误，
  而不是 `TypeError`。

### 错误响应（HTTP 4xx/5xx）

- **400 的响应体**（实测，注意它**不包含**任何 "media markers" 字样）：
  ```json
  {"error":{"code":400,"message":"Failed to tokenize prompt"}}
  ```
  它是通用文案，靠它无法区分“0 markers”还是“2 markers”。最常见的真实成因：
  - 服务端被强制了 `--chat-template deepseek-ocr`（裸模板不绑视觉）→ 必坏，请去掉该参数重启。
  - 文本里多写/错写了 media marker → 与自动注入的 marker 叠加成 2 markers vs 1 bitmap → 400。
    修正：文本里删掉任何 `<image>` / `<__media_...__>` 之类的 marker。
- 客户端对 4xx/5xx 统一抛出并在日志里记录 `status` 与截断的 `body`（前 500 字符）以便排查。

---

## 4. 其他端点（探活）

- `GET /health` 或 `GET /v1/models`：返回 200 表示服务就绪（`available()` 用两者取并集）。
- `GET /props`：返回服务端属性，含随机化的 `media_marker`。**pic2edit 客户端不再读取它**——
  因为原生模板会自动注入，客户端写了反而 400。仅作探测可用性的端点之一。

---

## 5. pic2edit 代码落点

- `backends/llamacpp_backend.py`
  - `_build_messages(prompt, b64)`：生成 §2 的 messages 结构（text + image_url，文本不含 marker）。
  - `ocr_image()`：直接 `messages = self._build_messages(prompt, b64)` 发送，**不前置任何 marker**。
  - `_chat()`：发请求 + 按 §3 拆包，健壮处理缺失字段，错误时记日志。
- `run_llamacpp_server.py`
  - `build_command()`：**不再**传 `--chat-template deepseek-ocr`（见 §1 说明）。
- `config.py` `PROMPT_TEMPLATES`：模板**不含** `<image>` 等 media marker（由原生模板自动注入）；
  markdown 模板含 `<|grounding|>`（布局/框线感知输出，官方建议）。
- `verify_backend.py`：沙箱 mock 服务端按“文本不得含 media marker”校验请求；`--live URL` 模式
  直连真实 llama-server 打印真实 OCR 文本。
