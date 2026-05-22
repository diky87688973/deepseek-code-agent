# -*- coding: utf-8 -*-
"""Console TTS 实现：调试用，只打印文本不发声。"""
from __future__ import annotations

import sys
from typing import Optional

from .base import TTSProvider


class ConsoleTTSProvider(TTSProvider):
    """调试用，将文本打印到 stderr 代替发声。"""

    def __init__(self, **kwargs):
        pass

    name = "console"

    @property
    def default_voice(self) -> str:
        return "console"

    def synthesize(self, text: str, *, voice: Optional[str] = None) -> bytes:
        print(f"[TTS] {text}", file=sys.stderr, flush=True)
        return b""
