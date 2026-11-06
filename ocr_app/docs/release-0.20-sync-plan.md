# alpha-0.20 发布同步计划（说明文档全量对账）

> 状态：**已确认，执行中**（2026-11-06）。
> 起因：`alpha-0.20` tag 与 `master` 已推到 `267d5cc`，但 tag 快照里的
> `README.md` 仍写 `alpha 0.19` /「四种输出格式」/ 旧目录树，`VERSION` 未升版，
> 安装说明、`about.py`、`pic2edit.iss` 全部停在 0.19；远端 CI 在 HEAD 上还是红灯。
>
> **变更（用户 2026-11-06 14:06 指示）**：把 CI 修复并入 **P1**，
> 并把「远端 CI 在 HEAD 上绿」补进部署前 Check List ①。

## 前置事实（只读核对）

- 远程 `master` = `267d5cc`，`alpha-0.20` 是 annotated tag，指向同一 commit；
  仅 `alpha-0.19` 有 GitHub Release，**没有基于 0.20 的 Release**。
- GitHub Actions `run 42164106400`：`test` job failure / `Run tests` 2.0 秒 exit 2；
  日志为 `ImportError: libEGL.so.1`（4 个 GUI 测试收集期中断）。
  根因是 `.github/workflows/ci.yml:31` 只装 `libgl1 libglib2.0-0`，**漏 `libegl1`**；
  本机 `ldd PySide6/QtWidgets.abi3.so` 已实证 NEEDED 含 `libEGL.so.1`。
  `conftest.py` 的 offscreen 只省 X server，省不掉动态链接依赖。
- 本机两次全量 `pytest` 313 项 `EXIT=0`、ruff 全绿；`requirements.lock` 与
  本地 `pip list` 逐项一致。

## P1 · 版本位统一 + 防漂移门禁 + CI 修复

范围（合并后）：

1. `.github/workflows/ci.yml`：系统依赖补 `libegl1`（根治收集期 ImportError）；
   Python 依赖改用钉死的 `requirements.lock` 并输出 `pip freeze` 便于诊断，
   消除「CI 装 `requirements.txt` 未钉版本」的漂移隐患。
2. `VERSION`：首行升 `pic2edit 0.20 (alpha)`，新增 0.20 变更条目
   （审查修复 + 生成器 P1–P3 + 313 项测试）；**不改写已发布的 0.19 记录**，
   只在 0.20 条目里注明「0.19 条目混入了 11-06 的修复，此处归位」。
   同步更新「核心能力」段。
3. `README.md:255` / `INSTALL.zh-CN.md:4,219` / `packaging/pic2edit.iss:16` /
   `ocr_app/about.py:12,15` → 0.20 / `0.20.0` / `2026-11-06`。
4. 新增 `ocr_app/test_version_consistency.py`：
   把上述五处版本位钉成一致；并断言两份 README 目录树里列出的路径
   在 `git ls-files` 中真实存在。

验收：两次独立全量 `pytest -q` 各 `EXIT=0` + `ruff check .` 绿；
**负例真跑**：把任一版本位临时改回 0.19，新增测试必须变红（防止门禁自证）。
收口 = 独立自解释 commit + 工作区干净。

## P2 · 说明文档与合规文件全量同步

- `README.md`：功能特性（判别式优先零幻觉、凭证 schema 三态、忠实版式、
  双前端门面、供应链加固、schema 生成器、目录/多文件输入）、
  「四种」→「五种」输出格式、目录结构按 `git ls-files` 重写、
  「开发与测试」补 `pytest -q` / ruff / `schemas.cli` / `schema_gen` / `--live`；
  修正「不打包、不内置任何第三方二进制」与 PyInstaller 形态自相矛盾的表述。
- `ocr_app/README.md`：目录树（`backends/` 13 / `utils/` 16 / `docs/` 8，
  新增 `backend_api.py`、`document_controller.py`、`schemas/`、`schema_gen/`）
  与功能段；同样的打包形态表述。
- `INSTALL.zh-CN.md`：版本位、简介功能、第 7 节「验证安装」补 pytest / ruff / `--live`。
- `THIRD_PARTY_NOTICES`：按 `requirements.txt` 逐项对账版本下限与许可证；
  删除 `.venv_ocr` / `rapidocr_onnxruntime 1.2.x`，改为主 venv `rapidocr>=3.10`；
  补 `PyYAML` / `rapidocr` / `onnxruntime` 条目。
- 顺带修 push 前检查发现的两处实质缺陷：
  `packaging/pic2edit.spec:30,32,78` 旧包名 `rapidocr_onnxruntime` → `rapidocr`；
  `ocr_app/docs/schema-gen-plan.md:122` 硬编码用户名路径 → 中性表述。

验收：`git grep -nE 'alpha 0\.19|0\.19\.0|四种输出格式|\.venv_ocr|rapidocr_onnxruntime'`
除历史报告外无命中；README 目录树路径全部真实；全量 pytest + ruff 绿。
收口 = 独立 commit + 工作区干净。

## P3 · 发布收口与真跑验收

1. 两次独立全量 `pytest -q` + `ruff check .` 绿。
2. `git push origin master`；按方案 **A** force 重打 `alpha-0.20` 到新 HEAD 并推。
3. **等远端 CI 在新 HEAD 上转绿**（Check List ① 新增项），用 Actions API 核 `conclusion`。
4. 逐项验收：`git ls-remote` 核 master / tag；`git show alpha-0.20:VERSION` 首行
   = `pic2edit 0.20 (alpha)`；`git show alpha-0.20:README.md | sha256sum` 与本地一致；
   `curl raw.githubusercontent.com/.../master/README.md | sha256sum` 与本地一致；
   `git status --short` 为空。
5. GitHub 首页强刷确认 README 与目录树已更新。

> 若沙箱无 push 凭据（已核实 `~/.git-credentials` 不存在、无 token 环境变量、
> remote URL 不含 PAT），则本地完成 commit + 重打 tag，**不擅自写入凭据**；
> push 与 CI 验收给出用户本机命令并如实报告为未完成。

## 明确不做

- 不改 `ocr_app/docs/` 下的历史审查报告 / 技术规格 / 台账（日期与结论是证据快照），
  `schema-gen-plan.md` 仅做第 122 行路径脱敏。
- 不改 `.workbuddy/skills/pic2edit-ocr/SKILL.md`（无版本位）。
- 不改 `schema_gen/__version__ = "1.0.0"`（生成器自身版本，与发布版本两套）。
- **不新建 CHANGELOG.md**：`VERSION` 已是唯一变更记录入口。
- 不碰功能代码逻辑、不改生成包定位策略、不碰仓库外文件。
- `schema_gen/emit.py:155` 的 `shutil.rmtree` 收窄列为建议，本次不动
  （`assert_out_dir()` 已挡住项目根 / `.git` / `schemas/`，风险可控）。

## 风险与回滚

- force 移动已发布的 `alpha-0.20` 是改写引用；已核实无基于该 tag 的 Release、
  CI 不监听 tags，影响可控。
- 三个 Phase 各一个 commit，回滚按 commit 粒度。
- 目录树是静态文本：对账测试能防「写了不存在的路径」，防不了「漏写真实文件」，
  P2 验收用 `git ls-files` 人工逐条核一遍。
