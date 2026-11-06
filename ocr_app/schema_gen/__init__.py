# -*- coding: utf-8 -*-
"""schema 生成器（工作流 B 的自动化入口）。

与 ``schemas/`` 的关系
-----------------------
``ocr_app/schemas/`` 是注册表 ``BUILTIN_ROOT``：它会扫描子目录里的 ``manifest.json``
并把它当 schema 包装载。因此本包**必须**待在 ``schemas/`` 之外，产物也 MUST NOT
写进 ``schemas/``。这里只有「造包的机器」，没有任何具体票种的字段知识。

分层：
  · ``sources`` — 官网原件下载 / sha256 钉住 / 本地缓存 / zip 成员读取
  · ``readers`` — XSD / XML 实例 / 国标 PDF → 内部结构（IR）
  · ``ir``      — 中间表示（只内存，不落盘）
  · ``emit``    — IR → manifest.json + schema.yaml + samples/
  · ``verify``  — 生成后自检（结构与效果两层）
  · ``cli``     — 唯一用户入口 ``python -m schema_gen``

规范性契约见 ``docs/schema-technical-spec-v1.md`` §6，执行计划见
``docs/schema-gen-plan.md``。
"""
from __future__ import annotations

__version__ = "1.0.0"
