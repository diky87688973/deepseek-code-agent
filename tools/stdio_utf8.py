# -*- coding: utf-8 -*-
"""
子进程/管道场景：将 stdin/stdout/stderr 对齐为 UTF-8，并设置 PYTHONUTF8 / PYTHONIOENCODING。

请 ``import stdio_utf8`` 并调用 ``install_stdio_utf8()``。
"""

from __future__ import annotations

import os
import sys


def install_stdio_utf8() -> None:
    """尽量把标准流改为 UTF-8；失败则忽略。"""
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError, TypeError):
            pass
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
