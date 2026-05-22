# -*- coding: utf-8 -*-
"""TTS 模块。支持多种 TTS 引擎，通过工厂函数切换。

快速开始：
    from util.tts import create_tts_provider
    
    # 调试模式（只打印不发声）
    tts = create_tts_provider("console")
    
    # edge-tts 模式（需 pip install edge-tts）
    tts = create_tts_provider("edge", voice="zh-CN-XiaoxiaoNeural")
    
    # 合成
    audio = tts.synthesize("你好，世界")
"""
from __future__ import annotations

from .base import TTSProvider
from .factory import create_tts_provider

__all__ = [
    "TTSProvider",
    "create_tts_provider",
]
