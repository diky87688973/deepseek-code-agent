# -*- coding: utf-8 -*-
"""
将 stdin/stdout/stderr 对齐为 UTF-8，并为本进程设置 PYTHONUTF8 / PYTHONIOENCODING（子进程可继承）。

在各 cli_*.py 与 orch 入口最早调用 ``install_stdio_utf8()``，降低 Windows 管道与中文 JSON 乱码概率。

约定：工具库与 Code Web Agent 的源文件、清单、提示词及 request/payload 等落盘文本一律 UTF-8。若某处对子进程输出尝试 gbk/gb18030 解码，仅为兼容不可控字节，不改变上述约定。
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
