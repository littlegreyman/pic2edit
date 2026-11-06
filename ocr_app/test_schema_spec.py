# -*- coding: utf-8 -*-
"""技术规格 v1.0 的可执行契约：`docs/schema-technical-spec-v1.md` 与源码对账。

为什么要有这个文件：规格是给「schema 生成流程」用的输出契约，如果它写的常量、
默认值、权威来源规则与代码悄悄漂移，生成器会按一份不存在的格式产出包——
这正是项目反复吃过的「两份真相源靠人脑同步」的亏。

守的不是某个函数的细节，而是规格里那些**会被生成器直接照抄**的条目：
  · 枚举白名单、id 规则、默认值；
  · manifest 是元信息权威（覆盖 yaml）；origin 不是 kind；
  · layout 只对声明式生效；
  · A 侧参数集合、缓存键组成、派生数据剔除清单；
  · 规格文档本身四个部分齐全（防止被删/被改坏）。
"""
from __future__ import annotations

import ast
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schemas import cli, lint, registry, runtime  # noqa: E402
from schemas import apply_schemas, list_schemas, reset_registry  # noqa: E402
from schemas.base import info_of  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "docs", "schema-technical-spec-v1.md")
SKILL_SCRIPT = os.path.join(
    HERE, "..", ".workbuddy", "skills", "pic2edit-ocr", "scripts", "pic2edit_convert.py")


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_registry()
    yield
    reset_registry()


def _skill_tree():
    with open(SKILL_SCRIPT, encoding="utf-8") as f:
        return ast.parse(f.read())


# ---------------------------------------------------------------- 常量白名单
def test_spec_enum_whitelists_match_runtime():
    assert runtime.SECTION_MODES == ("kv", "table", "text")
    assert runtime.ANCHORS == ("right", "below", "inline", "none")
    # lint 必须复用 runtime 的白名单，不能自己抄一份（规格 §3.13）
    assert tuple(lint._MODES) == runtime.SECTION_MODES
    assert tuple(lint._ANCHORS) == runtime.ANCHORS


@pytest.mark.parametrize("sid,ok", [
    ("a", True), ("asset_card", True), ("a" * 32, True), ("x-1_2", True),
    ("A", False), ("1x", False), ("_x", False), ("x y", False),
    ("x/y", False), ("", False), ("a" * 33, False),
])
def test_spec_id_rule(sid, ok):
    """规格 §3.14：1–32 位、小写字母开头、其余 [a-z0-9_-]。"""
    assert bool(cli._SAFE_ID_RE.fullmatch(sid)) is ok


def test_spec_defaults_are_behavioural():
    """规格 §3.4/§3.6：strict=True、priority=50、anchor=right 是真行为，不只是文档。"""
    p = runtime.DeclarativeProvider("", {}, {
        "id": "x", "sections": [{"key": "h", "mode": "kv", "fields": [
            {"key": "a", "label": "甲", "required": True}]}]})
    assert p.priority == 50
    # 未声明 strict → required 缺失即整页未命中
    assert p.extract("完全无关的一段文字", [], 0, 0) is None
    # 未声明 anchor → 默认 right（右侧同一视觉行的框会被取到）
    q = runtime.DeclarativeProvider("", {}, {
        "id": "y", "strict": False,
        "sections": [{"key": "h", "mode": "kv", "fields": [
            {"key": "v", "label": "甲"}]}]})
    lines = [{"text": "甲", "x0": 10, "y0": 10, "x1": 40, "y1": 30},
             {"text": "取值", "x0": 100, "y0": 10, "x1": 160, "y1": 30}]
    assert q.extract("甲", lines, 400, 200)["flat"]["v"] == "取值"


def test_spec_three_state_constants_and_none_semantics():
    assert registry.MODE_NONE == "none" and registry.MODE_AUTO == "auto"
    page = {"page": 1, "raw_text": "随便", "lines": [], "img_w": 0, "img_h": 0}
    apply_schemas([page], registry.MODE_NONE)
    # 规格 §2 / §3.9：none 连键都不写；启用未命中则写 None
    assert not {"schema", "schema_id", "schema_version"} & set(page)
    apply_schemas([page], registry.MODE_AUTO)
    assert page["schema_id"] is None and "schema_id" in page


# ---------------------------------------------------------------- 权威来源
def test_spec_manifest_is_authoritative_over_yaml(tmp_path):
    """规格 §3.2：元信息以 manifest 为准；origin 来自扫描根目录，不是 kind。"""
    pkg = tmp_path / "spec_demo"
    pkg.mkdir()
    (pkg / "manifest.json").write_text(json.dumps({
        "id": "spec_demo", "title": "来自 manifest", "version": "9.9.9",
        "priority": 7, "schema": "schema.yaml"}, ensure_ascii=False), encoding="utf-8")
    (pkg / "schema.yaml").write_text(
        "id: spec_demo\ntitle: 来自 yaml\nversion: 0.0.1\npriority: 99\n"
        "detect:\n  any: [规格示例]\nsections:\n"
        "  - key: h\n    mode: text\n", encoding="utf-8")
    reset_registry([str(tmp_path)])
    info = next(i for i in list_schemas() if i["id"] == "spec_demo")
    assert info["title"] == "来自 manifest"
    assert info["version"] == "9.9.9"
    assert info["priority"] == 7
    assert info["origin"] == "user"
    assert info["path"] and os.path.isdir(info["path"])


def test_spec_layout_applies_only_to_declarative(tmp_path):
    """规格 §3.3：entry 与 layout 同时声明时，注册表不把 layout 当版式。"""
    pkg = tmp_path / "plug_demo"
    pkg.mkdir()
    (pkg / "manifest.json").write_text(json.dumps({
        "id": "plug_demo", "entry": "provider.py", "layout": "layout.html"}),
        encoding="utf-8")
    (pkg / "layout.html").write_text("<p>{{ x }}</p>", encoding="utf-8")
    (pkg / "provider.py").write_text(
        "class P:\n"
        "    id='plug_demo'; title='x'; version='1'; kind='plugin'; priority=1; layout=None\n"
        "    def detect(self,*a): return False\n"
        "    def extract(self,*a): return None\n"
        "    def render_md(self,d): return ''\n"
        "    def render_txt(self,d): return ''\n"
        "    def render_html(self,d): return ''\n"
        "    def css(self): return ''\n"
        "def get_provider(pkg, manifest=None): return P()\n", encoding="utf-8")
    reg = reset_registry([str(tmp_path)])
    assert not getattr(reg.get("plug_demo"), "layout", None)


def test_spec_info_fields_include_origin_and_path():
    class P:
        id, title, version, kind, origin, priority = "x", "X", "1.0", "builtin", "user", 5
        layout, package_dir = None, "/tmp/pkg"

    info = dict(info_of(P()))
    assert {"id", "title", "version", "kind", "origin", "priority",
            "layout", "path"} == set(info)
    assert info["origin"] == "user" and info["path"] == "/tmp/pkg"


def test_spec_placeholder_regex_is_shared_with_lint():
    """规格 §4.2：占位语法只有一种正则；lint 对账必须用同一条。"""
    assert runtime._PLACEHOLDER_RE.pattern == r"\{\{\s*!?\s*([A-Za-z_][\w.]*)\s*\}\}"
    with open(lint.__file__, encoding="utf-8") as f:
        lint_src = f.read()
    assert runtime._PLACEHOLDER_RE.pattern in lint_src, "lint 抄了一份不同的占位正则"


# ---------------------------------------------------------------- A 侧契约
def test_spec_cli_flags_and_mode_source():
    """规格 §5.3：A 侧参数齐全，--mode 取值必须来自门面而不是本地白名单。"""
    with open(SKILL_SCRIPT, encoding="utf-8") as f:
        src = f.read()
    for flag in ("--schema", "--format", "--mode", "--cache", "--list-schemas",
                 "--vlm-model", "--temperature", "--repeat-penalty",
                 "--repeat-last-n", "--max-tokens"):
        assert f'"{flag}"' in src, f"规格声明的参数缺失: {flag}"
    assert "choices=list_modes()" in src


def test_spec_cache_key_and_drop_keys():
    """规格 §5.4：识别层参数进键；派生/运行期数据不写缓存。"""
    tree = _skill_tree()
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_cache_key_for")
    # 只取 key 赋值表达式里的常量，不把函数 docstring 混进来（docstring 里会提到 schema）
    exprs = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
             and any(getattr(t, "id", "") == "key" for t in n.targets)]
    exprs += [n.value for n in ast.walk(fn) if isinstance(n, ast.AugAssign)
              and getattr(n.target, "id", "") == "key"]
    consts = "".join(c.value for e in exprs for c in ast.walk(e)
                     if isinstance(c, ast.Constant) and isinstance(c.value, str))
    refs = {c.id for e in exprs for c in ast.walk(e) if isinstance(c, ast.Name)}
    assert "|vlm:" in consts and "|skip:" in consts and "|mask:" in consts
    assert {"mode", "CACHE_VERSION", "_sha256_file"} <= refs
    assert "schema" not in refs and "schema" not in consts, \
        "schema 是派生数据，不能进缓存键"
    ver = next(n.value for n in tree.body if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "CACHE_VERSION" for t in n.targets))
    assert ast.literal_eval(ver) == "v3"
    drop = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "_CACHE_DROP_KEYS" for t in n.targets))
    keys = set(ast.literal_eval(drop))
    assert {"schema", "schema_id", "schema_version", "image"} <= keys
    payload = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_cache_payload")
    assert any(isinstance(s, ast.Call) and isinstance(s.func, ast.Attribute)
               and s.func.attr == "pop" for s in ast.walk(payload)), \
        "source 也必须在写缓存前剔除"


def test_spec_html_meta_is_escaped_on_both_sides():
    """规格 §4.6：meta content 必须转义，GUI 与 Skill 两侧都要守。"""
    with open(SKILL_SCRIPT, encoding="utf-8") as f:
        skill = f.read()
    with open(os.path.join(HERE, "utils", "export_formats.py"), encoding="utf-8") as f:
        gui = f.read()
    assert 'content="{_esc_html(stamp)}"' in skill
    assert 'content="{_esc_html(stamp)}"' in gui


def test_spec_json_schema_shape_difference_is_documented():
    """规格 §7 D8：两侧顶层 schema 形状不同，必须如实登记，不能写成一种。

    真跑中文样张时发现：skill 写的是 apply_schemas 的 statistics（版本在
    versions 子键），GUI 写的是纯 {id: version}。规格初稿把它们写成了一个，
    已修正；这条测试防止实现或规格单方面漂移。
    """
    with open(SKILL_SCRIPT, encoding="utf-8") as f:
        skill = f.read()
    with open(os.path.join(HERE, "utils", "export_formats.py"), encoding="utf-8") as f:
        gui = f.read()
    assert '"schema": schema_stats' in skill
    assert '"schema": versions' in gui
    with open(SPEC, encoding="utf-8") as f:
        spec = f.read()
    assert "D8" in spec and "statistics" in spec


# ---------------------------------------------------------------- 文档本体
def test_spec_document_covers_all_four_parts_and_gap_list():
    """规格被删/被改坏时，生成器就失去契约；至少四个部分与差距清单要在。"""
    assert os.path.isfile(SPEC)
    with open(SPEC, encoding="utf-8") as f:
        text = f.read()
    for marker in ("# 第一部分 · 数据格式定义", "# 第二部分 · 版式显示要求",
                   "# 第三部分 · 转换适用参数", "# 第四部分 · 生成流程输出契约",
                   "## 7. 规格与实现的差距清单", "## 2. 调用情形矩阵"):
        assert marker in text, f"规格缺章节: {marker}"


def test_spec_v11_closes_generator_contract_gaps():
    """v1.1 的两处漏洞必须写进规格：保守 detect、两层自检；数字不得硬编码。"""
    with open(SPEC, encoding="utf-8") as f:
        text = f.read()
    assert "v1.1" in text.splitlines()[0]
    # 漏洞 A
    assert "priority: 900" in text
    assert "保守 `detect`" in text
    assert "auto 模式不得误命中" in text
    # 漏洞 B
    assert "结构自检" in text and "抽取效果自检" in text
    assert "locator_pending: N" in text
    assert "hit_rate" in text and "只打印、不判通过" in text
    # 实测口径：361 = 10 顶层 + 9 ref + 342 字段；两者不矛盾，规格必须写清
    assert "361" in text and "297" in text and "342" in text
    assert "字段元素数" in text and "不得硬编码" in text
    # 生成器入口与两条 lint
    assert "python -m schema_gen" in text
    assert "D1·v1.1 补" in text and "D2·v1.1 补" in text


# ---------------------------------------------------------------- D1 / D2 lint
def _lint_fixture(tmp_path, sid, sections_yaml, manifest=None, extra_top=""):
    """构造一个最小合法声明式包，返回 lint_package() 结果。"""
    pkg = tmp_path / sid
    pkg.mkdir()
    mf = {"id": sid, "title": "测试", "version": "1.0.0", "priority": 50,
          "schema": "schema.yaml"}
    if manifest:
        mf.update(manifest)
    (pkg / "manifest.json").write_text(
        json.dumps(mf, ensure_ascii=False), encoding="utf-8")
    (pkg / "schema.yaml").write_text(
        f"id: {sid}\ntitle: 测试\nversion: 1.0.0\n{extra_top}"
        "detect:\n  any: [测试]\nsections:\n" + sections_yaml, encoding="utf-8")
    return lint.lint_package(str(pkg))


_KV_SECTION = "  - key: h\n    mode: text\n    regex: '甲(.+)'\n"
_TABLE_NO_START = ("  - key: items\n    label: 明细\n    mode: table\n"
                   "    columns:\n      - {key: name, label: 名称}\n")
_TABLE_WITH_START = ("  - key: items\n    label: 明细\n    mode: table\n"
                     "    start: 明细\n"
                     "    columns:\n      - {key: name, label: 名称}\n")


def test_spec_d1_table_without_start_warns_but_does_not_block(tmp_path):
    """D1：缺 start 的运行语义是安全的，但此前无任何提示；补 warning 不阻断存量包。"""
    res = _lint_fixture(tmp_path, "d1_demo", _TABLE_NO_START)
    assert res["ok"] is True, "warning MUST NOT 阻断 validate/pack"
    assert any(i["level"] == "warning" and "缺 start" in i["message"]
               for i in res["issues"])
    res2 = _lint_fixture(tmp_path, "d1_ok", _TABLE_WITH_START)
    assert not any("缺 start" in i["message"] for i in res2["issues"])


def test_spec_section6_generator_contract_matches_source():
    """§6 生成器契约：常量、默认值、CLI 参数、唯一入口都要与源码对账。"""
    from schema_gen import cli, emit, ir

    assert ir.GENERATED_PRIORITY == 900
    assert ir.SECTION_MAX_FIELDS == 30
    assert ir.LOCATOR_PENDING == "pending"
    with open(emit.__file__, encoding="utf-8") as f:
        emit_src = f.read()
    assert '"strict": False' in emit_src
    assert '"priority": GENERATED_PRIORITY' in emit_src
    assert '"locator": f.locator' in emit_src
    # 「不产出 layout.html」必须查真实文件集合：emit 的模块说明里本就写着
    # 「不生成 layout.html」这句话，grep 源码文字会把注释误判成产物。
    probe = ir.IR(
        schema_id="spec_probe", title="规格对账探针", version="0",
        standard="", detect_hint="SPEC-PROBE-NOT-ON-REAL-PAGE",
        xsd_member="PPE.xsd",
        sections=(ir.IRSection(
            key="probe", label="探针", mode="kv",
            fields=(ir.IRField(key="k", label="K", raw_name="K",
                               entity="Probe", source="spec"),)),))
    produced = emit.render_files(probe)
    assert "layout.html" not in produced, "生成器 MUST NOT 产出 layout.html"
    assert set(produced) == {"manifest.json", "schema.yaml",
                             "samples/README.md", "samples/skeleton.txt"}
    help_text = cli.build_parser().format_help()
    for flag in ("--source", "--member", "--id", "--out", "--standard",
                 "--detect-hint", "--offline", "--frozen-time", "--no-verify"):
        assert flag in help_text, f"规格 §5.9 声明的生成器参数缺失: {flag}"
    assert os.path.isfile(os.path.join(HERE, "schema_gen", "__main__.py"))
    with open(SPEC, encoding="utf-8") as f:
        text = f.read()
    for marker in ("python -m schema_gen", "priority: 900", "locator_pending: N",
                   "--frozen-time", "结构自检", "抽取效果自检"):
        assert marker in text, f"规格缺 §6 条款: {marker}"


def test_spec_d2_metadata_drift_warns_only_when_declared(tmp_path):
    """D2：只对 yaml 里显式声明且与 manifest 冲突的键告警；不写就是交给 manifest。"""
    res = _lint_fixture(tmp_path, "d2_drift", _KV_SECTION,
                        manifest={"version": "9.9.9", "title": "另一个标题",
                                  "priority": 7},
                        extra_top="priority: 99\n")
    assert res["ok"] is True
    msgs = [i["message"] for i in res["issues"] if i["level"] == "warning"]
    for k in ("version", "title", "priority"):
        assert any(k in m and "manifest" in m for m in msgs), f"未对 {k} 漂移告警"
    # 一致时不报；yaml 未声明 priority 时也不报
    clean = _lint_fixture(tmp_path, "d2_clean", _KV_SECTION)
    assert not any("不一致" in i["message"] for i in clean["issues"])
