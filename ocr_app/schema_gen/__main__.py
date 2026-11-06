# -*- coding: utf-8 -*-
"""``python -m schema_gen`` 的入口。"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
