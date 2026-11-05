"""skill 命令行入口的行为测试：目录输入、多输入输出语义、list 的来源列。

背景（2026-11-06 审查）：用户 11-05 定义双工作流时明确写了输入包含「目录」，
并写明"Skill 需补"，但位置参数一直是 ``nargs="?"`` + ``isfile`` 检查——传目录
直接 exit(2)。批量场景只能由 Agent 层循环调用 N 次。

这里只测不触发 OCR 的部分（输入展开、参数校验、列表输出），真实识别链路由
test_skill_render / test_skill_cache 覆盖。
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.join(_HERE, "..", ".workbuddy", "skills", "pic2edit-ocr", "scripts",
                 "pic2edit_convert.py"),
    os.path.expanduser("~/.workbuddy/skills/pic2edit-ocr/scripts/pic2edit_convert.py"),
]


def _load():
    script = next((os.path.abspath(p) for p in _CANDIDATES if os.path.isfile(p)), None)
    if script is None:
        pytest.skip("未找到 pic2edit skill 脚本")
    os.environ["PIC2EDIT_OCR_APP"] = _HERE
    spec = importlib.util.spec_from_file_location("_p2e_skill_cli_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except (ImportError, SystemExit) as e:
        # 只跳过环境类失败；代码级异常必须冒泡（否则整套用例静默变 skip）。
        sys.modules.pop(spec.name, None)
        pytest.skip(f"skill 脚本导入失败（环境问题），跳过：{e!r}")
    return mod


@pytest.fixture(scope="module")
def skill():
    return _load()


# ---------------------------------------------------------------- 目录输入
def test_expand_inputs_walks_directory(skill, tmp_path):
    root = tmp_path / "batch"
    (root / "sub").mkdir(parents=True)
    (root / "b.png").write_bytes(b"x")
    (root / "a.pdf").write_bytes(b"x")
    (root / "sub" / "c.tif").write_bytes(b"x")
    (root / "notes.txt").write_bytes(b"x")          # 不支持的扩展名应被忽略

    files, missing = skill._expand_inputs([str(root)])
    names = [os.path.basename(f) for f in files]
    assert names == ["a.pdf", "b.png", "c.tif"], f"应按稳定顺序递归收集，实得 {names}"
    assert missing == []


def test_expand_inputs_accepts_mixed_file_and_dir(skill, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "x.png").write_bytes(b"x")
    one = tmp_path / "one.jpg"
    one.write_bytes(b"x")

    files, _ = skill._expand_inputs([str(one), str(d)])
    assert len(files) == 2
    assert str(one) in files


def test_expand_inputs_reports_missing(skill, tmp_path):
    files, missing = skill._expand_inputs([str(tmp_path / "nope.png")])
    assert files == [] and len(missing) == 1


# ---------------------------------------------------------------- 输出语义
def test_multi_input_requires_out_directory(skill, tmp_path, capsys):
    """多输入时 --out 必须是目录，否则 N 个文件会争抢同一个输出名（静默丢失）。"""
    d = tmp_path / "in"
    d.mkdir()
    for n in ("a.png", "b.png"):
        (d / n).write_bytes(b"x")

    code = skill.main([str(d), "--out", str(tmp_path / "out.md")])
    assert code == 2
    assert "必须是已存在的目录" in capsys.readouterr().err


def test_no_input_files_returns_2(skill, tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    code = skill.main([str(empty)])
    assert code == 2
    assert "没有可转换的文件" in capsys.readouterr().err


def test_unknown_schema_aborts_before_any_ocr(skill, tmp_path, capsys):
    """写错 --schema 不该等到 OCR 完才报错，也不该只跳过第一个文件。"""
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    code = skill.main([str(f), "--schema", "no_such_schema"])
    assert code == 2
    assert "no_such_schema" in capsys.readouterr().err


# ---------------------------------------------------------------- list-schemas
def test_list_schemas_prints_origin(skill, tmp_path, monkeypatch, capsys):
    """来源列必须是实际加载根目录，否则"拷出来改"的包无法确认生效。"""
    import shutil

    from schemas import reset_registry
    from schemas.registry import BUILTIN_ROOT

    shutil.copytree(os.path.join(BUILTIN_ROOT, "asset_card"),
                    str(tmp_path / "asset_card"))
    monkeypatch.setenv("PIC2EDIT_SCHEMA_DIR", str(tmp_path))
    reset_registry()
    try:
        assert skill.main(["--list-schemas"]) == 0
        out = capsys.readouterr().out
        line = next(ln for ln in out.splitlines() if ln.startswith("asset_card"))
        assert "user" in line, f"来源应显示实际加载根目录，实得: {line}"
    finally:
        reset_registry()


# ---------------------------------------------------------------- 参数面
def test_mode_choices_cover_html_table(skill):
    """--mode 的取值必须来自门面，避免再漏掉 html_table 这类新格式。"""
    from backend_api import list_modes

    assert "html_table" in list_modes()
