# -*- coding: utf-8 -*-
"""TTS 管理器：句子积累 → TTS → 串行推 audio 事件（每会话独立队列，保证顺序）。"""
from __future__ import annotations

import base64
import hashlib
import queue
import re
import sys
import threading
from typing import Dict, List, Optional, Tuple

from util.tts import create_tts_provider
from util.tts.base import TTSProvider

# TTS 合成超时（秒），超时后跳过当前任务继续消费下一项
_TTS_SYNTHESIZE_TIMEOUT: int = 15


# 句末：不用逗号切分，减少 Edge-TTS 碎片化请求
_SENTENCE_END = re.compile(r"[。！？!?;\n]+")

_TTS_MAX_SEGMENT_CHARS: Optional[int] = None
_TTS_MAX_SEGMENT_LOCK = threading.Lock()


def _tts_max_segment_chars() -> int:
    global _TTS_MAX_SEGMENT_CHARS
    if _TTS_MAX_SEGMENT_CHARS is None:
        with _TTS_MAX_SEGMENT_LOCK:
            if _TTS_MAX_SEGMENT_CHARS is None:
                try:
                    from util.config_loader import load_config

                    raw = str(load_config(verbose=False).get("AGENT_TTS_MAX_SEGMENT_CHARS") or "").strip()
                    n = int(raw) if raw else 240
                    _TTS_MAX_SEGMENT_CHARS = max(40, min(n, 2000))
                except Exception:
                    _TTS_MAX_SEGMENT_CHARS = 240
    return int(_TTS_MAX_SEGMENT_CHARS or 240)


def _chunk_text_for_tts(text: str) -> List[str]:
    """按句末切分；单段超长再硬切，避免一次合成过长。"""
    t = str(text or "").strip()
    if not t:
        return []
    max_len = _tts_max_segment_chars()
    parts: List[str] = []
    buf = t
    while buf:
        m = _SENTENCE_END.search(buf)
        if m:
            piece = buf[: m.end()].strip()
            buf = buf[m.end() :].lstrip()
        elif len(buf) > max_len:
            piece = buf[:max_len].strip()
            buf = buf[max_len:].lstrip()
        else:
            parts.append(buf)
            break
        if piece and len(piece) >= 2:
            if len(piece) <= max_len:
                parts.append(piece)
            else:
                for i in range(0, len(piece), max_len):
                    sub = piece[i : i + max_len].strip()
                    if sub and len(sub) >= 2:
                        parts.append(sub)
    return parts

# 可用的 edge-tts 中文音色列表（7 种）
TTS_VOICES: List[str] = [
    "zh-CN-XiaoxiaoNeural",   # 晓晓-女声亲切
    "zh-CN-XiaoyiNeural",    # 晓伊-女声自然
    "zh-CN-YunjianNeural",   # 云健-男声沉稳
    "zh-CN-YunxiNeural",     # 云希-男声阳光
    "zh-CN-YunyangNeural",   # 云扬-男声成熟
    "zh-CN-XiaochenNeural",  # 晓辰-女声可爱
    "zh-CN-XiaohanNeural",   # 晓涵-女声温润
]

# Agent 音色分配：用 CID+名字的 md5 哈希确定，不维护映射表

def voice_for(cid: str, name: str = "") -> str:
    """选择音色：用户主动对话（无发送者）用晓晓，其他 Agent 用 hash 分配。"""
    if not cid and not name:
        return TTS_VOICES[0]  # 晓晓-女声亲切
    raw = f"{cid or ''}|{name or ''}"
    idx = int(hashlib.md5(raw.encode("utf-8")).hexdigest(), 16) % len(TTS_VOICES)
    return TTS_VOICES[idx]


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
_tts_queues: Dict[str, queue.Queue] = {}
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


def _parse_queue_item(item: object) -> Tuple[str, Optional[str], str]:
    if isinstance(item, str):
        return item, None, ""
    if isinstance(item, (list, tuple)):
        if len(item) >= 3:
            text, voice, dbg = item[:3]
            return str(text or ""), voice, str(dbg or "")
        if len(item) >= 2:
            text, voice = item[:2]
            return str(text or ""), voice, ""
    return str(item or ""), None, ""


def _consume_queue(cid: str) -> None:
    """串行消费队列：当前项合成完成（或失败）后才处理下一项。"""
    q = _tts_queues.get(cid)
    if q is None:
        return
    while True:
        try:
            item = q.get()
        except Exception as exc:
            print(f"[TTS] queue get failed cid={cid}: {exc}", file=sys.stderr, flush=True)
            break
        if item is None:
            break
        text, voice, dbg = _parse_queue_item(item)
        if not str(text or "").strip():
            continue
        try:
            _synthesize_and_publish(cid, text, voice, dbg)
        except Exception as exc:
            print(
                f"[TTS] synthesize failed cid={cid}: {exc} text={str(text)[:40]}...",
                file=sys.stderr,
                flush=True,
            )
    with _tts_queues_lock:
        _tts_queues.pop(cid, None)


def _synthesize_and_publish(cid: str, text: str, voice: Optional[str] = None, _dbg_sender: str = "") -> None:
    """合成音频并推 SSE 事件。voice 指定音色，None 用默认。"""
    # 推前再检查一次开关，防止队列中的旧任务在关闭后继续播放
    if not is_tts_enabled(cid):
        return
    try:
        # 符号配音表：特殊符号 → 读音文字
        SYMBOL_MAP = {"→": "转为", "←": "从", "↑": "上", "↓": "下",
                      "±": "正负", "×": "乘", "÷": "除",
                      "℃": "摄氏度",
                      "≠": "不等于", "=": "等于",}
        for k, v in SYMBOL_MAP.items():
            text = text.replace(k, v)
        # 智能运算符：仅当 +/-/*/// 两侧是数字时才配音
        def _op_repl(m):
            op_map = {"+": "加", "-": "减", "*": "乘", "/": "除"}
            return m.group(1) + op_map.get(m.group(2), m.group(2)) + m.group(3)
        text = re.sub(r"(\d+)\s*([+\-*/])\s*(\d+)", _op_repl, text)
        # 百分号：先处理（先于小数），避免 3.14% 被拆散
        text = re.sub(r"([\d.]+)％", r"百分之\1", text)
        text = re.sub(r"([\d.]+)%", r"百分之\1", text)
        # 小数点：数字间的 . 把小数部分按位转中文数字
        def _dec_repl(m):
            cn = "零一二三四五六七八九"
            return m.group(1) + "点" + "".join(cn[int(d)] for d in m.group(2))
        text = re.sub(r"(\d+)\.(\d+)", _dec_repl, text)
        # 去掉转义序列（\n \t: " ", clean)
        clean = re.sub(r"\\[a-z]", "", text)
        # 只保留中文、英文、数字和空格，其他全部替换为空格
        clean = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9\s]", " ", clean)
        # 合并多个连续空格为一个
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean.strip():
            return
        tts = _get_tts()
        # ── 带超时的合成，防止 edge-tts 网络卡死消费线程 ──
        # 用 daemon 线程 + join(timeout)：超时后抛弃，不泄漏线程池
        _result: List[bytes] = []
        _exc: List[Exception] = []

        def _synth() -> None:
            try:
                _result.append(tts.synthesize(clean, voice=voice))
            except Exception as e:
                _exc.append(e)

        t = threading.Thread(target=_synth, daemon=True)
        t.start()
        t.join(timeout=_TTS_SYNTHESIZE_TIMEOUT)
        if t.is_alive():
            print(f"[TTS] 合成超时({_TTS_SYNTHESIZE_TIMEOUT}s)，跳过: {str(clean)[:40]}...", file=sys.stderr, flush=True)
            # daemon 线程放任不管，进程退出时自动清理
            return
        if _exc:
            print(f"[TTS] 合成失败: {_exc[0]}", file=sys.stderr, flush=True)
            return
        audio = _result[0] if _result else None
        if not audio:
            return
        publish = _ensure_sse_event_publisher()
        publish(cid, {
            "type": "audio",
            "audio": base64.b64encode(audio).decode("ascii"),
            "text": text,
            "voice": voice or "zh-CN-XiaoxiaoNeural",
            "_dbg": _dbg_sender or "",
        })
    except Exception as exc:
        print(f"[TTS] 合成推送失败: {exc}", file=sys.stderr, flush=True)


def _ensure_sse_event_publisher():
    """惰性导入 publish_conversation_event 避免循环依赖。"""
    from agent_v4.agent_core import publish_conversation_event
    return publish_conversation_event


# ── 每会话 TTS 开关（默认从配置读取）──
_tts_enabled: Dict[str, bool] = {}
_tts_enabled_lock = threading.Lock()
_tts_default_enabled: Optional[bool] = None
_tts_default_lock = threading.Lock()
_TTS_ENABLED_MAX = 512
_tts_last_voice: Dict[str, Tuple[Optional[str], str]] = {}


def _tts_segment_ok(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if len(t) >= 2:
        return True
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", t))


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
        while len(_tts_enabled) > _TTS_ENABLED_MAX:
            _tts_enabled.pop(next(iter(_tts_enabled)))

def is_tts_enabled(conversation_id: str) -> bool:
    """按会话检查 TTS 是否开启（不再因任一会话开启而全局合成）。"""
    default = _get_default_enabled()
    with _tts_enabled_lock:
        return _tts_enabled.get(conversation_id, default)


# ── 每会话的句子积累器 ──
_sentence_buffers: Dict[str, str] = {}
_sentence_buffers_lock = threading.Lock()


def feed_delta(conversation_id: str, delta: str, voice: Optional[str] = None, *, _dbg_sender: str = "") -> None:
    """向 TTS 管理器喂入文本 delta。voice 指定音色，None 用默认。"""
    if not delta or not conversation_id:
        return
    if not is_tts_enabled(conversation_id):
        return

    max_len = _tts_max_segment_chars()
    texts: List[str] = []
    with _sentence_buffers_lock:
        _tts_last_voice[conversation_id] = (voice, _dbg_sender or "")
        buf = _sentence_buffers.get(conversation_id, "")
        buf += delta

        while buf:
            m = _SENTENCE_END.search(buf)
            if m:
                end_pos = m.end()
                sentence = buf[:end_pos]
                buf = buf[end_pos:]
                texts.extend(_chunk_text_for_tts(sentence))
                continue
            if len(buf) >= max_len:
                texts.extend(_chunk_text_for_tts(buf[:max_len]))
                buf = buf[max_len:]
                continue
            break

        _sentence_buffers[conversation_id] = buf

    q = _get_queue(conversation_id)
    dbg = _dbg_sender or ""
    for t in texts:
        t = t.strip()
        if _tts_segment_ok(t):
            q.put((t, voice, dbg))


def flush_remaining(conversation_id: str) -> None:
    """清空缓冲区中剩余的文本，强制入队。"""
    with _sentence_buffers_lock:
        voice, dbg = _tts_last_voice.get(conversation_id, (None, ""))
        remaining = _sentence_buffers.pop(conversation_id, "")
    if remaining and is_tts_enabled(conversation_id):
        q = _get_queue(conversation_id)
        for t in _chunk_text_for_tts(remaining.strip()):
            if _tts_segment_ok(t):
                q.put((t, voice, dbg))
