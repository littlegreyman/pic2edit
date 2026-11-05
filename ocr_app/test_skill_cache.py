"""skill ``--cache`` 路径的回归测试。

背景（2026-11-06 全量审查发现）：缓存写盘时 `schema` 被剔除（这是对的，因为它是
派生数据），但**命中分支只补做了重复护栏、没有补 `apply_schemas`**。于是
``page["schema"]`` 恒为 None，一律走"无 schema"渲染：

- 同一份发票第一次转出票面版式，第二次（``--cache``）转出 OCR 原文；
- 导出的 JSON 里 ``schema.hits`` 还是缓存里的旧值 —— 元数据在说谎。

当时 170 项测试全绿，因为 ``grep cache test_*.py`` 零命中：缓存路径没有任何覆盖。
本文件补上这一层，并把「两遍输出必须逐字节相同」钉成断言。
"""
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.join(_HERE, "..", ".workbuddy", "skills", "pic2edit-ocr", "scripts",
                 "pic2edit_convert.py"),
    os.path.expanduser("~/.workbuddy/skills/pic2edit-ocr/scripts/pic2edit_convert.py"),
]


def _load_skill():
    script = None
    for p in _CANDIDATES:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            script = p
            break
    if script is None:
        pytest.skip("未找到 pic2edit skill 脚本，跳过缓存测试")

    os.environ["PIC2EDIT_OCR_APP"] = _HERE
    import importlib.util

    spec = importlib.util.spec_from_file_location("_p2e_skill_cache_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except (ImportError, SystemExit) as e:
        # 只对「缺依赖 / 外部守卫退出」跳过：这类是环境问题。
        # 其余异常（SyntaxError / NameError / AttributeError…）必须**抛出去**——
        # 之前这里 catch Exception 一律 skip，于是 12 项缓存测试在代码真坏掉时
        # 也只是显示 skip，全套件照样"全绿"，与这批测试要防的静默失效同源。
        sys.modules.pop(spec.name, None)
        pytest.skip(f"skill 脚本导入失败（环境问题），跳过：{e!r}")
    return mod


@pytest.fixture()
def skill(tmp_path, monkeypatch):
    mod = _load_skill()
    # 把缓存目录挪进 tmp_path，避免污染真实 /tmp 与其它测试
    monkeypatch.setattr(mod, "_cache_root", lambda: str(tmp_path / "cache"))
    # 固定路由决策：沙箱的内存软限制会让 skip 位翻转，使缓存键不可预期
    monkeypatch.setattr(mod, "env_force_skip", lambda: False)
    monkeypatch.setattr(mod, "vmem_limited_gb", lambda: 0)
    return mod


def _asset_card_text():
    return open(os.path.join(_HERE, "schemas", "asset_card", "samples", "basic.txt"),
                encoding="utf-8").read()


def _fake_input(tmp_path):
    """只要文件存在即可：缓存命中分支不会读它的内容。"""
    src = tmp_path / "in.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return str(src)


def _write_cache(skill, src, text, page_extra=None):
    """按真实写缓存规则造一份缓存：已剔除派生键。"""
    page = {"page": 1, "text": text, "raw_text": text, "engine": "disc",
            "lines": [], "img_w": 0, "img_h": 0}
    page.update(page_extra or {})
    data = {"source": src, "vlm_fallback_invoked": False,
            "schema": {"mode": "auto", "hits": 0, "pages": 1,
                       "applied": {}, "errors": []},
            "pages": [page]}
    key = skill._cache_key_for(src, "markdown", None, False, None)
    path = skill._cache_file(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# 核心回归：缓存命中必须重新抽取 schema
# ---------------------------------------------------------------------------
def test_cache_hit_reapplies_schema(skill, tmp_path):
    src = _fake_input(tmp_path)
    _write_cache(skill, src, _asset_card_text())

    md = tmp_path / "a.md"
    skill.convert(src, "md", "markdown", "http://127.0.0.1:9", None, str(md),
                  False, True, False, None, "auto")
    text = md.read_text(encoding="utf-8")

    # 命中 asset_card 的 md 是"### 一、基本信息 + 字段列表"，
    # 而原始样张是一行两列的裸文本 —— 这两者绝不会混淆。
    assert "### 一、基本信息" in text, "缓存命中后必须按 schema 渲染，不能退回 OCR 原文"
    assert "- **卡片编号**：AC-2026-0001" in text


def test_cache_hit_matches_fresh_render(skill, tmp_path):
    """同一份输入，走缓存与不走缓存必须产出**完全一致**的内容。"""
    from schemas import apply_schemas

    src = _fake_input(tmp_path)
    _write_cache(skill, src, _asset_card_text())

    cached_out = tmp_path / "cached.md"
    skill.convert(src, "md", "markdown", "http://127.0.0.1:9", None, str(cached_out),
                  False, True, False, None, "auto")

    # "不走缓存"的那一遍：直接用同一份页数据现抽 schema 再渲染
    fresh_pages = [{"page": 1, "text": _asset_card_text(),
                    "raw_text": _asset_card_text(), "engine": "disc",
                    "lines": [], "img_w": 0, "img_h": 0}]
    apply_schemas(fresh_pages, "auto")
    fresh = {"pages": fresh_pages}
    fresh_out = skill._render(fresh, "md", str(tmp_path / "fresh.md"), src, False, "markdown")

    assert (open(os.path.abspath(str(cached_out)), encoding="utf-8").read()
            == open(os.path.abspath(str(fresh_out)), encoding="utf-8").read())


def test_cache_hit_runs_twice_identically(skill, tmp_path):
    """连跑两遍（都命中缓存）必须逐字节相同 —— 用户能感知的一致性。"""
    src = _fake_input(tmp_path)
    _write_cache(skill, src, _asset_card_text())

    outs = []
    for i in (1, 2):
        p = tmp_path / f"run{i}.md"
        skill.convert(src, "md", "markdown", "http://127.0.0.1:9", None, str(p),
                      False, True, False, None, "auto")
        outs.append(p.read_text(encoding="utf-8"))
    assert outs[0] == outs[1]


def test_cache_hit_does_not_lie_in_json_metadata(skill, tmp_path):
    """JSON 里的 schema 统计必须是本次现算的，不能沿用缓存里的旧值。"""
    src = _fake_input(tmp_path)
    _write_cache(skill, src, _asset_card_text())      # 缓存里 hits=0（旧元数据）

    out = tmp_path / "a.json"
    skill.convert(src, "json", "markdown", "http://127.0.0.1:9", None, str(out),
                  False, True, False, None, "auto")
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["schema"]["hits"] == 1, "元数据必须反映本次抽取结果"
    assert data["schema"]["applied"].get("asset_card") == 1
    assert data["schema"]["versions"].get("asset_card") == "1.0.0"
    assert data["pages"][0]["schema_id"] == "asset_card"
    assert data["pages"][0]["schema_version"] == "1.0.0"


def test_cache_none_mode_writes_no_schema_keys(skill, tmp_path):
    """--schema none 命中缓存时也不得写 schema 键（否则"没启用"无法与"没命中"区分）。"""
    src = _fake_input(tmp_path)
    _write_cache(skill, src, _asset_card_text())

    out = tmp_path / "n.json"
    skill.convert(src, "json", "markdown", "http://127.0.0.1:9", None, str(out),
                  False, True, False, None, "none")
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["schema"]["mode"] == "none"
    assert data["schema"]["hits"] == 0
    assert "note" in data["schema"]
    assert "schema" not in data["pages"][0]
    assert "schema_id" not in data["pages"][0]


# ---------------------------------------------------------------------------
# 写缓存的内容契约
# ---------------------------------------------------------------------------
def test_cache_payload_strips_all_derived_keys(skill):
    page = {"page": 1, "raw_text": "x", "text": "x", "engine": "disc",
            "lines": [], "img_w": 0, "img_h": 0,
            "schema": {"a": 1}, "schema_id": "asset_card", "schema_version": "1.0.0"}
    out = skill._cache_payload({"pages": [page]})

    for k in ("schema", "schema_id", "schema_version"):
        assert k not in out["pages"][0], f"{k} 是派生数据，不能进缓存"
    assert out["pages"][0]["raw_text"] == "x", "识别结果必须保留"


def test_cache_payload_drops_run_scoped_fields(skill):
    """``source`` 与页里的 ``image`` 都是**本次运行**的东西，缓存存它们就是留死链。

    实测踩到过：同一份内容换个目录跑，``--cache`` 的 json 仍报上一次的绝对路径；
    ``image`` 存的是临时 PNG，下次启动即失效，html 因此悄悄退化成纯文本。
    """
    page = {"page": 1, "raw_text": "x", "engine": "disc", "lines": [],
            "img_w": 0, "img_h": 0, "image": "/tmp/pic2edit_abc/001.png"}
    out = skill._cache_payload({"source": "/home/someone/secret/invoice.pdf",
                                "pages": [page]})

    assert "source" not in out, "输入路径不随内容走，缓存不该存"
    assert "image" not in out["pages"][0], "临时底图路径不能进缓存"
    assert out["pages"][0]["raw_text"] == "x"


def test_json_source_is_basename_only(skill, tmp_path):
    """json 的 source 只写文件名：完整路径会带出用户名与目录结构。"""
    src = _fake_input(tmp_path)
    _write_cache(skill, src, _asset_card_text())

    out = tmp_path / "a.json"
    skill.convert(src, "json", "markdown", "http://127.0.0.1:9", None, str(out),
                  False, True, False, None, "auto")
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["source"] == "in.png"
    assert str(tmp_path) not in json.dumps(data), "产物里不能出现输入所在的真实目录"


# ---------------------------------------------------------------------------
# 缓存命中不得降低 html 的保真度（2026-11-06 实测发现）
# ---------------------------------------------------------------------------
def test_cache_hit_html_rebuilds_faithful_layout(skill, tmp_path, monkeypatch):
    """先 md 建缓存（只有文本）再导出 html，必须补算坐标而不是退化成纯文本。

    坐标只在需要版面时才计算，所以「md 建缓存 → html」天然拿不到 ``lines``。
    此前该路径直接掉到"识别文本（本页无坐标数据）"，日志却仍写着"按原图版面还原"。
    """
    from PIL import Image

    src = str(tmp_path / "in.png")
    Image.new("RGB", (200, 120), "white").save(src)
    _write_cache(skill, src, "Total: 880.00")       # lines=[]，模拟 md 建的缓存

    monkeypatch.setattr(skill, "_rapidocr_lines",
                        lambda p: ([{"text": "Total: 880.00", "x0": 10, "x1": 120,
                                     "y0": 10, "y1": 30}], 200, 120))
    out = tmp_path / "a.html"
    skill.convert(src, "html", "markdown", "http://127.0.0.1:9", None, str(out),
                  False, True, False, None, "auto")
    html = out.read_text(encoding="utf-8")

    assert "fx-stage" in html, "缓存命中也要有原图垫底的文字层"
    assert "本页无坐标数据" not in html
    assert "data:image/png;base64" in html


def test_render_html_log_reports_actual_tier(skill, tmp_path, capsys):
    """html 的渲染日志必须报告真实档位，不能一律写"按原图版面还原"。"""
    page = {"page": 1, "text": "abc", "raw_text": "abc", "engine": "vlm",
            "lines": [], "img_w": 0, "img_h": 0}
    skill._render({"pages": [page]}, "html", str(tmp_path / "a.html"), "x.png", False, "markdown")
    assert "纯文本×1" in capsys.readouterr().err

    page2 = dict(page, image=str(tmp_path / "p.png"), lines=[{"text": "abc"}],
                 img_w=100, img_h=50)
    from PIL import Image

    Image.new("RGB", (100, 50), "white").save(page2["image"])
    skill._render({"pages": [page2]}, "html", str(tmp_path / "b.html"), "x.png", False, "markdown")
    assert "原图垫底×1" in capsys.readouterr().err


def test_cache_key_includes_recognition_params(skill, tmp_path):
    """换模型 / 改跳过标志必须换键，否则会读到上一次的 OCR 结果。"""
    src = _fake_input(tmp_path)
    base = skill._cache_key_for(src, "markdown", None, False, None)

    assert skill._cache_key_for(src, "markdown", "q8", False, None) != base
    assert skill._cache_key_for(src, "markdown", None, True, None) != base
    # 掩膜矩形也参与键（同图不同掩膜 = 不同识别输入）
    assert skill._cache_key_for(src, "markdown", None, False, (0.1, 0.2, 0.3, 0.4)) != base
    # 相同入参必须稳定（否则永远命中不了）
    assert skill._cache_key_for(src, "markdown", None, False, None) == base


def test_cache_dir_is_user_private():
    """缓存目录必须按用户隔离且 0700：共享 /tmp 名可被本机其他用户预置伪造结果。

    刻意不用 ``skill`` fixture —— 它把 ``_cache_root`` 换成了 tmp_path，
    这条测试要验的正是**真实**实现。
    """
    mod = _load_skill()
    d = mod._cache_root()
    assert os.path.basename(os.path.realpath(d)).startswith("pic2edit_cache_"), \
        "目录名必须带用户标识，否则多用户机器可互相预置伪造结果"

    p = mod._cache_file("tmp-probe-key")      # 触发目录创建（只建目录，不建文件）
    if hasattr(os, "getuid"):
        assert (os.stat(os.path.dirname(p)).st_mode & 0o777) == 0o700
