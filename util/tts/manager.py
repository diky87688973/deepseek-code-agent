# -*- coding: utf-8 -*-
"""TTS 管理器：句子积累 → TTS → 串行推 audio 事件（每会话独立队列，保证顺序）。"""
from __future__ import annotations

import base64
import queue
import re
import threading
from typing import Optional

from util.tts import create_tts_provider
from util.tts.base import TTSProvider


# 句子结束符
_SENTENCE_END = re.compile(r"[。！？\n]+")

# 全局 TTS provider（延迟初始化）
_tts_provider: Optional[TTSProvider] = None
_tts_lock = threading.Lock()


def _get_tts() -> TTSProvider:
    global _tts_provider
    if _tts_provider is None:
        with _tts_lock:
            if _tts_provider is None:
                from util.config_loader import load_config
                cfg = load_config(verbose=False)
                engine = str(cfg.get("AGENT_TTS_ENGINE") or "console").strip().lower()
                voice = str(cfg.get("AGENT_TTS_VOICE") or "").strip() or None
                _tts_provider = create_tts_provider(engine, voice=voice)
    return _tts_provider


# ── 每会话独立串行 TTS 队列 ──
_tts_queues: dict[str, queue.Queue] = {}
_tts_queues_lock = threading.Lock()


def _get_queue(cid: str) -> queue.Queue:
    with _tts_queues_lock:
        q = _tts_queues.get(cid)
        if q is None:
            q = queue.Queue()
            _tts_queues[cid] = q
            # 启动一个消费线程，串行处理该会话的 TTS 请求
            threading.Thread(target=_consume_queue, args=(cid,), daemon=True).start()
    return q


def _consume_queue(cid: str) -> None:
    """串行消费队列中的 TTS 任务，确保 audio 事件按入队顺序推送。"""
    q = _tts_queues.get(cid)
    if q is None:
        return
    while True:
        text = q.get()
        if text is None:  # 终止信号
            break
        _synthesize_and_publish(cid, text)
    # 清理
    with _tts_queues_lock:
        _tts_queues.pop(cid, None)


def _synthesize_and_publish(cid: str, text: str) -> None:
    """合成音频并推 SSE 事件。"""
    try:
        # 去除 markdown 星号，避免朗读出"星号星号"
        clean = text.replace("*", "")
        if not clean.strip():
            return
        tts = _get_tts()
        audio = tts.synthesize(clean)
        if not audio:
            return
        publish = _ensure_sse_event_publisher()
        publish(cid, {
            "type": "audio",
            "audio": base64.b64encode(audio).decode("ascii"),
            "text": text,
        })
    except Exception as exc:
        import sys
        print(f"[TTS] 合成推送失败: {exc}", file=sys.stderr, flush=True)


def _ensure_sse_event_publisher():
    """惰性导入 publish_conversation_event 避免循环依赖。"""
    from agent_v2.agent_core import publish_conversation_event
    return publish_conversation_event


# ── 每会话 TTS 开关（默认从配置读取）──
_tts_enabled: dict[str, bool] = {}
_tts_enabled_lock = threading.Lock()
_tts_default_enabled: Optional[bool] = None
_tts_default_lock = threading.Lock()

def _get_default_enabled() -> bool:
    global _tts_default_enabled
    if _tts_default_enabled is None:
        with _tts_default_lock:
            if _tts_default_enabled is None:
                try:
                    from util.config_loader import load_config
                    cfg = load_config(verbose=False)
                    raw = str(cfg.get("AGENT_TTS_ENABLED") or "").strip().lower()
                    _tts_default_enabled = raw == "true" or raw == "1" or raw == "yes"
                except Exception:
                    _tts_default_enabled = False
    return _tts_default_enabled

def set_tts_enabled(conversation_id: str, enabled: bool) -> None:
    with _tts_enabled_lock:
        _tts_enabled[conversation_id] = enabled

def is_tts_enabled(conversation_id: str) -> bool:
    default = _get_default_enabled()
    with _tts_enabled_lock:
        return _tts_enabled.get(conversation_id, default)


# ── 每会话的句子积累器 ──
_sentence_buffers: dict[str, str] = {}
_sentence_buffers_lock = threading.Lock()


def feed_delta(conversation_id: str, delta: str) -> None:
    """向 TTS 管理器喂入文本 delta，积累完整句子后入队 TTS。"""
    if not delta or not conversation_id:
        return
    if not is_tts_enabled(conversation_id):
        return

    with _sentence_buffers_lock:
        buf = _sentence_buffers.get(conversation_id, "")
        buf += delta

        m = _SENTENCE_END.search(buf)
        if not m:
            _sentence_buffers[conversation_id] = buf
            return

        end_pos = m.end()
        sentence = buf[:end_pos]
        buf = buf[end_pos:]

        # 继续查找后续完整句子一并入队
        texts = [sentence]
        while True:
            m = _SENTENCE_END.search(buf)
            if not m:
                break
            end_pos = m.end()
            texts.append(buf[:end_pos])
            buf = buf[end_pos:]

        _sentence_buffers[conversation_id] = buf

    q = _get_queue(conversation_id)
    for t in texts:
        t = t.strip()
        if t and len(t) >= 2:
            q.put(t)


def flush_remaining(conversation_id: str) -> None:
    """清空缓冲区中剩余的文本，强制入队。"""
    with _sentence_buffers_lock:
        remaining = _sentence_buffers.pop(conversation_id, "")
    if remaining and len(remaining.strip()) >= 2 and is_tts_enabled(conversation_id):
        q = _get_queue(conversation_id)
        q.put(remaining.strip())
