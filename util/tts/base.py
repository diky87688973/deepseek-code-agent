# -*- coding: utf-8 -*-
"""TTS 抽象接口层。所有 TTS 实现必须继承 TTSProvider。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class TTSProvider(ABC):
    """TTS 引擎抽象基类。"""

    @abstractmethod
    def synthesize(self, text: str, *, voice: Optional[str] = None) -> bytes:
        """将文本合成为音频字节流（默认 WAV/MP3 格式取决于实现）。
        
        Args:
            text: 要朗读的文本
            voice: 音色标识，None 表示使用默认音色
        
        Returns:
            音频数据字节流
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """返回实现名称，如 'edge', 'console'"""
        ...

    @property
    @abstractmethod
    def default_voice(self) -> str:
        """返回默认音色标识"""
        ...

    @property
    def available_voices(self) -> list[dict[str, str]]:
        """返回可用音色列表，每项含 name/display/性别等。默认返回空。"""
        return []
