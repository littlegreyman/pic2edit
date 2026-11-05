# pic2edit 全量代码审查报告

- **审查对象**：`master` @ `e018ffa`（Phase 2/3/4 全部收口，170 passed / ruff 全绿）
- **审查范围**：`ocr_app/` 全部 11,183 行非测试源码 + 3,308 行测试 + skill 前端 1,739 行 + CI/依赖清单
- **审查方法**：逐模块通读 + 危险 API 全量扫描（`safe_load` / `subprocess` / `eval` / `exec` / `pickle` / `extractall` / HTML 拼接 / 路径拼接）+ 依赖版本与 NVD/GHSA/OSV 最新公告对账
- **审查日期**：2026-11-06
- **基线验证**：审查前复跑 `pytest -q` → **170 passed**，工作区干净

---

## 一、结论摘要

| 维度 | 评价 | 说明 |
|---|---|---|
| 架构分层 | **B+（良好，有 5 处结构性残留）** | 门面 / DocumentController / schema 注册表三层是干净的；但包边界、双事实源、双入口参数有残留 |
| 正确性 | **B（可用，有 2 处静默数据丢失风险）** | 重复裁剪启发式、表格行布局会静默改坏/丢掉内容 |
| 安全性 | **C（有 1 个必修的供应链缺口 + 1 个无补丁依赖）** | 引擎下载链路无完整性校验；pypdfium2 内置 PDFium 命中未修复 CVE |
| 依赖健康 | **B-** | 实际安装版本大体是当前最优，但 `requirements.txt` 的**版本下限**允许装到高危旧版 |
| 测试 | **A-** | 170 项、含静态契约守卫（纯门面 / 无 Qt / 进程组回收），质量高于同类项目 |

**最需要立刻处理的三件事**（详见 §3）：

1. `pypdfium2 5.14.0` 内置的 PDFium 命中 **CVE-2026-110155**（堆溢出），且**上游尚无修复版本**——而项目对每一个 PDF 输入都走这条路径。
2. `download_models.py` 从**三个第三方反代**下载 `llama-server.exe` 并直接执行，**全程无任何 hash/签名校验**；解压用裸 `extractall`（无 Zip Slip 防护）。
3. `requirements.txt` 的 `Pillow>=10.0` 下限允许装到 10.x–12.x，而 13.0.0 之前存在一个 **CVSS 9.2 的 Ghostscript 参数注入 RCE**（CVE-2026-112900）等 4 个已修复漏洞。

---

## 二、你明确关心的那类问题：设计上的"低级不合规"

你在提问里点名的是「**修改前 Schema 直接嵌入代码**」这一类——本质是**让某个具体业务类型的知识泄漏进通用流程**。Phase 3/4 已经把这一处彻底修好了（`schemas/` 目录 drop-in + `registry` 发现 + 三态选择，`test_skill_imports_only_facade` 静态守卫）。这一点做得好，值得保留。

但同一个病根在**另外 5 个地方**还活着。它们不是"没做完"，而是**新长出来的同类结构**：

### 2.1 🔴 `ocr_app/` 不是 Python 包，导致顶层命名空间裸奔

**位置**：`ocr_app/`（无 `__init__.py`）；`backend_api.py` 用 `from backends.* import` / `from utils.* import` / `from schemas import`；`conftest.py` 与 20 个测试靠 `sys.path.insert(0, ocr_app)` 才能导入。

**为什么这是结构问题**：项目的顶层模块名是 `config`、`utils`、`schemas`、`backends`、`worker`、`gui`。这四个词在 PyPI 上**都已被占用**——我实测：

```
$ pip download --no-deps config
Downloading config-0.5.1-py2.py3-none-any.whl ... Successfully downloaded config
```

后果是双向的：

- **向内**：任何第三方依赖若 `import config`，在本进程里会拿到 pic2edit 的 `Config` 数据类而不是真正的包——因为 `sys.path[0]` 被插成了 `ocr_app/`。
- **向外**：一旦有人 `pip install -e .` 或把 `ocr_app` 当库引用，`import backends` 会撞上别人的 `backends` 包。

这正是「局部知识泄漏进通用层」的另一种形态：**项目的身份没有命名空间，靠"谁先插 sys.path"来决定同名模块归谁**。PyInstaller 打包态掩盖了它（冻结后无 sys.path 竞争），所以只在开发态/被 import 时暴露——属于"平时看不出来，出事很难查"的类型。

**建议**（可分期，不必一次做完）：

```python
# 目标形态
ocr_app/__init__.py            # 新增，声明包
ocr_app/backends/...           # 不变
backend_api.py:  from ocr_app.backends.ocr_pipeline import run
```
- **P1 短期**：加 `ocr_app/__init__.py`，把**入口脚本**（`launch_gui.py` / `main.py` / skill 脚本）改成 `sys.path.insert(0, <repo_root>)` + `from ocr_app import ...`；`tests` 改用 `pytest` 的 rootdir 机制而非逐个 `sys.path.insert`。
- **P2 中期**：内部 import 全部加 `ocr_app.` 前缀，删除所有 `sys.path.insert`。允许保留 `backend_api` 作为唯一薄转发层。
- 若嫌成本高，**最低限度**是给 `config` 与 `utils` 改名（如 `p2e_config` / `p2e_utils`）——这两个撞名概率最高。

### 2.2 🟡 同一份"页字典"被三个地方手工拼装

**位置**：
- `ocr_app/worker.py:149-156`（`_apply_schema` 里拼 `page = {...}`）
- `ocr_app/utils/export_formats.py:59-68`（`_page_dict` 再拼一遍）
- skill `pic2edit_convert.py` 内部又有一套

**为什么这是同类问题**：`schema` 子系统约定"我吃一个纯数据 page 字典"。这个约定本身对，但**这个字典的形状没有类型**，于是每个调用方都自己拼。今天两处字段一致纯属运气（`text` / `raw_text` / `lines` / `img_w` / `img_h`），**任何一方加字段另一方不知道**——这就是"契约靠人脑同步"，和当初 `docs/invoice_schema.md` 与代码各写一份字段表是同一个病。

**建议**：在 `schemas/base.py` 里定义

```python
@dataclass(frozen=True)
class PageData:
    text: str = ""
    raw_text: str = ""
    lines: tuple = ()
    img_w: int = 0
    img_h: int = 0
    page: int = 1
    schema: dict | None = None
    schema_id: str | None = None
    def as_dict(self) -> dict: ...
```
`apply_schemas` / `render_schema` 接受 `PageData | dict`，两个调用方只构造 `PageData`。新增字段时改一处，`mypy`/测试即报错。

### 2.3 🟡 `ocr_pipeline.run()` 有 10 个参数，且新旧两套路由语义并存

**位置**：`ocr_app/backends/ocr_pipeline.py:89-99`

```python
def run(items, vlm_backend=None, server_url=..., model=..., use_vlm_fallback=True,
        mode="markdown", skip_discriminative=False, prompt=None,
        guard_hallucination=True, model_params=None, policy=None)
```

`policy` 与 `skip_discriminative` / `guard_hallucination` 是**同一件事的两套表达**，靠 `if policy is not None` 决定谁优先。注释写着"保留旧参数仅为向后兼容"——但两个前端现在**都已传 policy**，旧参数只剩测试在用。这种"兼容层"没有到期日，就会永久留下来变成"两套路由"。

**建议**：把 `run()` 的入参收敛为一个 `OcrRequest`（`items` / `policy` / `mode` / `prompt` / `model_params` / `vlm_backend`），旧关键字参数保留一个版本周期后删；用 `DeprecationWarning` 而不是静默优先。

### 2.4 🟡 `gui.py` 里还留着一个"平行数据结构"——Phase 3.1 想消灭的东西

**位置**：`ocr_app/gui.py:435` `self.page_tree_items: list`（"与 self.items 平行"），写于 `_add_document`（680）与 `_rebuild_tree`（728），读于 `_on_item_done`（1071）。

Phase 3.1 的核心结论是「模型是唯一事实源，树是单向视图」。但 `page_tree_items` 是**第三份**必须与 `controller.items` 手工保持同序的列表。它现在没错（增删都 `_reindex()`），但一旦有人新增一条"不重建树"的路径，`_on_item_done` 就会更新错行。

**建议**：删掉这个列表，改为按需查找：

```python
tw = self.tree_widget.currentItem()   # 或从 UserRole+1 反查
```
或者让 `_show_page` 记住 `self._page_nodes: dict[int, QTreeWidgetItem]` 由 `controller` 的扁平索引作键，并在 `_rebuild_tree` 里一次性重建（单一写入点）。

### 2.5 🟡 `manifest.layout` 一个字段承担两种语义

**位置**：`schemas/invoice/__init__.py:32` `layout = "html_layout.py"`；`registry.py:174-176` 无条件 `provider.layout = layout`；`lint.py:60-62` 对插件只判 `isfile`。

对声明式 schema，`layout` = **HTML 版式文件**；对插件式，它被复用成 **Python 模块名**。`registry` 和 `lint` 都不区分这两者，只是"文件存在就算过"。将来若有工具按声明式语义去 `open(provider.layout).read()`，会读到 Python 源码当 HTML 渲染。

**建议**：拆成两个键——`layout`（HTML，可选）与 `entry`（Python，可选）；发票的 HTML 由 `render_html()` 现算，本就不该声明 `layout`。删掉 `invoice/manifest.json` 里的 `layout` 键即可，`lint` 那两行也自动正确。

### 2.6 💭 `_dup_tail` 与 `_periodic_tail` 是 O(n·k) 的朴素实现

**位置**：`utils/text_utils.py:102-105`

```python
for k in range(upper, min_len - 1, -1):
    suffix = text[-k:]              # 每轮都复制一次 k 字符
    idx = text.find(sig, 0, n - k)  # 每轮 O(n) 扫描
```
`upper` 上限 4000，长文本（10 万字符）下最坏约 4×10⁸ 次字符操作。它在 **GUI 的 worker 线程里对每一页都跑一次**，是长文档转换"越到后面越慢"的隐藏原因之一。

**建议**：`text.endswith(...)` 与切片比较改在命中候选后再做；或先按 `anchor_len` 建一个后缀哈希索引。

---

## 三、🔴 Blocker（必须修）

### 3.1 `pypdfium2 5.14.0` 内置 PDFium 命中未修复的堆溢出 CVE

| 项 | 内容 |
|---|---|
| CVE | **CVE-2026-110155**（PDFium AGG 光栅化 heap buffer overflow，CWE-122）；另 **CVE-2026-121113** 修复于 PDFium 157.0.8103.38 |
| 公告 | PyPA advisory-database #401：**pypdfium2 ≤ 5.14.0 全部受影响，无修复版本**；5.14.0 打包的是 chromium/8076（2026-09-29），修复提交 58e1130 首见于 chromium/8100 |
| 项目暴露 | **100%**。`utils/input_prep.py:93-115` 对每个 PDF 的**每一页**调用 `page.render(scale=scale)`。用户打开任何外部 PDF 都走这里 |
| 严重度 | Chrome 侧评为 **High**；影响是渲染期的 OOB 写/越界访问，本地进程内可导致崩溃，理论上可被利用 |

**这是本次审查唯一的"无补丁可打 + 必然触发路径"组合。** 它不属于项目代码的错，但项目承担了风险，必须给出处置。

**处置方案（三选一，建议 A+B 并行）**：

- **A. 缓解（今天就能做）**：在 `prepare_pdf` 前后加进程隔离——把 PDF 渲染放到子进程（复用现有 `_rapidocr_cli.py` 的子进程模式），子进程崩溃只损失当页，不影响主进程：
  ```python
  # 失败即返回空页 + 明确提示，而不是把整个 GUI 带走
  ```
- **B. 跟进上游**：订阅 `pypdfium2-team/pypdfium2#487`，chromium/8100 落地后立刻抬版本；`requirements.txt` 写 `pypdfium2>=5.14.0` 之外**加一行注释**指向该 issue，避免下次有人以为"已经最新了"。
- **C. 自建轮子（重）**：用 `pdfium-binaries chromium/8100+` 源码构建 pypdfium2。适合发布正式版前做一次。
- **同时**：`README` 加一句"仅处理可信来源 PDF；不可信文件请在隔离环境转换"。这与项目"本地离线处理敏感票据"的定位不冲突，但必须写明。

### 3.2 引擎下载链路：第三方反代 + 零完整性校验 + 裸解压

**位置**：`ocr_app/download_models.py:41-46`、`150-196`

```python
GITHUB_MIRRORS = [
    "https://github.com",
    "https://ghfast.top/https://github.com",
    "https://ghproxy.net/https://github.com",
    "https://mirror.ghproxy.com/https://github.com",
]
...
def extract_archive(path, dest):
    if path.endswith(".zip"):
        with zipfile.ZipFile(path, "r") as z:
            z.extractall(dest)          # ← 无 Zip Slip 防护
    elif path.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path, "r:gz") as t:
            t.extractall(dest)          # ← Python ≤3.11 默认 fully_trusted
```
下载完成后 `gather_libs()` 找到 exe 并 `chmod 0755`，GUI 一转换就**执行它**。

**为什么是 Blocker**：git 拉代码时跑的是 GitHub 官方 TLS，但**二进制引擎的信任被静默转交给了三个第三方反向代理**。任一代理（或其 CDN、其运营者）替换 zip 内容，用户得到的 `llama-server.exe` 就是任意代码。而 `requirements.txt` 声明支持 Python 3.10+，在 3.10/3.11 上 `tarfile.extractall` 默认 `fully_trusted`，配合 `../../` 条目可写到安装目录之外（`zipfile` 至今无此防护，任何版本都受影响）。

**建议（按性价比排序）**：

1. **加 SHA256 白名单**（成本最低、收益最大）：
   ```python
   ENGINE_SHA256 = {           # 发布时把实测值钉进来
       "llama-b11853-bin-win-cuda-12.4-x64.zip": "…",
       "llama-b11853-bin-ubuntu-cuda-12.8-x64.tar.gz": "…",
   }
   def _verify(path, expected):
       got = hashlib.sha256(open(path,"rb").read()).hexdigest()
       if expected and got != expected:
           raise RuntimeError(f"引擎校验失败（疑似被替换）：{got[:16]}…")
   ```
   只在 GitHub 官方域名下载、镜像失败则不降级（或镜像只用于"重试"，仍做 hash 校验）。
2. **加解压守卫**：
   ```python
   for m in z.infolist():
       dest = os.path.realpath(os.path.join(root, m.filename))
       if os.path.commonpath([dest, root]) != root:
           raise RuntimeError(f"非法归档路径：{m.filename}")
   ```
   tar 侧显式传 `filter="data"`（3.12+ 默认，3.10/3.11 需手写）。
3. **`ensure_hf_hub()` 的运行时 `pip install` 应移除**（`download_models.py:235-245`）：程序在运行时改自己的依赖树，既是供应链面也违反最小惊讶。改为在 `requirements.txt` 里声明、启动时若缺失给出可复制的安装命令。
4. **模型侧**：`snapshot_download` 对 LFS 文件有 sha256 校验（部分缓解），但 `HF_MIRRORS` 里的 `hf-mirror.com` 同样是第三方。建议对 `deepseek-ocr-Q4_K_M.gguf` 也钉一个 hash 常量——这与项目已有"模型已用 SHA256 验证"的做法一致，只是没落进代码。

### 3.3 `requirements.txt` 的版本下限允许装到有 RCE 的旧版

**位置**：`ocr_app/requirements.txt`

当前实测安装的是 Pillow **13.0.0**（最优）。但清单写的是 `Pillow>=10.0`——在离线机、锁文件缺失、依赖解析回退等场景下，`pip` 完全可能装到 10.x–12.x，而 13.0.0 之前存在：

| CVE | 影响 | 修复版本 |
|---|---|---|
| CVE-2026-112900 | EPS/Ghostscript **参数注入 → RCE**（CVSS 9.2） | 13.0.0 |
| CVE-2026-96778 | JPEG COM/ICC **堆溢出**（CVSS 8.8） | 13.0.0 |
| CVE-2026-96640 | PDF `/Pages` DAG **资源耗尽**（CVSS 8.2） | 13.0.0 |
| CVE-2026-112401 | GIF 注释**二次方复制** DoS | 13.0.0 |
| CVE-2026-59204 | JPEG2000 tiled **OOM** | 12.3.0 |

本项目**确实**把不可信图片交给 Pillow（`input_prep.prepare_image/tif`、`mask.mask_copy`）。

**建议**：把所有"用到了原生解析器"的依赖下限抬到已修复线，并改成 `~=` 锁小版本：

```txt
Pillow>=13.0.0,<14        # 13.0.0 修 CVE-2026-112900(9.2)/96778/96640/112401
pypdfium2>=5.14.0,<6      # 见 §3.1：5.14.0 仍带 CVE-2026-110155，仅锁不降级
PySide6>=6.12.1,<6.13     # 6.12.0 命中 CVE-2026-105924（本项目未走 CBOR 路径，但白拿）
urllib3>=2.8.0            # 2.8.0 修 CVE-2026-97688/97689（chunked 无限循环/无界缓冲）
markdown>=3.11.0          # 3.11.0 修 CVE-2019-25781（链接括号二次方回溯）
```

---

## 四、🟡 安全问题（应修）

### 4.1 schema 插件是无条件的任意代码执行入口

**位置**：`ocr_app/schemas/registry.py:71-79、151-160`

```python
def _load_by_path(py_path, mod_name):
    spec = importlib.util.spec_from_file_location(mod_name, py_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)          # ← 无条件执行
```
扫描根包含 `~/.pic2edit/schemas` 与 `$PIC2EDIT_SCHEMA_DIR`。**任何放进该目录的 `provider.py` 在 GUI 启动时就会被执行**（`_reload_schema_combo` → `list_schemas()` → `reload()`）。同时 `manifest["entry"]` 未做路径约束，可以写成 `"../../../tmp/x.py"` 指向包外文件。

对比：声明式 schema（yaml + html）是**纯数据、零执行**，这是正确的默认。插件式是必要的逃生口，但**它不该是"静默自动执行"**。

**建议**：
- `entry` 必须落在包目录内（`os.path.commonpath` 校验），拒绝绝对路径与 `..`。
- 首次加载用户目录下的 `provider.py` 时**要求显式授权**：在 `config.json` 记 `trusted_schemas: ["invoice"]`，未授权的插件包只出现在"发现列表"里并标注"未启用（需要信任）"，选中时才询问一次。GUI 侧一个复选框即可。
- `_scan` 跳过符号链接目录（`os.path.islink`）。

### 4.2 HTML 注入链：VLM 输出 → raw HTML 直通 → 导出文件 / GUI 预览

三个点串成一条链：

1. **导出 HTML**：`utils/export_formats.py:145`
   ```python
   body = _md_lib.markdown(item_text(it, "md"), extensions=["tables", "fenced_code"])
   ```
   Python-Markdown **默认保留原始 HTML 直通**（无 `safe_mode`，该参数已废弃）。OCR 文本里出现 `<script>`、`<iframe>`、`<img src=http://…>` 会**原样写进导出文件**。而项目**主动**让 VLM 输出 HTML（`html_table` 模式，`config.py:48`），提示词注入就有了稳定的产出通道。
2. **GUI 预览**：`gui.py:885-890`
   ```python
   html = markdown.markdown(text, extensions=["tables", "fenced_code"])
   self.viewer.setHtml(html)
   ```
   `QTextBrowser` 不执行 JS，但**会解析并加载 `<img src>`、`<link>` 指向的资源**——恶意文档里的远程图片会产生外连（DNS/内网探测），本地 `file://` 图片会被读入渲染。
3. **schema 版式**：`schemas/runtime.py:562-571` 里 `{{! path }}` 是**不转义**逃生口。默认 `{{ path }}` 转义（这点做对了），但一个 schema 作者为了"保留换行"误用 `{{! }}`，就把 OCR 文本原样注入。

**建议**：
- 导出 HTML 前过一遍白名单清洗（`bleach` 太重，可用 `html.parser` 重写或干脆 `html.escape` 后只放行 `p/br/table/tr/td/th/h1-h6/ul/li/a[href^=https]`）。**最低限度**：把 `markdown` 输出中 `<script`/`<iframe`/`<object`/`on\w+=` 一律转义并记日志。
- GUI 预览同样清洗；并给 `viewer` 设 `document().setBaseUrl(QUrl("about:blank"))` 阻断相对/本地资源解析。
- 把 `{{! }}` 从文档里删掉（当前 `asset_card/layout.html` 与模板都没用它），或者改名成 `{{raw: }}` 逼开发者显式意识到风险。

### 4.3 VLM 输出的表格属性 → 未校验整数 → OOM / 导出崩溃

**位置**：`utils/table_html.py:38-42`、`70-116`

```python
cell = {"text": text,
        "rowspan": int(self._attrs.get("rowspan", 1) or 1),   # ← 非数字直接 ValueError
        "colspan": int(self._attrs.get("colspan", 1) or 1)}
```
`layout_table()` 随后 `for dr in range(rs): for dc in range(cs):` 逐格建网格。VLM 幻觉出一个 `rowspan="10000000"` 就是 10⁷×n 的内存分配 —— 一次导出能把内存打满；`rowspan="abc"` 则让 `parse_html_tables` 抛异常，一路冒到 `export_selected` 的 `except Exception`（用户只看到"导出失败"，无原因）。

注释自己也写了"VLM 在合适的提示词下输出 HTML 表格"——即**这段代码的输入天然不可信**（`html_table` 模式就是专门让模型吐 HTML 的）。

**建议**：
```python
def _span(attrs, key, limit=200):
    try:
        v = int(attrs.get(key, 1) or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(v, limit))
```
并在 `layout_table` 里对 `nrows*ncols > 200_000` 直接放弃（返回空表 + 一条 warning）。

### 4.4 `schemas.cli new` 的 id 未校验 → 目录穿越写

**位置**：`schemas/cli.py:193-199`

```python
pkg = os.path.join(root, args.id)
if os.path.exists(pkg) and not args.force: ...
os.makedirs(os.path.join(pkg, "samples"), exist_ok=True)
```
`python -m schemas.cli new ../../foo --force` 会写到 `$PWD/../../foo`，覆盖同名文件。本地 CLI 工具、攻击者需先能执行命令，实际风险低；但 `id` 同时被写进 `manifest.json` 与 `schema.yaml`，**它本来就应该是一个受约束的标识符**（注册表以它为 key）。顺手加上校验即可：

```python
if not re.fullmatch(r"[a-z][a-z0-9_\-]{0,31}", args.id):
    print("id 必须是 1–32 位的 [a-z0-9_-]，且以字母开头", file=sys.stderr); return 2
```

### 4.5 `export_json` 写入本机绝对路径

**位置**：`utils/export_formats.py:178` `"source": source or ""`

导出 JSON 里带 `/home/alice/客户资料/2026/发票.pdf`。用户把这个 JSON 交给下游（RAG 入库、发给同事、上传资料库）时，**目录结构与用户名一并外泄**。票据场景恰恰是最敏感的一类。

**建议**：默认只写 `os.path.basename(source)`，完整路径放在 `--with-source-path` 之类显式开关后面。

### 4.6 skill 的共享缓存目录可被本机其他用户篡改

**位置**：skill `pic2edit_convert.py:181-185`

```python
d = os.path.join(tempfile.gettempdir(), "pic2edit_cache")
```
`/tmp/pic2edit_cache` 名固定、权限跟 `os.makedirs` 默认（0755），文件按内容 hash 命名但**内容本身无签名**。多用户机器上，另一个本地用户可以预置 `sha256(输入hash).json`，让本次转换读出别人伪造的识别结果——对"票据金额"这种场景是实打实的数据完整性风险。

**建议**：目录改为 `os.path.join(tempfile.gettempdir(), f"pic2edit_cache_{os.getuid()}")`（Windows 用 `getpass.getuser()`），`makedirs(mode=0o700)`；读缓存时校验文件属主。

### 4.7 缺少输入规模上限（DoS / 磁盘打满）

`_add_paths`（gui.py:682）接受任意数量文件；`prepare_pdf` 对每页无条件渲染 200 DPI PNG。一个 3000 页 PDF → 3000 张 A4@200DPI PNG ≈ 数 GB 临时文件 + 长时间占满磁盘。skill 侧同理。

**建议**：`prepare_pdf` 加 `max_pages`（默认 500，超出提示确认）；`_add_paths` 加"单次最多 N 个文件/累计最大页数"的软限；`work_dir` 加磁盘剩余空间预检。

---

## 五、依赖 CVE 对照表（2026-11-06 检索）

> 判定口径：**受影响** = 本项目实际调用了漏洞描述里的代码路径；**不适用** = 版本或调用路径不命中。版本取自 `ocr_app/.venv` 实测。

| 依赖 | 实测版本 | 相关公告 | 判定 | 说明 |
|---|---|---|---|---|
| **pypdfium2** | 5.14.0 | CVE-2026-110155（AGG 堆溢出，High）<br>CVE-2026-121113（PDFium 157.0.8103.38 修复） | 🔴 **受影响，无修复版** | 每页 `page.render()` 必走；见 §3.1 |
| **Pillow** | 13.0.0 | CVE-2026-112900(9.2) / 96778(8.8) / 96640(8.2) / 112401 / 59204 | ✅ 已修 | 但 `>=10.0` 下限不安全，见 §3.3 |
| **urllib3** | 2.8.0 | CVE-2026-97688（deflate 无限循环）<br>CVE-2026-97689（chunked 无界缓冲） | ✅ 已修 | 2.8.0 即修复版本 |
| **requests** | 2.34.2 | — | ✅ | 使用处均有 `timeout` |
| **markdown** | 3.11.2 | CVE-2019-25781（链接括号二次方回溯，≤3.11.2 影响 → 3.11.0 已修 #798）<br>CVE-2025-69534（畸形 HTML DoS，3.11.0 修 #1617）<br>CVE-2026-86303 | ✅ 已修 | **CVE-2026-86303 属同名 C 库 `92181 markdown`，与 Python-Markdown 无关（误关联）** |
| **PySide6 / Qt** | 6.12.0 | CVE-2026-105924（CBOR→JSON 空指针，影响 6.12.0，修 6.12.1）<br>CVE-2026-15037 / 16762 / 8168 / 101201 / 107472 / 95620 | 🟡 库存受影响，**路径不命中** | 项目不解析 CBOR/QDom/XML/SVG；6.12 已修大部分。仍建议升 6.12.1（白拿） |
| **llama.cpp** | b11853 | CVE-2026-33298（GGUF 整数溢出 RCE，<b7824 受影响）<br>CVE-2026-7482 "Bleeding Llama"（<b4120）<br>oss-security 2026-05-15 GGUF parser 多漏洞（V-01 alignment 无上界，声称影响全部版本） | ✅ 前两个已修<br>🟡 第三个未编号/未修复 | b11853 > b7824/b4120。V-01 需恶意 GGUF 才能触发——项目只加载官方 repo 的固定模型（且已 SHA256 验证过），**用户手工替换 GGUF 是唯一入口**，README 应提示 |
| **onnxruntime** | 1.31.1 | CVE-2026-14647（ONNX shape inference OOB read，影响 onnx ≤1.21.x） | ✅ 不适用 | 版本远高于 1.21；且只加载 rapidocr 内置模型 |
| **PyYAML** | 6.0.3 | CVE-2017-18342 / CVE-2020-14343 | ✅ 已修 | 且代码只用 `yaml.safe_load`（`runtime.py:77`），双保险 |
| **opencv-python-headless** | 5.0.0.93 | 库内关联 4 个"无修复"历史 CVE（imread/imdecode 解析，2017–2019） | 🟡 低 | 仅 `handwriting_screen.py` 使用，且由 `PIC2EDIT_HW_FORCE=1` **默认关闭**——这个默认值本身就是一道正确的防御 |
| **python-docx** | 1.2.0 | 未检索到 2026 年相关 CVE | ✅ | 只写不读外部 docx |
| **rapidocr** | 3.10.0 | 未检索到公告 | ✅ | 纯 Python + 内置 ONNX 模型 |
| **certifi / idna / charset-normalizer** | 2026.10.23 / 3.20 / 3.5.2 | — | ✅ | 均为当前版本 |

**关于 Qt 的一条补充**：`CVE-2026-105924` 虽然"路径不命中"，但项目里 `QTextBrowser.setHtml()` 接收的是**不可信 HTML**（OCR 文本）。Qt 的 rich-text 解析器历史上有过递归/崩溃类问题（如 CVE-2026-8168 SVG 深嵌套）。虽然 SVGs 走 `QImageReader` 不走这里，我仍建议按 §4.2 做输入清洗——**不依赖"Qt 目前没有这条 CVE"来保证安全**。

---

## 六、💭 Nits（有空再说）

1. **`ocr_pipeline.py:21` `sys.path.insert(0, HERE)` 是无效代码**：`HERE` 是 `ocr_app/backends/`，而下面 `from backends.discriminative_backend import ...` 需要的是 `ocr_app/` 在 path 上。这条 insert 只让 `backends/` 目录里的裸模块名可见（目前无害，但它是"模块遮蔽"的现成入口）。删掉即可，或改成 `os.path.dirname(HERE)`。
2. **`gui.py:1011` `self._server_proc = starter._proc`** 跨对象读私有属性。`ServerStarter` 应暴露 `@property def proc`。
3. **`discriminative_backend.py:209-210` 子进程失败只 `return None`**，`r.stderr` 被丢弃。之前修过同类"静默失败"问题（skill 的引擎日志落盘），这里还留着一处。
4. **`config.py:15` / `logging_setup.py:24` 在模块导入时调 `writable_app_home()`** → import 即写盘，且 `.writable_test` 固定文件名在并发启动时互相删（TOCTOU）。改为惰性求值（`@lru_cache` 的函数）。
5. **`export_formats.py:134-148` 页码用 `enumerate(pages, 1)` 但会跳过空页**，导致"第 1 页、第 3 页"跳号。改成先算有效页再编号。
6. **`run_llamacpp_server.py:58` / `download_models.py:207` 用 `sorted(hits)[0]` 选可执行文件**——多候选时按字典序取，非确定性（`b11853` vs `b11853-new` 之类）。应显式报错让人选。
7. **`mask.py:59` 在 `backends/` 包内用绝对 `from backends.discriminative_backend import ...`**，包内应 `from .discriminative_backend`。同类：`worker.py` 从 `backend_api` 和 `utils.*` 混着 import。
8. **`ocr_pipeline._HALLUCINATION_MARKERS` 只有 12 条英文**，且 `figure` 模式直接关守卫。零幻觉保证只对判别式路径成立，VLM 兜底本质无保证。建议在导出物里标注每页 `source=disc|vlm`（skill 已有 `_classify_engine`，GUI 没有）——让用户知道哪一页不是"零幻觉"产出的。
9. **`cli.py:34-43` 模板 manifest 里有 `"layout": "layout.html"`，但 `new` 生成的是带 `{{ title }}` 未被替换的 layout**（`_TEMPLATE_LAYOUT` 里 `<h2>{title}</h2>` 会被 `_fill` 替换 ✓）。这条没问题，只是提醒 `_fill` 用 `replace` 的写法对 `{id}` 与 `{title}` 之外的 `{...}` 无感，未来加新占位容易踩。
10. **CI 用 `actions/checkout@v4` 等 tag 而非 SHA**，且 `pip install -r requirements.txt` 不锁版本 → 构建不可复现，且是典型的 CI 供应链面。建议 tag→SHA 固定，并加 `--require-hashes` 或提交 `requirements.lock`。

---

## 七、修复路线图（建议顺序）

| 优先级 | 事项 | 成本 | 收益 |
|---|---|---|---|
| **P0** | §3.1 PDF 渲染进程隔离 + README 风险声明 | 0.5 天 | 把"无补丁依赖"的爆炸半径关进子进程 |
| **P0** | §3.2 下载器加 SHA256 白名单 + 解压路径守卫 + 去 `pip install` | 0.5 天 | 堵住唯一的高危供应链缺口 |
| **P0** | §3.3 `requirements.txt` 抬下限（Pillow/pypdfium2/PySide6/urllib3/markdown） | 0.2 天 | 一次性消灭 6 个已知 CVE 的"可落到旧版"空间 |
| **P1** | §2.1 `ocr_app` 包化（先加 `__init__.py` + 入口改 rootdir） | 1 天 | 消除顶层命名空间污染（已实测 `config` 撞名） |
| **P1** | §4.1 schema 插件信任门 + `entry` 路径约束 | 0.5 天 | 把"启动即执行任意代码"变成显式授权 |
| **P1** | §4.2 HTML 清洗（导出 + 预览 + `{{! }}`） | 0.5 天 | 断掉 VLM 注入 → XSS/外连链路 |
| **P1** | §4.3 rowspan/colspan clamp | 0.2 天 | 防 OOM、修导出崩溃 |
| **P2** | §2.2 `PageData` 类型 / §2.3 `OcrRequest` / §2.4 删 `page_tree_items` / §2.5 拆 `layout`/`entry` | 1.5 天 | 让"同类问题"不再复发 |
| **P2** | §4.5 路径脱敏 / §4.6 缓存目录隔离 / §4.7 规模上限 | 0.5 天 | 隐私与 DoS |
| **P2** | §2.6 `_dup_tail` 性能 / §6 其余 Nits | 1 天 | 长文档体验 |

---

## 八、值得肯定的三处设计

按审查惯例，也要说清楚哪些地方做得对——这些是应该被**复制到其他模块**的模式，而不是被下一步重构改掉的：

1. **`schemas/` 的三层分离**（`base` 协议 → `registry` 目录发现 → `runtime`/插件两种档位）真正做到了"新增凭证不改转换流程"。这是本项目最重要的一次架构胜利，`test_skill_imports_only_facade` 那条静态守卫是它的保险丝——**别为了省事去拆**。
2. **`EngineLauncher` 的进程组回收** + `test_gui_close_uses_process_group_stop` 这条守卫。它把"孤儿进程占显存"这种只在用户机器上偶发、极难复现的问题，变成了 CI 里一条会红的断言。同类问题（如 §4.1 的插件执行）值得照抄这个"用测试钉住行为"的做法。
3. **`RoutingPolicy` 收敛 + `_hw_force()` 默认关闭**。手写预筛误报率 65% 就默认关掉，而不是"先上线再说"——这个判断把"能力"和"默认开启"分开了，是成熟的产品工程取舍。

---

## 附：本次审查的覆盖清单

- 已逐行通读：`backend_api` `document_controller` `engine_launcher` `worker` `gui` `config` `ocr_pipeline` `routing` `discriminative_backend` `llamacpp_backend` `server_launcher` `mask` `registry` `runtime` `lint` `cli` `invoice/{__init__,render,html_layout}` `utils/{input_prep,export_formats,export_docx,table_html,faithful_html,text_utils,logging_setup,app_home,usage_log}` `download_models` `run_llamacpp_server` `.github/workflows/ci.yml` `requirements*.txt` `ruff.toml`
- 已静态扫描但未逐行：`handwriting_screen` `model_select` `table_layout` `table_utils` `assemble` `transformers_*` `download_models_tf` `invoice/extract.py`(1349 行，按函数清单 + 危险模式扫描) `skill/pic2edit_convert.py`(1080 行，按危险调用点 + 结构审查)
- 未覆盖：`packaging/`（Inno Setup 脚本）、模型文件二进制内容、真机 GPU 路径运行时行为
