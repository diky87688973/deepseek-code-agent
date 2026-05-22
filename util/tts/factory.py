# -*- coding: utf-8 -*-
"""TTS 工厂函数，根据配置选择实现。"""
from __future__ import annotations

from typing import Optional

from .base import TTSProvider


def create_tts_provider(engine: str = "console", **kwargs) -> TTSProvider:
    """创建 TTS Provider 实例。
    
    Args:
        engine: 引擎名，支持 'edge'(需安装 edge-tts) 和 'console'(静默调试)
        **kwargs: 透传给具体实现的参数（如 voice）
    
    Returns:
        TTSProvider 实例
    """
    engine = (engine or "console").strip().lower()

    if engine == "edge":
        from .edge import EdgeTTSProvider
        return EdgeTTSProvider(**kwargs)

    # 默认 console 模式：只 print 不发声，方便调试
    from .console import ConsoleTTSProvider
    return ConsoleTTSProvider(**kwargs)
