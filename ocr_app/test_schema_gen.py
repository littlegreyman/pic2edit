# -*- coding: utf-8 -*-
"""生成器本体（ir / emit / verify / cli）的契约测试。

守的是规格 §6 那些「写错就会让生成器自己骗自己」的条款：保守 detect、
priority 900、strict false、locator pending、无定位策略、不生成 layout、
table 段带 start、单段 ≤30、IR 不进包、两次生成逐字节一致、x-* 不影响装载、
结构自检与效果自检分层、auto 不误命中、输出路径守卫。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import zipfile

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schema_gen import cli, emit, ir, readers, sources, verify  # noqa: E402
from schemas import reset_registry  # noqa: E402

MIN_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:D="urn:demo">
  <xs:element name="Root"><xs:complexType><xs:sequence>
    <xs:element ref="D:Master" maxOccurs="unbounded"/>
    <xs:element ref="D:Header"/>
  </xs:sequence></xs:complexType></xs:element>
  <xs:element name="Master"><xs:complexType><xs:sequence>
    <xs:element name="Type_Code" nillable="false">
      <xs:annotation><xs:documentation>类别编码。例如 0201。</xs:documentation></xs:annotation>
      <xs:simpleType><xs:restriction base="xs:string"><xs:maxLength value="60"/></xs:restriction></xs:simpleType>
    </xs:element>
    <xs:element name="Amount" nillable="true">
      <xs:annotation><xs:documentation>金额。两位小数。</xs:documentation></xs:annotation>
      <xs:simpleType><xs:restriction base="xs:decimal">
        <xs:totalDigits value="12"/><xs:fractionDigits value="2"/>
      </xs:restriction></xs:simpleType>
    </xs:element>
  </xs:sequence></xs:complexType></xs:element>
  <xs:element name="Header"><xs:complexType><xs:sequence>
    <xs:element name="Doc_No" nillable="false">
      <xs:annotation><xs:documentation>单据编号。</xs:documentation></xs:annotation>
    </xs:element>
  </xs:sequence></xs:complexType></xs:element>
</xs:schema>
"""

HINT = "T/CICPA 0110—2026"


def _model(xsd=MIN_XSD):
    return readers.read_xsd(xsd.encode("utf-8"), member="DEMO.xsd")


def _ir(xsd=MIN_XSD, **kw):
    params = dict(schema_id="gen_demo", title="测试骨架", version="1.0.0",
                  standard=HINT, detect_hint=HINT,
                  sources=[sources.provenance("xsd", "2026-11-06T00:00:00+00:00")],
                  generated_at="2026-11-06T00:00:00+00:00")
    params.update(kw)
    return ir.build_ir(_model(xsd), **params)


@pytest.fixture
def project_root(monkeypatch, tmp_path):
    """把 emit 的项目根指到 tmp，测试完全不污染仓库。"""
    monkeypatch.setattr(emit, "_APP_ROOT", str(tmp_path))
    monkeypatch.setattr(emit, "_BUILTIN_ROOT", str(tmp_path / "schemas"))
    monkeypatch.setattr(cli, "_APP_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_registry()
    yield
    reset_registry()


# ---------------------------------------------------------------- IR
def test_ir_conservative_detect_and_pending_locators():
    gen = _ir()
    spec = emit.build_spec(gen)
    assert spec["strict"] is False
    assert spec["priority"] == 900 == ir.GENERATED_PRIORITY
    assert spec["detect"]["any"] == [HINT]
    assert gen.locator_pending == gen.field_count == 3
    assert gen.coverage == {"model": 3, "locator_resolved": 0, "locator_pending": 3}
    assert all(f.locator == "pending" for f in gen.fields)


def test_ir_generates_no_locator_strategy():
    """不能生成定位策略：没有 regex / anchor / required / shape。"""
    spec = emit.build_spec(_ir())
    text = yaml.safe_dump(spec, allow_unicode=True)
    for forbidden in ("regex", "anchor", "required", "shape_regex", "dtype", "max_length"):
        assert forbidden not in text, f"定位/IR 元信息泄漏进包：{forbidden}"


def test_ir_repeating_entity_is_table_with_conservative_start():
    spec = emit.build_spec(_ir())
    by_key = {s["key"]: s for s in spec["sections"]}
    assert by_key["sec_master"]["mode"] == "table"
    assert by_key["sec_master"]["start"] == HINT     # 票面不会出现 → 运行时返回空段
    assert by_key["sec_master"]["limit"] > 0
    assert by_key["sec_header"]["mode"] == "kv"
    assert "start" not in by_key["sec_header"]
    assert all(c["locator"] == "pending" for c in by_key["sec_master"]["columns"])


def test_ir_splits_large_entity_into_sections_of_at_most_30():
    fields = "".join(
        f'<xs:element name="F{i:02d}"><xs:annotation>'
        f'<xs:documentation>字段{i}。</xs:documentation></xs:annotation></xs:element>'
        for i in range(35))
    xsd = (f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:D="urn:d">'
           f'<xs:element name="Root"><xs:complexType><xs:sequence>'
           f'<xs:element ref="D:Big" maxOccurs="unbounded"/></xs:sequence></xs:complexType></xs:element>'
           f'<xs:element name="Big"><xs:complexType><xs:sequence>{fields}'
           f'</xs:sequence></xs:complexType></xs:element></xs:schema>')
    gen = _ir(xsd)
    spec = emit.build_spec(gen)
    assert len(spec["sections"]) == 2
    assert [len(s["columns"]) for s in spec["sections"]] == [30, 5]
    assert all(len(s["columns"]) <= ir.SECTION_MAX_FIELDS for s in spec["sections"])
    keys = [s["key"] for s in spec["sections"]]
    assert len(set(keys)) == 2 and not any("sec_sec" in k for k in keys)
    assert gen.field_count == 35


# ---------------------------------------------------------------- emit
def test_manifest_provenance_and_x_keys_do_not_affect_loading(project_root):
    gen = _ir()
    manifest = emit.build_manifest(gen)
    prov = manifest["x-provenance"]
    assert prov["generator"].startswith("schema_gen/")
    assert prov["field_count"] == 3 and prov["coverage"]["locator_pending"] == 3
    assert prov["sources"][0]["sha256"] == sources.SOURCES["xsd"].sha256
    assert prov["standard"] == HINT

    res = emit.write_package(gen, str(project_root / "pkg"))
    reg = reset_registry([str(project_root)])
    p = reg.get("gen_demo")
    assert p is not None and p.origin == "user"
    assert p.title == "测试骨架" and p.version == "1.0.0" and p.priority == 900
    assert not os.path.exists(os.path.join(res["pkg"], "layout.html"))
    assert set(res["files"]) == {"manifest.json", "schema.yaml",
                                 "samples/README.md", "samples/skeleton.txt"}


def test_two_generations_are_byte_identical(project_root):
    gen = _ir()          # generated_at 冻结，两次序列化必须完全一致
    a = emit.render_files(gen)
    b = emit.render_files(gen)
    assert a == b
    r1 = emit.write_package(gen, str(project_root / "a"))
    r2 = emit.write_package(gen, str(project_root / "b"))
    for name in r1["files"]:
        h1 = hashlib.sha256(open(os.path.join(r1["pkg"], name), "rb").read()).hexdigest()
        h2 = hashlib.sha256(open(os.path.join(r2["pkg"], name), "rb").read()).hexdigest()
        assert h1 == h2, f"{name} 两次生成不一致"


def test_force_is_required_to_overwrite(project_root):
    gen = _ir()
    out = str(project_root / "pkg")
    emit.write_package(gen, out)
    with pytest.raises(emit.EmitError, match="非空"):
        emit.write_package(gen, out)
    emit.write_package(gen, out, force=True)
    assert os.path.isfile(os.path.join(out, "schema.yaml"))


def test_out_dir_guard_rejects_registry_root_and_escape(project_root):
    app = str(project_root)
    with pytest.raises(emit.EmitError, match="BUILTIN_ROOT"):
        emit.assert_out_dir(os.path.join(app, "schemas", "evil"))
    with pytest.raises(emit.EmitError, match="项目根"):
        emit.assert_out_dir(app)
    with pytest.raises(emit.EmitError, match="项目根"):
        emit.assert_out_dir(os.path.dirname(app))
    with pytest.raises(emit.EmitError, match=".git"):
        emit.assert_out_dir(os.path.join(app, ".git", "pkg"))
    assert emit.assert_out_dir(os.path.join(app, "generated", "ppe_card")).startswith(app)


def test_safe_id_rejects_weird_names():
    for bad in ("A", "1x", "_x", "x/y", "x y", "", "a" * 33):
        with pytest.raises(emit.EmitError, match="id 必须"):
            emit.safe_id(bad)
    assert emit.safe_id("ppe_card-2") == "ppe_card-2"


# ---------------------------------------------------------------- verify
def test_verify_structure_passes_on_generated_package(project_root):
    gen = _ir()
    pkg = emit.write_package(gen, str(project_root / "pkg"))["pkg"]
    rep = verify.verify_structure(pkg, gen)
    assert rep["ok"] is True, [(c["name"], c["detail"]) for c in rep["checks"] if not c["ok"]]
    assert any("逐字节一致" in c["name"] for c in rep["checks"])


def test_verify_no_auto_false_positive(project_root):
    """规格 §6.4 漏洞 A：无关样张在 auto 与强制指定两条路径都不能命中生成包。"""
    gen = _ir()
    pkg = emit.write_package(gen, str(project_root / "pkg"))["pkg"]
    rep = verify.verify_no_auto_false_positive(pkg, gen)
    assert rep["ok"] is True, rep["details"]
    assert any("应为 None" in d for d in rep["details"])


def test_verify_effects_is_report_only(project_root):
    gen = _ir()
    pkg = emit.write_package(gen, str(project_root / "pkg"))["pkg"]
    ef = verify.verify_effects(pkg, gen)
    assert ef["gated"] is False
    assert ef["locator_pending"] == 3 and ef["locator_resolved"] == 0
    assert ef["samples"] >= 1
    assert "只报告" in ef["note"] or "不构成" in ef["note"]


def test_verify_report_never_claims_everything_passed(project_root):
    gen = _ir()
    pkg = emit.write_package(gen, str(project_root / "pkg"))["pkg"]
    rep = verify.verify(pkg, gen)
    text = verify.format_report(rep)
    assert rep["ok"] is True
    assert "结构自检通过" in text and "抽取效果未验证" in text
    assert "locator_pending: 3" in text
    assert "全部通过" not in text, "日志不许把结构通过和效果未验证混成一句"


def test_verify_detects_broken_package(project_root):
    gen = _ir()
    pkg = emit.write_package(gen, str(project_root / "pkg"))["pkg"]
    with open(os.path.join(pkg, "schema.yaml"), "w", encoding="utf-8") as f:
        f.write("id: gen_demo\nsections: []\n")
    rep = verify.verify_structure(pkg, gen)
    assert rep["ok"] is False
    assert any(not c["ok"] for c in rep["checks"])


# ---------------------------------------------------------------- CLI
def test_cli_parser_exposes_all_contract_flags():
    ap = cli.build_parser()
    src = ap.format_help()
    for flag in ("--source", "--member", "--id", "--title", "--version",
                 "--standard", "--detect-hint", "--out", "--cache-dir",
                 "--offline", "--frozen-time", "--force", "--no-verify"):
        assert flag in src, f"生成器 CLI 缺参数 {flag}"


def test_cli_full_chain_writes_package_and_runs_verify(project_root, monkeypatch, capsys):
    """不联网但完整跑 main()：读源 → IR → 落盘 → verify → stdout 只有路径。"""
    fake_original = project_root / "fake-original.zip"
    fake_original.write_bytes(b"placeholder")
    monkeypatch.setattr(sources, "fetch", lambda *a, **kw: str(fake_original))
    monkeypatch.setattr(sources, "read_member",
                        lambda *a, **kw: MIN_XSD.encode("utf-8"))
    out = str(project_root / "out")
    rc = cli.main(["--id", "gen_demo", "--out", out,
                   "--frozen-time", "2026-11-06T00:00:00+00:00"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert captured.out.strip() == out
    assert "[gen]" in captured.err and "结构自检通过" in captured.err
    assert "抽取效果未验证" in captured.err
    manifest = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
    assert manifest["id"] == "gen_demo" and manifest["priority"] == 900
    spec = yaml.safe_load(open(os.path.join(out, "schema.yaml"), encoding="utf-8"))
    assert spec["strict"] is False and spec["detect"]["any"] == [HINT]
    assert not os.path.exists(os.path.join(out, "layout.html"))


def test_cli_reports_usage_errors_without_traceback(project_root, capsys):
    rc = cli.main(["--id", "BAD_ID", "--out", str(project_root / "x")])
    assert rc == 2
    assert "[error]" in capsys.readouterr().err


def test_cli_offline_seed_from_local_zip(project_root, monkeypatch, capsys):
    """--offline + 已校验缓存也能跑通：CI 不依赖官网。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("附件5：文件输出格式/DEMO.xsd", MIN_XSD)
    payload = buf.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    cache = project_root / "cache"
    cache.mkdir()
    (cache / f"xsd-{digest[:12]}-file-output-format.zip").write_bytes(payload)
    monkeypatch.setitem(sources.SOURCES, "xsd", sources.Source(
        key="xsd", label="离线夹具", filename="file-output-format.zip",
        url=f"https://{sources.OFFICIAL_HOST}/x.zip", sha256=digest,
        kind="zip", standard=HINT, notice_attachment="附件 7"))
    out = str(project_root / "offline_pkg")
    rc = cli.main(["--member", "demo.xsd", "--out", out,
                   "--cache-dir", str(cache), "--offline", "--no-verify",
                   "--frozen-time", "2026-11-06T00:00:00+00:00"])
    assert rc == 0, capsys.readouterr().err
    assert os.path.isfile(os.path.join(out, "schema.yaml"))
