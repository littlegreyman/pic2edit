"""下载链路的完整性防护测试。

背景（2026-11-06 全量审查）：``download_models.py`` 从 GitHub **官方域名 + 三个第三方
反向代理**下载 ``llama-server`` 归档，解压后 ``chmod 0755`` 由 GUI 直接执行，**全程
没有任何 hash 或签名校验**；解压用裸 ``extractall``（zip 无 Zip Slip 防护，tar 在
Python ≤3.11 默认 fully_trusted）。等于把二进制的信任静默交给了代理运营者。

本文件钉住四条不变量：
  1. 未钉 SHA256 的资产**只允许**从 github.com 官方域名下载；
  2. 已钉 SHA256 时必须校验，且**校验失败不重试**（换镜像重试会把"被篡改"当"网络抖动"）；
  3. 归档中任何写到目标目录之外的成员都必须拒绝（zip 与 tar 都要）；
  4. 不在运行时 ``pip install``（改自己的依赖树是供应链面）。
"""
from __future__ import annotations

import io
import os
import sys
import tarfile
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import download_models as dm  # noqa: E402


# ---------------------------------------------------------------- 镜像策略
def test_mirrors_limited_to_official_when_hash_not_pinned():
    assert dm.mirrors_for("unknown-asset.zip") == ["https://github.com"], \
        "没钉 hash 时不得使用第三方反代"


def test_mirrors_allow_all_when_hash_pinned(monkeypatch):
    monkeypatch.setitem(dm.ENGINE_SHA256, "pinned.zip", "0" * 64)
    assert dm.mirrors_for("pinned.zip") == dm.GITHUB_MIRRORS


# ---------------------------------------------------------------- hash 校验
def _fake_stream(body: bytes):
    def _stream(url, dest, label):
        with open(dest, "wb") as f:
            f.write(body)
    return _stream


def test_download_rejects_hash_mismatch(tmp_path, monkeypatch):
    monkeypatch.setitem(dm.ENGINE_SHA256, "e.zip", "a" * 64)
    monkeypatch.setattr(dm, "_stream", _fake_stream(b"tampered"))
    dest = str(tmp_path / "e.zip")
    assert dm.download_file("https://github.com/x/e.zip", dest, "e.zip") is False
    assert not os.path.exists(dest), "被篡改的归档必须删除，不能留着被执行"


def test_download_accepts_matching_hash(tmp_path, monkeypatch):
    body = b"genuine"
    import hashlib
    monkeypatch.setitem(dm.ENGINE_SHA256, "e.zip",
                        hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(dm, "_stream", _fake_stream(body))
    dest = str(tmp_path / "e.zip")
    assert dm.download_file("https://github.com/x/e.zip", dest, "e.zip") is True
    assert open(dest, "rb").read() == body


def test_hash_mismatch_does_not_fall_through_to_next_mirror(tmp_path, monkeypatch):
    """校验失败必须终止：换镜像重试等于把"被篡改"当"网络抖动"。"""
    monkeypatch.setitem(dm.ENGINE_SHA256, "e.zip", "a" * 64)
    seen = []

    def _stream(url, dest, label):
        seen.append(url)
        with open(dest, "wb") as f:
            f.write(b"x")

    monkeypatch.setattr(dm, "_stream", _stream)
    dm.download_file("https://github.com/x/e.zip", str(tmp_path / "e.zip"), "e.zip")
    assert len(seen) == 1, f"只应尝试一次，实际尝试了 {seen}"


# ---------------------------------------------------------------- 解压守卫
def _zip_with(tmp_path, names):
    p = tmp_path / "a.zip"
    with zipfile.ZipFile(p, "w") as z:
        for n in names:
            z.writestr(n, "boom")
    return str(p)


def _tar_with(tmp_path, names):
    p = tmp_path / "a.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        for n in names:
            data = b"boom"
            ti = tarfile.TarInfo(n)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return str(p)


def test_zip_slip_rejected(tmp_path):
    arch = _zip_with(tmp_path, ["../evil.txt"])
    dest = tmp_path / "dest"
    with pytest.raises(RuntimeError, match="Zip Slip"):
        dm.extract_archive(arch, str(dest))
    assert not (tmp_path / "evil.txt").exists(), "不得写到目标目录之外"


def test_zip_symlink_entry_rejected(tmp_path):
    p = tmp_path / "l.zip"
    with zipfile.ZipFile(p, "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        z.writestr(info, "/etc/passwd")
    with pytest.raises(RuntimeError, match="符号链接"):
        dm.extract_archive(str(p), str(tmp_path / "d"))


def test_tar_traversal_rejected(tmp_path):
    arch = _tar_with(tmp_path, ["../evil.txt"])
    with pytest.raises(RuntimeError, match="Zip Slip"):
        dm.extract_archive(arch, str(tmp_path / "dest"))
    assert not (tmp_path / "evil.txt").exists()


def test_tar_hardlink_rejected(tmp_path):
    p = tmp_path / "h.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        ti = tarfile.TarInfo("h")
        ti.type = tarfile.LNKTYPE
        ti.linkname = "/etc/passwd"
        t.addfile(ti)
    with pytest.raises(RuntimeError, match="链接"):
        dm.extract_archive(str(p), str(tmp_path / "d"))


def test_normal_archives_extract(tmp_path):
    arch = _zip_with(tmp_path, ["bin/llama-server", "bin/libfoo.so"])
    dest = tmp_path / "ok"
    dm.extract_archive(arch, str(dest))
    assert (dest / "bin" / "llama-server").is_file()

    tarch = _tar_with(tmp_path, ["bin/llama-server"])
    dest2 = tmp_path / "ok2"
    dm.extract_archive(tarch, str(dest2))
    assert (dest2 / "bin" / "llama-server").is_file()


# ---------------------------------------------------------------- 不做运行时 pip
def test_no_runtime_pip_install():
    """运行时 pip install 会悄悄改用户的依赖树，且是一条供应链面。"""
    src = open(os.path.join(HERE, "download_models.py"), encoding="utf-8").read()
    assert "subprocess.check_call" not in src
    assert '"pip", "install"' not in src
    assert "'pip', 'install'" not in src


# ---------------------------------------------------------------- 可执行文件选择
def test_find_exe_prefers_shallowest(tmp_path, monkeypatch, capsys):
    (tmp_path / "bin" / "old").mkdir(parents=True)
    (tmp_path / "bin" / "llama-server").write_text("x")
    (tmp_path / "bin" / "old" / "llama-server").write_text("y")
    got = dm._find_exe_under(str(tmp_path))
    assert got == os.path.join(str(tmp_path), "bin", "llama-server"), \
        "多候选时应取最浅的，不能按字典序随机取"
    assert "多个 llama-server" in capsys.readouterr().out
