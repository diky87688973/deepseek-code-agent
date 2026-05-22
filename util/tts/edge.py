# -*- coding: utf-8 -*-
"""edge-tts 实现。需要 pip install edge-tts。"""
from __future__ import annotations

import sys
from typing import Optional

from .base import TTSProvider


class EdgeTTSProvider(TTSProvider):
    """基于微软 Edge 浏览器的免费 TTS 服务。"""

    name = "edge"

    # 常见中文音色
    VOICES = [
        {"name": "zh-CN-XiaoxiaoNeural", "display": "晓晓（女声-亲切）", "gender": "female"},
        {"name": "zh-CN-XiaoyiNeural", "display": "晓伊（女声-自然）", "gender": "female"},
        {"name": "zh-CN-YunjianNeural", "display": "云健（男声-沉稳）", "gender": "male"},
        {"name": "zh-CN-YunxiNeural", "display": "云希（男声-阳光）", "gender": "male"},
        {"name": "zh-CN-YunyangNeural", "display": "云扬（男声-成熟）", "gender": "male"},
        {"name": "zh-CN-XiaochenNeural", "display": "晓辰（女声-可爱）", "gender": "female"},
    ]

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self._voice = voice
        self._import_err: Optional[str] = None
        try:
            import edge_tts  # noqa
        except ImportError:
            self._import_err = (
                "edge-tts 未安装。请执行: pip install edge-tts"
            )

    @property
    def default_voice(self) -> str:
        return self._voice

    @property
    def available_voices(self) -> list[dict[str, str]]:
        return list(self.VOICES)

    def synthesize(self, text: str, *, voice: Optional[str] = None) -> bytes:
        if self._import_err:
            print(f"[EdgeTTS] {self._import_err}", file=sys.stderr, flush=True)
            return b""

        import edge_tts

        v = voice or self._voice
        try:
            communicate = edge_tts.Communicate(text, v)
            chunks = []
            for chunk in communicate.stream_sync():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)
        except Exception as exc:
            print(f"[EdgeTTS] 合成失败: {exc}", file=sys.stderr, flush=True)
            return b""
