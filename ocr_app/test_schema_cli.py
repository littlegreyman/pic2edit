"""schema 制作侧 CLI（``python -m schemas.cli``）的测试。

覆盖三件用户点名要求的收尾能力：
  1. ``new`` 的 id 必须是受约束标识符（它同时是目录名与注册表 key）；
  2. ``pack`` 把做好的包打成可分发 zip，并在打包前先校验、打包后打印 sha256；
  3. ``list`` 的「来源」列必须是实际加载根目录（origin），不是包自报的 kind——
     否则用户改完用户目录里的同名包，无法确认生效的是哪一份。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from schemas import reset_registry  # noqa: E402
from schemas.cli import main  # noqa: E402
from schemas.registry import BUILTIN_ROOT  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    reset_registry()
    yield
    reset_registry()


def _run(argv, capsys=None):
    code = main(argv)
    out = capsys.readouterr() if capsys is not None else None
    return code, out


# ---------------------------------------------------------------- new
@pytest.mark.parametrize("bad", ["../evil", "Upper", "有中文", "a" * 40, "", "a/b"])
def test_new_rejects_unsafe_id(bad, tmp_path):
    code = main(["new", bad, "--dir", str(tmp_path)])
    assert code == 2
    assert not list(tmp_path.iterdir()), "非法 id 不得写出任何文件"


def test_new_normalizes_root_with_dotdot(tmp_path):
    """``--dir`` 里带 ``..`` 时按真实路径落地，不得逃出实际根目录。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    assert main(["new", "ok_id", "--dir", str(outside / ".." / "outside")]) == 0
    assert (outside / "ok_id" / "manifest.json").is_file()
    assert main(["new", "ok_id", "--dir", str(tmp_path / "a" / "b")]) == 0
    assert (tmp_path / "a" / "b" / "ok_id" / "manifest.json").is_file()


def test_new_then_pack_roundtrip(tmp_path):
    root = tmp_path / "schemas"
    assert main(["new", "demo_card", "--title", "演示卡片", "--dir", str(root)]) == 0
    pkg = root / "demo_card"
    assert (pkg / "manifest.json").is_file()
    assert (pkg / "schema.yaml").is_file()
    assert (pkg / "layout.html").is_file()

    zp = tmp_path / "demo.zip"
    assert main(["pack", str(pkg), "--out", str(zp)]) == 0
    assert zp.is_file()

    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
    assert "demo_card/manifest.json" in names
    assert "demo_card/schema.yaml" in names
    # 顶层目录必须是 id，解压到用户目录即可直接生效
    assert all(n.startswith("demo_card/") for n in names)
    assert not any("__pycache__" in n for n in names)


def test_pack_refuses_broken_package(tmp_path, capsys):
    """半份包（manifest 与 schema.yaml 的 id 不一致）不该被打包出去。"""
    root = tmp_path / "schemas"
    assert main(["new", "demo_card", "--dir", str(root)]) == 0
    pkg = root / "demo_card"
    # 改坏：manifest 仍写 demo_card，schema.yaml 改成别的 id
    y = pkg / "schema.yaml"
    y.write_text(y.read_text(encoding="utf-8").replace("id: demo_card", "id: other"),
                 encoding="utf-8")
    out = tmp_path / "bad.zip"
    assert main(["pack", str(pkg), "--out", str(out)]) == 1
    assert not out.exists(), "校验未通过时不得产出 zip"
    assert "校验未通过" in capsys.readouterr().out

    # --force 仍可打包（用于"先出包、后修"的临时场景）
    assert main(["pack", str(pkg), "--out", str(out), "--force"]) == 0
    assert out.is_file()


# ---------------------------------------------------------------- list / origin
def test_list_shows_load_origin_not_declared_kind(tmp_path, monkeypatch, capsys):
    """把内置包拷到用户目录后，来源必须显示 user（否则覆盖机制无法验证）。"""
    shutil.copytree(os.path.join(BUILTIN_ROOT, "asset_card"),
                    str(tmp_path / "asset_card"))
    # 故意不改 manifest.json 里的 kind（仍是 builtin），模拟"拷出来改"的真实情形
    mf = json.loads((tmp_path / "asset_card" / "manifest.json").read_text(encoding="utf-8"))
    assert mf["kind"] == "builtin"

    monkeypatch.setenv("PIC2EDIT_SCHEMA_DIR", str(tmp_path))
    reset_registry()

    assert main(["list"]) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("asset_card"))
    assert "user" in line, f"来源应显示实际加载根目录，实得: {line}"


def test_pack_via_id_not_path(tmp_path, monkeypatch):
    """pack 支持用 id 定位包（含 PIC2EDIT_SCHEMA_DIR 里的包）。

    此前 ``_find_package`` 读错了属性名（pkg_dir vs package_dir），
    环境变量目录里的包永远找不到，只能靠按 id 猜 BUILTIN/USER 路径兜底。
    """
    shutil.copytree(os.path.join(BUILTIN_ROOT, "asset_card"),
                    str(tmp_path / "asset_card"))
    monkeypatch.setenv("PIC2EDIT_SCHEMA_DIR", str(tmp_path))
    reset_registry()

    out = tmp_path / "ac.zip"
    assert main(["pack", "asset_card", "--out", str(out)]) == 0
    assert out.is_file()
