# 需求符合性验证矩阵（2026-11-06）

配套文档：`code-review-2026-11-06.md`（安全/架构审查）、`code-completeness-2026-11-06.md`（需求完整性审查）。

这份矩阵存在的唯一目的：**回答"改了这么多，到底有没有真的符合需求"**。
用户 2026-11-06 的指令原话是「代码修改后记得验证是否已真的符合需求，避免再犯在 phase 3.3
上面的遗漏的错误」——Phase 3.3 的失败模式是「审计列了缺口 → 写进计划 → 然后没做」。
所以这里每一行都必须给出**可复核的证据**，而不是"已完成"三个字。

图例：✅ 已落地并有证据 ／ 🟡 部分 ／ ❌ 未做（明确立项，不在本轮范围）

基线：`e018ffa`（170 passed）→ 本轮结束 `e550389`（**240 passed**，ruff 全绿）。

---

## 一、审查路线图（completeness §7 的 12 项）

| # | 事项 | 状态 | 证据 |
|---|---|---|---|
| 1 | 缓存命中补 `apply_schemas` + 测试 | ✅ | `_apply_schema_stats()` 供两条路径共用；`test_cache_hit_reapplies_schema`、`test_cache_hit_matches_fresh_render`、`test_cache_hit_does_not_lie_in_json_metadata` |
| 2 | 5 条文档失真 | ✅ | 见第二节逐条 |
| 3 | GUI `export_html` 接 `page_with_image` | ✅ | `export_formats.export_html` 三档降级（schema → 原图垫底 → 重排）；`test_export_formats.py::test_export_html_uses_image_overlay_when_no_schema` |
| 4 | 产物补 `schema_version` | ✅ | `registry.apply()` 写 `page["schema_version"]`，md/html/json 三处落盘（html 走 `<meta name="pic2edit-schema">`）；`test_cache_hit_does_not_lie_in_json_metadata` 断言 `versions` |
| 5 | `--mode` 加 `html_table`；4 个采样参数 flag | ✅ | `--help` 实测出现 `{markdown,ocr,figure,table,html_table}` 与 `--temperature/--repeat-penalty/--repeat-last-n/--max-tokens`；`test_mode_choices_cover_html_table`、`test_skill_cli.py` 参数面 |
| 6 | 抽 `ensure_engine()` 进门面 | ✅ | 新建 `backends/engine_boot.py`（非 Qt），`server_launcher` 删掉本地 5 个私有实现改复用；`test_server_starter_uses_shared_diagnostics`（断言是**同一个函数对象**） |
| 7 | `--list-schemas` 打印 origin | ✅ | 实测：把 `asset_card` 拷到 `$PIC2EDIT_SCHEMA_DIR` 改版本 9.9.9 → 列表输出 `asset_card v9.9.9 user`；`test_list_schemas_prints_origin`、`test_list_shows_load_origin_not_declared_kind` |
| 8 | 缓存键并入档位/跳过标志 | ✅ | `_cache_key_for()` 含 `vlm:`/`skip:`/`mask:`；`test_cache_key_includes_recognition_params` |
| 9 | skill 支持目录输入 | ✅ | 实测：目录（含子目录、1 个坏文件、1 个 .txt）→ 2 个产物落在 `--out`，坏文件报 `[error]` 后继续，退出码 1；`test_expand_inputs_walks_directory` 等 3 项 + `test_multi_input_requires_out_directory` |
| 10 | `cli pack` | ✅ | 实测：`python -m schemas.cli pack asset_card` → 5 文件、顶层 `asset_card/`、打印的 sha256 与 `sha256sum` 一致；`test_pack_via_id_not_path`、`test_new_then_pack_roundtrip`、`test_pack_refuses_broken_package` |
| 11 | 输入分诊（PDF 文本层 / 内嵌 XML） | ❌ | 未做。属 Phase 5「输入分诊」，需单独立项（报告 §6.2–6.3） |
| 12 | 国标 schema 生成器（342 字段） | ❌ | 未做。报告 §6.1，需单独立项 |

---

## 二、5 条文档失真（completeness §5）

| # | 原文 | 处理 | 证据 |
|---|---|---|---|
| 1 | 两处 README：「抽取 PDF 内嵌图形作为独立图像项」 | ✅ 改为「整页高 DPI 渲染（不再抽取页内嵌图）」并说明原因 | `git log -p e550389 -- README.md ocr_app/README.md`；`references/api_contract.md` 同步 |
| 2 | `ocr_app/README.md` 分辨率模式 = 伪能力 | ✅ 按用户决定「接上」：下拉从 llama.cpp 页挪到 transformers 页，`--mode` 真正下发 | `test_resolution_combo_lives_on_transformers_page`、`test_start_transformers_passes_mode_and_port`、`test_transformers_launcher_builds_cmd_with_mode` |
| 3 | README「四种模式」 | ✅ 改五种并补「表格（HTML/合并单元格）」行 | `README.md` 输出格式表 |
| 4 | `VERSION` 0.18 vs README 0.19 | ✅ VERSION 首行改 0.19 并补 0.19 变更条目；同时修该文件「核心能力」里过时的两条 | `head -1 VERSION` |
| 5 | SKILL.md 掩膜节称「skill 层预处理，不改 pic2edit」 | ✅ 已改为「已下沉内核 `backends/mask.py`」 | `SKILL.md` 掩膜节 |

---

## 三、本轮**真跑命令**新发现并修掉的问题（单测原本拦不住）

这四条是这份矩阵最该看的部分：它们证明"全绿 ≠ 符合需求"。

| # | 问题 | 如何发现 | 处理 | 守卫测试 |
|---|---|---|---|---|
| 1 | `--cache --format html` 从「原图垫底」静默退化为「纯文本重排」，日志仍写"按原图版面还原" | 连跑两次同图导 html，`grep -c data:image/png;base64` 首跑 1、二次 0 | 命中分支 `_ensure_page_lines()` 按需补算坐标并回写缓存；日志改为逐档如实汇报 | `test_cache_hit_html_rebuilds_faithful_layout`、`test_render_html_log_reports_actual_tier` |
| 2 | 缓存中的 `source` 是上次运行的绝对路径；页里 `image` 是临时 PNG 路径 | 同图换目录跑 `--format json`，`/tmp` 仍在产物里 | 两者都不进缓存，读取时按本次运行重写 | `test_cache_payload_drops_run_scoped_fields`、`test_json_source_is_basename_only` |
| 3 | 安全修复只覆盖一条路径：内核 `export_json` 已只写 basename，skill 自拼 JSON 仍写 `abspath` | 同上，看 json 产物 | skill 侧改为 `os.path.basename` | 同上 |
| 4 | 引擎忽略 `--url` 的端口：起在 8080 却去 8181 等就绪（必超时 + 孤儿进程） | 真实调用 `ensure_engine("http://127.0.0.1:8181")`，引擎日志显示 `listening on :8080` | 新增 `port_of(url)`，`build_command(port=)` / `build_cmd(mode, port)` 贯通；GUI 侧同修 | `test_port_of_reads_url`、`test_build_command_honours_port`、`test_ensure_engine_launches_on_requested_port`、`test_gui_starter_passes_url_port_to_engine` |

附加（同类"用户可修的错误变成异常栈"）：坏图片/坏 TIFF 现在抛 `InputPrepError`，CLI 输出
`[error] 无法解码图片：<文件名>（…）`、退出码 3；批量时跳过该文件、合计 1。
校验前移到**引擎拉起之前**（不该先等模型加载几十秒才报"文件坏了"）。
守卫：`test_corrupt_image_raises_inputprep_error`、`test_corrupt_tif_raises_inputprep_error`。

---

## 四、收口核对

| 项 | 结果 |
|---|---|
| 全量测试 | `240 passed`（基线 170） |
| ruff | `All checks passed`（规则 E/F/W，`ruff==0.17.4`） |
| 工作区 | 干净（`git status --short` 空） |
| 引擎进程 | 真拉起验证后已 `stop()` 回收，`ps` 无残留 `llama-server` |
| 未推送 | 本地 `master` 领先 `origin/master` 11 个提交（沙箱无 PAT，需用户在原生桌面推送） |

## 五、仍然已知的边界（不假装已解决）

- 声明式 schema 无跨页合并与几何推理；HTML 版式是"人读预览"，**不是**合规版式文件
  （电子会计档案需 OFD，DA/T 94-2022），无数字签名与监制章套印。
- `pypdfium2` 内置 PDFium 的 AGG 堆溢出（CVE-2026-110155）上游无修复版本；当前缓解是
  "不信任输入尺寸 + 单进程渲染"，彻底方案（渲染进程隔离）未做。
- llama.cpp b11853 落在多个 GGUF RCE CVE 区间（如 CVE-2025-49847），升级需评估。
- 路线图 11/12（输入分诊、国标 schema 生成器）未立项。
- 真实 GPU OCR 质量验证需在用户原生桌面进行（本沙箱 seccomp 拦 CUDA ioctl）。
