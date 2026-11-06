# -*- coding: utf-8 -*-
"""标准原件的获取、校验与本地缓存（生成器 S1/S2 的输入层）。

安全规则与 ``download_models.py`` 保持一致，因为这些是**进入生产数据的外部输入**：

  * URL 钉在官方域名，每个原件在台账里钉死 sha256；
  * 校验失败 **不重试**——换镜像重试等于把「被篡改」当成「网络抖动」；
    失败时保留 ``.bad-<hash>`` 副本供人工复核，并把原因说清楚；
  * zip 成员**只在内存读取、不落盘**，天然没有 Zip Slip；但仍拒绝 ``..``、
    绝对路径、盘符与符号链接，防止调用方以后把成员写盘时中招；
  * 下载有字节上限，防止异常响应把磁盘写满。

原件不进仓库：缓存落在应用数据目录（与注册表 ``~/.pic2edit/schemas`` 同级），
仓库里只留 URL / 标准号 / sha256 台账（``docs/standard-sources-2026-11-06.md``）。
"""
from __future__ import annotations

import hashlib
import os
import re
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import __version__

#: 唯一允许的官方主机。来源不明的镜像/网盘附件只作线索，不作论据（规格 §6.1）。
OFFICIAL_HOST = "www.cicpa.org.cn"
NOTICE_URL = "https://www.cicpa.org.cn/xxfb/tzgg/202610/t20261010_66113.html"
ASSET_BASE = "https://www.cicpa.org.cn/xxfb/tzgg/202610/W020261010587232"
STANDARD_PPE = "T/CICPA 0110—2026"

#: 单个原件上限（当前最大的一份 PDF 是 667 KB / 1.37 MB，留足余量）。
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024


class SourceError(Exception):
    """原件获取/校验失败。调用方 MUST 停止，不得静默降级或换源重试。"""


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    filename: str
    url: str
    sha256: str
    kind: str                     # "zip" | "pdf"
    standard: str
    notice_attachment: str        # 通知页的编号
    inner_attachment: str = ""    # zip 内目录名用的标准正文编号（两套编号，别混）


#: sha256 全部来自 2026-11-06 官网实下载（见 docs/standard-sources-2026-11-06.md）。
#: 官网若更新附件，校验会失败并提示人工复核——这正是钉 hash 的目的。
SOURCES: Dict[str, Source] = {
    "xsd": Source(
        key="xsd", label="文件输出格式（6 个 XSD）", filename="file-output-format.zip",
        url=f"{ASSET_BASE}730153.zip",
        sha256="d39b73781b19b172aedfb2b36728ec4039e6e7b4b473cf00940119b64b6689b7",
        kind="zip", standard=STANDARD_PPE, notice_attachment="附件 7",
        inner_attachment="附件5：文件输出格式"),
    "examples": Source(
        key="examples", label="实例（6 个 XML）", filename="instances.zip",
        url=f"{ASSET_BASE}734563.zip",
        sha256="8f65fa2f14ee82689cd6563968dbab718002e8584b532d69892e6fdc0371db04",
        kind="zip", standard=STANDARD_PPE, notice_attachment="附件 8",
        inner_attachment="附件6：实例"),
    "ppe_pdf": Source(
        key="ppe_pdf", label="《注册会计师审计数据规范 固定资产》标准正文 PDF",
        filename="standard-ppe.pdf",
        url=f"{ASSET_BASE}603870.pdf",
        sha256="e6d868517b6022f83a291be8e4e5fa20c593958311719c55b1a5f9dba62776d4",
        kind="pdf", standard=STANDARD_PPE, notice_attachment="附件 2"),
}


def default_cache_dir() -> str:
    """应用数据目录下的原件缓存（不进仓库、不占项目目录）。"""
    return os.path.join(os.path.expanduser("~"), ".pic2edit", "standard_sources")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _assert_official(url: str) -> None:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname != OFFICIAL_HOST:
        raise SourceError(f"拒绝非官方来源：{url}（只允许 https://{OFFICIAL_HOST}）")


def get_source(key: str) -> Source:
    src = SOURCES.get(key)
    if src is None:
        raise SourceError(f"未知原件 {key!r}，可选：{', '.join(sorted(SOURCES))}")
    return src


def fetch(key: str, *, cache_dir: Optional[str] = None,
          offline: bool = False, timeout: float = 60.0) -> str:
    """取回一个原件的本地路径：命中且 hash 一致则直接复用，否则下载并校验。

    ``offline=True`` 时只读缓存，供「不联网」的单测与断网环境使用。
    校验失败抛 :class:`SourceError`，**不重试、不覆盖、不删除证据**。
    """
    src = get_source(key)
    _assert_official(src.url)
    root = os.path.abspath(os.path.expanduser(cache_dir or default_cache_dir()))
    # 缓存名带 hash 前缀：官网换版后自然落新文件，旧缓存不会被误当新版。
    dest = os.path.join(root, f"{src.key}-{src.sha256[:12]}-{src.filename}")
    if os.path.isfile(dest):
        got = sha256_of(dest)
        if got == src.sha256:
            return dest
        raise SourceError(
            f"缓存文件 hash 不符，已停止（不重试）：{dest}\n"
            f"  期望 {src.sha256}\n  实际 {got}")
    if offline:
        raise SourceError(
            f"--offline 且缓存未命中：{src.key}（{src.label}）。\n"
            f"  请联网运行一次，或把已校验的原件放到 {dest}")

    os.makedirs(root, exist_ok=True)
    part = dest + ".part"
    req = urllib.request.Request(
        src.url, headers={"User-Agent": f"pic2edit-schema-gen/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as out:
            total = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise SourceError(f"响应超过 {MAX_DOWNLOAD_BYTES} 字节上限，已中止：{src.url}")
                out.write(chunk)
    except SourceError:
        raise
    except Exception as e:
        raise SourceError(f"下载失败（不重试）：{src.url}\n  {type(e).__name__}: {e}") from e

    got = sha256_of(part)
    if got != src.sha256:
        # 保留证据，不删、不覆盖、不换镜像重试。
        bad = f"{dest}.bad-{got[:8]}"
        os.replace(part, bad)
        raise SourceError(
            f"原件 sha256 与台账不符，已停止（不重试）：{src.label}\n"
            f"  期望 {src.sha256}\n  实际 {got}\n"
            f"  官网附件可能已更新，请人工复核后更新台账；复核前不得使用。\n"
            f"  可疑文件已保留：{bad}")
    os.replace(part, dest)
    return dest


def decoded_name(info: zipfile.ZipInfo) -> str:
    """还原 zip 内文件名。

    官网这两个包没有 UTF-8 flag（``flag_bits & 0x800`` 为 0），中文名是 GBK；
    ``zipfile`` 按 cp437 解码会得到乱码，必须再转一次。有 UTF-8 flag 时原样返回。
    """
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


_SAFE_MEMBER_RE = re.compile(r"^[A-Za-z]:", re.I)


def _assert_safe_member(info: zipfile.ZipInfo, name: str) -> None:
    norm = name.replace("\\", "/")
    parts = norm.split("/")
    if norm.startswith("/") or ".." in parts or _SAFE_MEMBER_RE.match(norm):
        raise SourceError(f"zip 成员路径越界，拒绝读取：{name!r}")
    # Unix 外部属性高位是 mode；符号链接 = 0o120000。
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and (mode & 0o170000) == 0o120000:
        raise SourceError(f"zip 成员是符号链接，拒绝读取：{name!r}")


def list_members(key: str, *, cache_dir: Optional[str] = None,
                 offline: bool = False) -> List[Tuple[str, str]]:
    """返回 ``[(解码后名字, 原始名字)]``，只列文件、不列目录。"""
    src = get_source(key)
    if src.kind != "zip":
        raise SourceError(f"{key} 不是 zip 原件，无法列成员")
    path = fetch(key, cache_dir=cache_dir, offline=offline)
    with zipfile.ZipFile(path) as z:
        return [(decoded_name(i), i.filename) for i in z.infolist() if not i.is_dir()]


def read_member(key: str, wanted: str, *, cache_dir: Optional[str] = None,
                offline: bool = False) -> bytes:
    """从 zip 里按**文件名（大小写不敏感）**读一个成员，返回其字节。

    ``wanted`` 只写 basename 即可（如 ``PPE.xsd`` / ``PPE.XML``）——实例包里
    固定资产文件叫 ``PPE.XML``（大写扩展名），大小写敏感匹配会漏掉它。
    """
    src = get_source(key)
    if src.kind != "zip":
        raise SourceError(f"{key} 不是 zip 原件，无法读成员")
    path = fetch(key, cache_dir=cache_dir, offline=offline)
    with zipfile.ZipFile(path) as z:
        matches = []
        for info in z.infolist():
            if info.is_dir():
                continue
            disp = decoded_name(info)
            if os.path.basename(disp).casefold() == wanted.casefold():
                matches.append((info, disp))
        if not matches:
            avail = ", ".join(os.path.basename(decoded_name(i))
                              for i in z.infolist() if not i.is_dir())
            raise SourceError(f"{key} 里找不到成员 {wanted!r}；可用：{avail}")
        if len(matches) > 1:
            raise SourceError(f"{key} 里 {wanted!r} 有 {len(matches)} 个同名候选，拒绝猜测")
        info, disp = matches[0]
        _assert_safe_member(info, disp)
        return z.read(info)


def provenance(key: str, retrieved_at: str) -> Dict[str, str]:
    """给 ``manifest.json`` 的 ``x-provenance.sources[]`` 用的一条记录。"""
    src = get_source(key)
    return {
        "kind": "S1" if src.kind == "zip" else "S2",
        "key": src.key,
        "label": src.label,
        "url": src.url,
        "notice_url": NOTICE_URL,
        "standard": src.standard,
        "attachment": src.notice_attachment,
        "inner_attachment": src.inner_attachment,
        "sha256": src.sha256,
        "retrieved_at": retrieved_at,
    }
