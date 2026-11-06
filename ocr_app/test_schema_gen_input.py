# -*- coding: utf-8 -*-
"""生成器输入层（sources / readers）的单测——**全部不联网**。

为什么坚持不联网：官网波动不该让 CI 假红；但也不能因此只测「函数能调用」。
这里用内置最小 XSD 夹具 + 假 HTTP 响应把**真实链路**跑一遍：下载 → sha256 校验 →
失败不重试、保留证据 → 缓存复用 → GBK 文件名还原 → 大小写不敏感读成员 →
XSD 计数自洽 → 字段属性与候选 regex。官网真下载由人工验收执行，见
``docs/standard-sources-2026-11-06.md``。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schema_gen import readers, sources  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 工具
def _seed(monkeypatch, tmp_path, payload, *, key="probe", filename="probe.zip"):
    """注入一个测试用 Source，并把已校验原件放进缓存目录。"""
    digest = hashlib.sha256(payload).hexdigest()
    src = sources.Source(
        key=key, label="测试原件", filename=filename,
        url=f"https://{sources.OFFICIAL_HOST}/xxfb/tzgg/202610/{filename}",
        sha256=digest, kind="zip" if filename.endswith(".zip") else "pdf",
        standard="TEST", notice_attachment="附件 X")
    monkeypatch.setitem(sources.SOURCES, key, src)
    dest = tmp_path / f"{key}-{digest[:12]}-{filename}"
    dest.write_bytes(payload)
    return src, dest


def _gbk_name(text):
    """模拟 zipfile 对无 UTF-8 flag 的 GBK 名字按 cp437 解码后的乱码。"""
    return text.encode("gbk").decode("cp437")


# ---------------------------------------------------------------- sources
def test_official_host_guard():
    sources._assert_official("https://www.cicpa.org.cn/a.zip")
    for bad in ("http://www.cicpa.org.cn/a.zip", "https://evil.example/a.zip",
                "https://www.cicpa.org.cn.evil.example/a.zip"):
        with pytest.raises(sources.SourceError, match="非官方来源"):
            sources._assert_official(bad)
    with pytest.raises(sources.SourceError, match="未知原件"):
        sources.get_source("__nope__")


def test_fetch_uses_verified_cache_without_network(monkeypatch, tmp_path):
    _src, dest = _seed(monkeypatch, tmp_path, b"cached original")

    def _must_not_run(*a, **kw):
        raise AssertionError("命中已校验缓存时不应发起网络请求")

    monkeypatch.setattr(sources.urllib.request, "urlopen", _must_not_run)
    got = sources.fetch("probe", cache_dir=str(tmp_path), offline=True)
    assert got == str(dest)


def test_fetch_downloads_and_verifies_sha256(monkeypatch, tmp_path):
    payload = b"official payload"
    digest = hashlib.sha256(payload).hexdigest()
    src = sources.Source(
        key="dl", label="下载用例", filename="dl.zip",
        url=f"https://{sources.OFFICIAL_HOST}/xxfb/tzgg/202610/dl.zip",
        sha256=digest, kind="zip", standard="TEST", notice_attachment="附件 X")
    monkeypatch.setitem(sources.SOURCES, "dl", src)
    calls = []

    def _fake(req, timeout=None):
        calls.append((req.full_url, timeout))
        return io.BytesIO(payload)

    monkeypatch.setattr(sources.urllib.request, "urlopen", _fake)
    got = sources.fetch("dl", cache_dir=str(tmp_path))
    assert sources.sha256_of(got) == digest
    assert len(calls) == 1
    assert not list(tmp_path.glob("*.part")), ".part 已 rename 成正式文件"


def test_fetch_hash_mismatch_does_not_retry_and_keeps_evidence(monkeypatch, tmp_path):
    """校验失败 = 可疑篡改，换镜像重试等于把篡改当网络抖动。"""
    src = sources.Source(
        key="bad", label="被改过的原件", filename="bad.zip",
        url=f"https://{sources.OFFICIAL_HOST}/xxfb/tzgg/202610/bad.zip",
        sha256="0" * 64, kind="zip", standard="TEST", notice_attachment="附件 X")
    monkeypatch.setitem(sources.SOURCES, "bad", src)
    calls = []

    def _fake(req, timeout=None):
        calls.append(req.full_url)
        return io.BytesIO(b"tampered")

    monkeypatch.setattr(sources.urllib.request, "urlopen", _fake)
    with pytest.raises(sources.SourceError, match="不重试"):
        sources.fetch("bad", cache_dir=str(tmp_path))
    assert len(calls) == 1, "不得重试"
    bads = list(tmp_path.glob("bad-*-bad.zip.bad-*"))
    assert len(bads) == 1, "可疑文件必须保留供人工复核"
    assert not list(tmp_path.glob("bad-*-bad.zip")), "不得覆盖成正式缓存"


def test_offline_miss_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sources.SOURCES, "miss", sources.Source(
        key="miss", label="离线未命中", filename="miss.zip",
        url=f"https://{sources.OFFICIAL_HOST}/x.zip",
        sha256="1" * 64, kind="zip", standard="TEST", notice_attachment="附件 X"))
    with pytest.raises(sources.SourceError, match="缓存未命中"):
        sources.fetch("miss", cache_dir=str(tmp_path), offline=True)


def test_corrupt_cache_is_rejected_not_silently_redownloaded(monkeypatch, tmp_path):
    _src, dest = _seed(monkeypatch, tmp_path, b"good")

    def _must_not_run(*a, **kw):
        raise AssertionError("缓存 hash 不符时不得偷偷重下")

    monkeypatch.setattr(sources.urllib.request, "urlopen", _must_not_run)
    dest.write_bytes(b"corrupted")
    with pytest.raises(sources.SourceError, match="缓存文件 hash 不符"):
        sources.fetch("probe", cache_dir=str(tmp_path))


def test_decoded_name_restores_gbk_and_respects_utf8_flag():
    text = "附件5：文件输出格式/PPE.xsd"
    garbled = _gbk_name(text)
    assert garbled != text
    info = zipfile.ZipInfo(garbled)
    info.flag_bits = 0
    assert sources.decoded_name(info) == text

    utf8 = zipfile.ZipInfo("中文/PPE.xsd")
    utf8.flag_bits |= 0x800
    assert sources.decoded_name(utf8) == "中文/PPE.xsd"


def test_list_members_and_read_member_are_case_insensitive(monkeypatch, tmp_path):
    # 夹具用 ASCII 名：zipfile 遇到非 ASCII 名会自动打 UTF-8 flag 并按 UTF-8 写，
    # 造不出官网那种「无 flag + 原始 GBK 字节」；GBK 还原另由
    # test_decoded_name_restores_gbk_and_respects_utf8_flag 单独钉住。
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Attachment6/PPE.XML", b"<PPE/>")
        z.writestr("Attachment6/PAYROLL.xml", b"<PAY/>")
    _seed(monkeypatch, tmp_path, buf.getvalue(), key="ex", filename="ex.zip")

    names = [n for n, _ in sources.list_members(
        "ex", cache_dir=str(tmp_path), offline=True)]
    assert any(n.endswith("PPE.XML") for n in names)
    assert sources.read_member("ex", "ppe.xml", cache_dir=str(tmp_path),
                               offline=True) == b"<PPE/>"
    with pytest.raises(sources.SourceError, match="找不到成员"):
        sources.read_member("ex", "missing.xsd", cache_dir=str(tmp_path), offline=True)


def test_unsafe_zip_members_are_refused():
    for name in ("../evil.xsd", "/abs/evil.xsd", "C:/evil.xsd", "a/../../evil.xsd"):
        info = zipfile.ZipInfo(name)
        with pytest.raises(sources.SourceError, match="越界"):
            sources._assert_safe_member(info, name)
    link = zipfile.ZipInfo("link.xsd")
    link.external_attr = (0o120777 << 16)
    with pytest.raises(sources.SourceError, match="符号链接"):
        sources._assert_safe_member(link, link.filename)
    sources._assert_safe_member(zipfile.ZipInfo("ok/PPE.xsd"), "ok/PPE.xsd")


def test_provenance_record_is_complete():
    rec = sources.provenance("xsd", "2026-11-06T08:00:00Z")
    assert rec["sha256"] == sources.SOURCES["xsd"].sha256
    assert rec["standard"] == sources.STANDARD_PPE
    assert rec["url"].startswith(f"https://{sources.OFFICIAL_HOST}/")
    assert rec["attachment"] == "附件 7" and rec["inner_attachment"].startswith("附件5")
    assert {"kind", "key", "label", "notice_url", "retrieved_at"} <= set(rec)
    json.dumps(rec, ensure_ascii=False)   # 必须可序列化进 manifest


# ---------------------------------------------------------------- readers 标识符
@pytest.mark.parametrize("name,expected", [
    ("PPE_ID", "ppe_id"),
    ("Tag_Number", "tag_number"),
    ("PPE_Master_Code", "ppe_master_code"),
    ("Type_Code", "type_code"),
    ("2026_Amount", "f_2026_amount"),
    ("", "field"),
])
def test_to_key_keeps_identifier_boundaries(name, expected):
    """防「贪婪正则吞块」：相邻标识符不得被粘成一整个 key。"""
    assert readers.to_key(name) == expected
    assert readers.to_key(name) == readers.to_key(name)      # 跨调用稳定
    assert " " not in readers.to_key(name)


def test_to_key_long_name_is_stable_and_short():
    long = "Very_Long_Fixed_Asset_Depreciation_Allocation_Identifier_Field"
    k = readers.to_key(long)
    assert len(k) <= 32 and k == readers.to_key(long)
    assert readers.to_key(long + "_X") != k


# ---------------------------------------------------------------- readers XSD
_MIN_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:D="urn:demo" targetNamespace="urn:demo">
  <xs:element name="Demo">
    <xs:complexType><xs:sequence>
      <xs:element ref="D:Master" minOccurs="0" maxOccurs="unbounded"/>
    </xs:sequence></xs:complexType>
  </xs:element>
  <xs:element name="Master">
    <xs:complexType><xs:sequence>
      <xs:element name="Type_Code" nillable="false">
        <xs:annotation><xs:documentation>类别编码。例如 0201 表示制造设备。</xs:documentation></xs:annotation>
        <xs:simpleType><xs:restriction base="xs:string">
          <xs:minLength value="0"/><xs:maxLength value="60"/>
        </xs:restriction></xs:simpleType>
      </xs:element>
      <xs:element name="Amount" nillable="true">
        <xs:annotation><xs:documentation>金额。保留两位小数。</xs:documentation></xs:annotation>
        <xs:simpleType><xs:restriction base="xs:decimal">
          <xs:totalDigits value="12"/><xs:fractionDigits value="2"/>
        </xs:restriction></xs:simpleType>
      </xs:element>
    </xs:sequence></xs:complexType>
  </xs:element>
</xs:schema>
"""


def test_read_xsd_counts_are_internally_consistent():
    m = readers.read_xsd(_MIN_XSD.encode(), member="DEMO.xsd")
    assert m.root_name == "Demo"
    assert (m.element_total, m.root_refs, m.field_total) == (5, 1, 2)
    assert m.element_total == 2 + m.root_refs + m.field_total
    assert len(m.entities) == 1 and m.entities[0].repeating is True
    assert len(m.fields) == 2


def test_read_xsd_field_attributes_and_candidate_regex():
    m = readers.read_xsd(_MIN_XSD.encode())
    code, amount = m.fields
    assert (code.key, code.label, code.dtype, code.max_length) == \
        ("type_code", "类别编码", "string", 60)
    assert code.nillable is False
    assert code.shape_regex == r"^.{1,60}$"
    assert (amount.key, amount.label, amount.dtype, amount.digits) == \
        ("amount", "金额", "decimal", (12, 2))
    assert amount.nillable is True
    assert amount.shape_regex == r"^\d{1,10}(?:\.\d{1,2})?$"
    # label 只取首个短句，不把整段定义塞进导出标题
    assert "例如" not in code.label and "0201" not in code.label
    assert code.source.endswith("/Master/Type_Code")


def test_read_xsd_nillable_is_not_required():
    """标准的可空性 ≠ 票面必印；这里只记录事实，required 由 emit 层决定全排除。"""
    import dataclasses

    m = readers.read_xsd(_MIN_XSD.encode())
    assert {f.nillable for f in m.fields} == {False, True}
    assert all("required" not in dataclasses.asdict(f) for f in m.fields)
    assert all("shape_regex" in dataclasses.asdict(f) for f in m.fields), \
        "值格式约束只作候选留在 IR，emit 时 MUST NOT 写进 schema.yaml"


def test_read_xsd_deduplicates_keys_across_entities():
    xsd = """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:D="urn:d">
      <xs:element name="Root"><xs:complexType><xs:sequence>
        <xs:element ref="D:A" maxOccurs="unbounded"/><xs:element ref="D:B" maxOccurs="unbounded"/>
      </xs:sequence></xs:complexType></xs:element>
      <xs:element name="A"><xs:complexType><xs:sequence>
        <xs:element name="ID" nillable="false"/>
      </xs:sequence></xs:complexType></xs:element>
      <xs:element name="B"><xs:complexType><xs:sequence>
        <xs:element name="ID" nillable="true"/>
      </xs:sequence></xs:complexType></xs:element>
    </xs:schema>"""
    m = readers.read_xsd(xsd.encode(), member="DUP.xsd")
    assert m.field_total == 2
    keys = [f.key for f in m.fields]
    assert keys[0] != keys[1], "lint 的 fields 是全局字典，重复 key 会让包非法"
    assert len(set(keys)) == 2


def test_read_xsd_rejects_inconsistent_element_count():
    xsd = """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
      <xs:element name="A"><xs:complexType><xs:sequence>
        <xs:element name="Outer">
          <xs:complexType><xs:sequence><xs:element name="Inner"/></xs:sequence></xs:complexType>
        </xs:element>
      </xs:sequence></xs:complexType></xs:element>
    </xs:schema>"""
    with pytest.raises(readers.ReaderError, match="计数不自洽"):
        readers.read_xsd(xsd.encode(), member="BAD.xsd")
    with pytest.raises(readers.ReaderError, match="可解析"):
        readers.read_xsd(b"not xml at all")


# ---------------------------------------------------------------- readers XML / PDF
def test_read_xml_example_collects_entities_and_fields():
    xml = b"""<PPE>
      <PPE_Master><PPE_ID>A001</PPE_ID><PPE_Code>C1</PPE_Code></PPE_Master>
      <PPE_Master><PPE_ID>A002</PPE_ID></PPE_Master>
      <PPE_Type><Type_Code>0201</Type_Code></PPE_Type>
    </PPE>"""
    ex = readers.read_xml_example(xml)
    assert ex.root_name == "PPE"
    assert ex.entities["PPE_Master"] == ("PPE_ID", "PPE_Code")
    assert ex.record_counts["PPE_Master"] == 2
    assert ex.entities["PPE_Type"] == ("Type_Code",)


def test_find_pdf_identifiers_are_only_candidates():
    text = "数据元 PPE_Master_Code 的说明；另一个 PPE_ID 字段；编号 20.16。"
    found = readers.find_pdf_identifiers(text)
    assert any(i == "PPE_Master_Code" for i, _ in found)
    assert all(ctx for _, ctx in found)       # 每个候选都带上下文，便于人工复核
