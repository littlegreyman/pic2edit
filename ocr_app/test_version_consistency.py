"""发布版本位与说明文档一致性门禁。

起因（2026-11-06）：``alpha-0.20`` tag 已推送，但 tag 快照里的 ``README.md``
仍写 ``alpha 0.19`` /「四种输出格式」，``VERSION`` 未升版，安装说明、
``about.py``、``pic2edit.iss`` 全部停在 0.19 ——「改了一处、忘了其余」的典型漂移。
本模块把五处版本位钉成同一个值，并顺带钉住 CI 的系统依赖修复与 README
目录树的真实性（防止写出不存在的路径）。

负例验证方式（人工真跑，不靠测试自证）：临时把任一版本位改回 ``0.19``，
或删掉 ci.yml 里的 ``libegl1``，本模块必须变红。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent  # ocr_app/
ROOT = HERE.parent

RELEASE = "0.20"
RELEASE_DATE = "2026-11-06"


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _tracked_names() -> set[str]:
    """git ls-files 的文件名集合；无 git 时退回文件系统扫描。"""
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        )
        return {p.rsplit("/", 1)[-1] for p in out.splitlines() if p}
    except (OSError, subprocess.CalledProcessError):
        return {
            p.name
            for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts
        }


def test_version_file_declares_current_release():
    lines = _text("VERSION").splitlines()
    assert lines[0] == f"pic2edit {RELEASE} (alpha)", lines[0]
    assert f"alpha {RELEASE} 相对" in _text("VERSION"), "VERSION 缺本次变更条目"


def test_all_version_slots_agree():
    """五处版本位必须指向同一版本；任一处漏改都会在这里显形。"""
    slots = {
        "VERSION": re.search(r"^pic2edit (\d+\.\d+) \(alpha\)", _text("VERSION"), re.M),
        "README.md": re.search(r"当前为 \*\*alpha (\d+\.\d+)\*\*", _text("README.md")),
        "INSTALL.zh-CN.md": re.search(
            r"当前版本：\*\*alpha (\d+\.\d+)\*\*", _text("INSTALL.zh-CN.md")
        ),
        "about.py": re.search(
            r'APP_VERSION = "alpha (\d+\.\d+)"', _text("ocr_app/about.py")
        ),
        "pic2edit.iss": re.search(
            r'#define MyAppVersion "(\d+\.\d+)\.0"', _text("packaging/pic2edit.iss")
        ),
    }
    for name, match in slots.items():
        assert match is not None, f"{name} 的版本位无法解析（格式被改坏了？）"
    values = {name: m.group(1) for name, m in slots.items()}
    assert set(values.values()) == {RELEASE}, f"版本位不一致: {values}"


def test_install_guide_footer_and_about_date_are_current():
    assert f"pic2edit alpha {RELEASE}。" in _text("INSTALL.zh-CN.md")
    assert f'APP_RELEASE_DATE = "{RELEASE_DATE}"' in _text("ocr_app/about.py")
    assert '"0.19.0"' not in _text("packaging/pic2edit.iss")


def _ci_step(ci: str, name: str) -> str:
    """取 CI 某个 step 的正文（到下一个 step 为止），避免注释里的字样造成假通过。"""
    match = re.search(
        r"(?ms)^      - name: " + re.escape(name) + r".*?(?=^      - name:|\Z)", ci
    )
    assert match is not None, f"CI 缺步骤: {name}"
    return match.group(0)


def test_ci_installs_libegl_and_uses_pinned_lock():
    """CI 红灯（run 42164106400）的根因修复不能被误删。"""
    ci = _text(".github/workflows/ci.yml")
    sysdep = _ci_step(ci, "Install system deps")
    deps = _ci_step(ci, "Install Python deps")
    assert "libegl1" in sysdep, "缺 libEGL.so.1 会让 PySide6 在收集期 ImportError"
    assert "requirements.lock" in deps, "CI 必须用钉死的 lock，不用下限式 requirements.txt"
    assert "pip install -r requirements.txt" not in deps


_TREE_FILE_RE = re.compile(
    r"[A-Za-z0-9_.\-]+\.(?:py|md|txt|bat|sh|iss|spec|ini|json|yaml|yml|lock|png|ico)"
)


def _tree_block(rel: str, marker: str) -> str:
    text = _text(rel)
    assert marker in text, f"{rel} 缺「{marker}」章节"
    return text.split(marker, 1)[1].split("```", 2)[1]


@pytest.mark.parametrize(
    "rel,marker",
    [("README.md", "## 📂 目录结构"), ("ocr_app/README.md", "## 目录结构")],
)
def test_readme_tree_lists_only_real_files(rel, marker):
    """防「写了不存在的路径」，与下面的「防漏写」成对。"""
    tracked = _tracked_names()
    names = {
        m.group(0)
        for m in _TREE_FILE_RE.finditer(_tree_block(rel, marker))
        if "*" not in m.group(0)
    }
    assert names, f"{rel} 目录树里没解析出任何文件名（解析规则失效？）"
    missing = sorted(n for n in names if n not in tracked)
    assert not missing, f"{rel} 目录树写了不存在的路径: {missing}"


REQUIRED_TREE_ENTRIES = (
    "backend_api.py",
    "document_controller.py",
    "engine_launcher.py",
    "schemas/",
    "schema_gen/",
    "packaging/",
    ".github/",
    "INSTALL.zh-CN.md",
    "THIRD_PARTY_NOTICES",
    "requirements.lock",
    "test_version_consistency.py",
)


def test_readme_tree_mentions_key_subsystems():
    """防「漏写真实文件」：0.20 的关键子系统必须在首页目录树里可见。"""
    block = _tree_block("README.md", "## 📂 目录结构")
    missing = [x for x in REQUIRED_TREE_ENTRIES if x not in block]
    assert not missing, f"README.md 目录树漏了关键路径: {missing}"


def test_third_party_notices_tracks_requirements():
    """Check List ⑤：合规署名必须与 requirements.txt 逐项对账，不能停在旧依赖。"""
    req = _text("ocr_app/requirements.txt")
    notice = _text("THIRD_PARTY_NOTICES")
    deps = []
    for line in req.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)\s*[><=]", line)
        if match:
            deps.append(match.group(1))
    assert len(deps) >= 10, f"requirements.txt 解析异常: {deps}"
    missing = [d for d in deps if d.lower() not in notice.lower()]
    assert not missing, f"THIRD_PARTY_NOTICES 缺依赖署名: {missing}"
    assert "rapidocr_onnxruntime" not in notice, "合规文件仍写已废弃包名"
    assert ".venv_ocr" not in notice, "合规文件仍写已弃用的子环境"


_CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六"}


def _gui_modes() -> tuple[str, ...]:
    """静态读 config.GUI_MODE_KEYS；不 import config（其导入期有写探测副作用）。"""
    src = _text("ocr_app/config.py")
    match = re.search(r"^GUI_MODE_KEYS\s*=\s*\(([^)]*)\)", src, re.M)
    assert match, "config.py 找不到 GUI_MODE_KEYS 定义"
    return tuple(re.findall(r"[\"']([a-z_]+)[\"']", match.group(1)))


def test_user_docs_match_gui_modes():
    """Check List ⑥：GUI 识别模式数量变化时，四份用户文档必须同步。

    起因（2026-11-13）：P1 把 html_table 从 GUI 移除后，README 的「输出格式」
    章节、ocr_app/README、INSTALL、VERSION 仍写「五种模式」，与同一份 README
    的「四种 GUI 识别模式」自相矛盾，而 Check List ⑥ 误判为已同步。
    本门禁从实现反向钉住文档，杜绝「改了实现、忘了说明」。
    """
    modes = _gui_modes()
    assert modes, "_gui_modes() 解析结果为空（正则失效？）"
    assert "html_table" not in modes, "决策 2：html_table 不得回到 GUI 下拉"
    word = _CN_NUM.get(len(modes))
    assert word, f"GUI 模式数 {len(modes)} 不在映射表内，请扩展 _CN_NUM 并同步文档"
    assert len(modes) == 4, (
        f"GUI 识别模式已从 4 种变为 {len(modes)} 种："
        "先同步 README / INSTALL / VERSION，再更新本断言"
    )
    for rel in ("README.md", "ocr_app/README.md", "INSTALL.zh-CN.md", "VERSION"):
        text = _text(rel)
        assert f"{word}种" in text, f"{rel} 未写明「{word}种」识别模式"
        for stale in ("五种输出模式", "五种模式", "提供五种模式", "五种输出格式"):
            assert stale not in text, f"{rel} 残留旧口径「{stale}」"
        assert "html_table" in text, f"{rel} 未交代 html_table 的 GUI 边界"
        assert not re.search(r"(?<!\d)\d{3}\s*项", text), (
            f"{rel} 写死了测试数量，随用例增减必然过期，请改成「全量回归」"
        )
