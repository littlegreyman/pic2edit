# -*- coding: utf-8 -*-
"""凭证 schema 包 v2 契约的可执行门禁（技术规格 v1.2 §3.1.1 / §3.13.1）。

为什么要有这份测试
------------------
用户提出的「每个凭证类型一个目录，含说明 / 数据规范 / 抽取程序 / 版式模板 /
输入输出例子」五件套，落成了规格里的 MUST。但文档写了不等于工具链真的产出、
也不等于缺件会被拦下——本项目已经吃过「审计列了缺口、写进计划、然后没做」
的亏。所以这里同时钉住三件事：

1. **规格与常量**不漂移（v1.2 章节、`CONTRACT_VERSION` 单一来源）；
2. **工具链真的产 v2 包**：`schemas.cli new` 骨架与 `schema_gen.emit` 产物
   都必须原生满足五件套，且能过 `lint_package` / `pack`；
3. **缺件会变红**：故意删 README / standard / expected、写错 form /
   layout_source、加标准外的野字段，逐项观察门禁报 error 且 `pack` 拒绝。

第 3 条是**负例真跑**，不是读源码 grep——单测全绿最容易骗自己的地方，
就是门禁本身写成了「永远通过」。

渐进纳管（规格 D4）：只有显式声明 `contract_version: 2` 的包才受 v2 硬检查。
内置 `invoice` / `asset_card` 在 P3 补齐文件后才声明；本测试不强制它们现在
就声明，但**一旦声明就必须全过**，避免 P3 留半份包上线。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from schema_gen import emit  # noqa: E402
from schema_gen.ir import IR, IRField, IRSection  # noqa: E402
from schemas import cli, lint, reset_registry  # noqa: E402
from schemas.cli import main as cli_main  # noqa: E402
from schemas.lint import (  # noqa: E402
    CONTRACT_VERSION,
    FORM_VALUES,
    LAYOUT_SOURCES,
    README_SECTIONS,
    _flatten_doc,
    check_samples,
    lint_package,
)
from schemas.registry import BUILTIN_ROOT  # noqa: E402

SPEC = os.path.join(HERE, "docs", "schema-technical-spec-v1.md")

#: `cli new` 骨架应产出的全部文件（v2 五件套 + 原有三件）。
NEW_FILES = {
    "manifest.json", "schema.yaml", "layout.html", "README.md",
    "standard.json", "samples/README.md", "samples/demo.txt",
    "samples/demo.expected.json",
}

#: 生成器产物：仍 MUST NOT 有 layout.html（标准只有数据模型，没有版式）。
GEN_FILES = {
    "manifest.json", "schema.yaml", "README.md", "standard.json",
    "samples/README.md", "samples/skeleton.txt",
    "samples/skeleton.expected.json",
}


@pytest.fixture(autouse=True)
def _fresh():
    reset_registry()
    yield
    reset_registry()


def _new(tmp_path, sid="contract_demo", title="演示单据"):
    root = tmp_path / "schemas"
    assert cli_main(["new", sid, "--title", title, "--dir", str(root)]) == 0
    return root / sid


def _errors(pkg):
    return [i for i in lint_package(str(pkg))["issues"] if i["level"] == "error"]


def _probe_ir() -> IR:
    """最小 IR：单段 kv、单字段，避免依赖官网 XSD 样本。"""
    return IR(
        schema_id="contract_probe",
        title="契约探针",
        version="1.0.0",
        standard="T/PROBE 0001-2026",
        detect_hint="SPEC-PROBE-NOT-ON-REAL-PAGE",
        xsd_member="",
        sections=(
            IRSection(
                key="header", label="一、基本信息", mode="kv",
                fields=(
                    IRField(key="doc_no", label="单据编号", raw_name="doc_no",
                            entity="header", source="xsd"),
                ),
            ),
        ),
        # verify_structure 会把 provenance.field_count 与 xsd_counts.fields 对账，
        # 探针必须自带这个键，否则是夹具不完整而非产品缺陷。
        xsd_counts={"fields": 1},
    )


def _builtin_packages():
    out = []
    for name in sorted(os.listdir(BUILTIN_ROOT)):
        pkg = os.path.join(BUILTIN_ROOT, name)
        if os.path.isdir(pkg) and os.path.isfile(os.path.join(pkg, "manifest.json")):
            out.append(pkg)
    return out


# ---------------------------------------------------------------- 1. 规格与常量
def test_spec_v12_markers_and_contract_sections():
    with open(SPEC, encoding="utf-8") as f:
        text = f.read()
    # 首行仍含 v1.1，保证既有 test_schema_spec 的静态对账不失效。
    assert "v1.1" in text.splitlines()[0] and "v1.2" in text.splitlines()[0]
    for marker in ("3.1.1 包目录 v2 清单", "3.3.1 v2 新增字段",
                   "3.8.1 期望输出样例", "3.13.1 v2 lint 规则",
                   "D4", "D5", "layout_source", "standard.json",
                   "contract_version: 2", "只报告"):
        assert marker in text, f"规格缺 v2 条款: {marker}"


def test_contract_constants_have_single_source():
    assert CONTRACT_VERSION == 2
    assert FORM_VALUES == ("declarative", "plugin")
    assert LAYOUT_SOURCES == ("package", "generic", "none")
    assert len(README_SECTIONS) == 6
    # 生成器不得自己另写一份数字（单一实现入口）。
    mf = emit.build_manifest(_probe_ir())
    assert mf["contract_version"] == CONTRACT_VERSION
    assert mf["form"] == "declarative" and mf["layout_source"] == "none"
    assert "layout" not in mf, "layout_source=none 时 MUST NOT 声明版式文件"
    # cli new 模板也必须声明 v2，且不能复用 kind / layout 表达档位。
    assert '"contract_version": 2' in cli._TEMPLATE_MANIFEST
    assert '"form": "declarative"' in cli._TEMPLATE_MANIFEST
    assert '"layout_source": "package"' in cli._TEMPLATE_MANIFEST


# ---------------------------------------------------------------- 2. 工具链真产 v2 包
def test_cli_new_produces_full_v2_package_and_passes_lint(tmp_path):
    pkg = _new(tmp_path)
    files = {os.path.relpath(os.path.join(r, f), pkg)
             for r, _, fs in os.walk(pkg) for f in fs}
    assert files == NEW_FILES

    mf = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert mf["contract_version"] == CONTRACT_VERSION
    assert mf["form"] == "declarative" and mf["layout_source"] == "package"
    assert mf["standard"] == "standard.json"
    assert mf["kind"] == "user", "kind 仍是来源标签，MUST NOT 被 v2 改写"

    readme = (pkg / "README.md").read_text(encoding="utf-8")
    for sec in README_SECTIONS:
        assert sec in readme, f"骨架 README 缺章节: {sec}"

    standard = json.loads((pkg / "standard.json").read_text(encoding="utf-8"))
    assert set(standard["fields"]) >= {"example_no", "remarks"}
    assert standard["standard"]["code"] and standard["standard"]["license"]

    assert _errors(pkg) == [], "骨架包必须自满足 v2 契约"
    out = tmp_path / "demo.zip"
    assert cli_main(["pack", str(pkg), "--out", str(out)]) == 0
    assert out.is_file()


def test_generator_emits_v2_package_and_passes_lint(tmp_path):
    ir = _probe_ir()
    produced = emit.render_files(ir)
    assert set(produced) == GEN_FILES
    assert "layout.html" not in produced, "生成器 MUST NOT 编造版式"

    pkg = tmp_path / "schemas" / ir.schema_id
    for name, content in produced.items():
        path = pkg / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    mf = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert mf["contract_version"] == CONTRACT_VERSION
    assert mf["layout_source"] == "none"
    readme = (pkg / "README.md").read_text(encoding="utf-8")
    for sec in README_SECTIONS:
        assert sec in readme
    standard = json.loads((pkg / "standard.json").read_text(encoding="utf-8"))
    assert set(standard["fields"]) == {"doc_no"}, "字段字典必须覆盖实现字段"
    assert _errors(pkg) == []


# ---------------------------------------------------------------- 3. 缺件负例（真跑）
@pytest.mark.parametrize("case, expect", [
    ("no_readme", "README.md"),
    ("no_standard", "standard.json"),
    ("no_expected", "expected.json"),
    ("bad_form", "form"),
    ("layout_conflict", "layout_source=none"),
    ("wild_field", "野字段"),
])
def test_missing_or_invalid_v2_parts_turn_lint_red(tmp_path, case, expect):
    """每个负例都新建一份完整骨架、只破坏一处，确认门禁真的会红。"""
    pkg = _new(tmp_path)
    assert _errors(pkg) == [], "负例前置条件：完整骨架必须是绿的"

    if case == "no_readme":
        os.remove(pkg / "README.md")
    elif case == "no_standard":
        os.remove(pkg / "standard.json")
    elif case == "no_expected":
        os.remove(pkg / "samples" / "demo.expected.json")
    elif case == "bad_form":
        p = pkg / "manifest.json"
        p.write_text(p.read_text(encoding="utf-8").replace(
            '"form": "declarative"', '"form": "bogus"'), encoding="utf-8")
    elif case == "layout_conflict":
        p = pkg / "manifest.json"
        p.write_text(p.read_text(encoding="utf-8").replace(
            '"layout_source": "package"', '"layout_source": "none"'),
            encoding="utf-8")
    elif case == "wild_field":
        y = pkg / "schema.yaml"
        y.write_text(y.read_text(encoding="utf-8").replace(
            "        required: true\n",
            "        required: true\n      - key: wild_field\n        label: 野字段\n"),
            encoding="utf-8")

    msgs = [i["where"] + ": " + i["message"] for i in _errors(pkg)]
    assert msgs, f"{case}: 破坏后门禁没有变红"
    assert any(expect in m for m in msgs), f"{case}: 期望报错含 {expect!r}，实得 {msgs}"


def test_pack_refuses_v2_package_missing_standard(tmp_path, capsys):
    pkg = _new(tmp_path)
    os.remove(pkg / "standard.json")
    out = tmp_path / "bad.zip"
    assert cli_main(["pack", str(pkg), "--out", str(out)]) == 1
    assert not out.exists(), "半份包不得流出"
    assert "校验未通过" in capsys.readouterr().out
    # --force 是明确写进规格的逃生口，仍可打包。
    assert cli_main(["pack", str(pkg), "--out", str(out), "--force"]) == 0
    assert out.is_file()


# ---------------------------------------------------------------- 4. golden 只报告不设门禁
def test_golden_report_is_informative_but_never_gates(tmp_path, capsys):
    pkg = _new(tmp_path)
    # 1 匹配 / 1 不符 / 1 缺失 / 1 跳过
    (pkg / "samples" / "demo.expected.json").write_text(json.dumps({
        "fields": {
            "flat.example_no": "SP-001",
            "flat.remarks": "故意写错的期望",
            "flat.not_exist": "X",
            "flat.skipped_one": "Y",
        },
        "skip": ["flat.skipped_one"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    s = check_samples(str(pkg))
    row = s["results"][0]
    g = row["golden"]
    assert g["matched"] == 1
    assert len(g["mismatched"]) == 1 and g["mismatched"][0]["field"] == "flat.remarks"
    assert len(g["missing"]) == 1 and g["missing"][0]["field"] == "flat.not_exist"
    assert g["skipped"] == ["flat.skipped_one"]

    # 差异不能进 lint 的 issues，也不能影响 cli test 的退出码。
    assert _errors(pkg) == [], "golden 差异 MUST NOT 变成结构门禁"
    assert cli_main(["test", str(pkg)]) == 0
    out = capsys.readouterr().out
    assert "只报告，不设门禁" in out and "[不符]" in out and "[缺失]" in out


def test_flatten_doc_handles_plugin_shape_and_flat_alias():
    """插件式 doc 是自由 dict；声明式 doc.flat 要支持裸键别名。"""
    doc = {
        "buyer": {"name": "甲公司", "address": None},
        "items": [{"name": "文具", "amount": "49.94"}],
        "totals": {"total": "49.94"},
    }
    flat = _flatten_doc(doc)
    assert flat["buyer.name"] == "甲公司"
    assert flat["items[0].name"] == "文具"
    assert flat["items[0].amount"] == "49.94"
    assert flat["totals.total"] == "49.94"

    decl = {"type": "x", "flat": {"doc_no": "SP-001"},
            "tables": {"items": [{"name": "文具"}]}}
    assert _flatten_doc(decl)["flat.doc_no"] == "SP-001"
    assert lint._expected_lookup(decl, "doc_no") == (True, "SP-001")
    assert lint._expected_lookup(decl, "absent")[0] is False


# ---------------------------------------------------------------- 5. 渐进纳管
def test_undeclared_builtin_packages_stay_green_with_info():
    """未声明 v2 的存量包按 v1 检查：只给 info，不判红（规格 D4）。"""
    pkgs = _builtin_packages()
    assert pkgs, "内置包目录不应为空"
    for pkg in pkgs:
        mf = json.loads(open(os.path.join(pkg, "manifest.json"), encoding="utf-8").read())
        if mf.get("contract_version") == CONTRACT_VERSION:
            # P3 之后内置包会声明 v2：那时必须全过，不允许半份包上线。
            assert _errors(pkg) == [], f"{pkg} 声明了 v2 却未满足契约"
            continue
        res = lint_package(pkg)
        assert res["ok"], f"{pkg} 未声明 v2 却被判红: {res['issues']}"
        assert any(i["level"] == "info" and "contract_version" in i["message"]
                   for i in res["issues"]), f"{pkg} 缺升级提示"


def test_generator_v2_package_passes_full_verify(tmp_path):
    """生成器完整自检在 v2 产物下仍全绿，且 v2 manifest 字段不影响装载。

    这条替代一次性临时脚本：把「注册表真实装载 + lint 0 error + 三套 render
    不崩 + 两次生成逐字节一致 + auto 不误命中」全部纳入可回归套件。
    """
    from schema_gen.verify import verify

    ir = _probe_ir()
    pkg = tmp_path / "schemas" / ir.schema_id
    for name, content in emit.render_files(ir).items():
        path = pkg / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    report = verify(str(pkg), ir)
    failed = [c["name"] for c in report["structure"]["checks"] if not c["ok"]]
    assert not failed, f"结构自检失败: {failed}"
    assert report["no_false_positive"]["ok"], "v2 包不得在 auto 模式误命中"
    assert report["ok"] is True
    # 效果层仍必须诚实标注「未验证」，不能因为新增了五件套就自称通过。
    assert report["effects"]["gated"] is False
    assert report["effects"]["locator_pending"] == ir.field_count
