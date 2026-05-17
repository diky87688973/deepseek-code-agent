# -*- coding: utf-8 -*-
"""会话落盘：消息 ID（v2 轮次 / v1 单条）、.raw 全量追加日志、按 ID 删除（摘要合并）。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from util.session_crypto import decrypt_raw_line, encrypt_raw_line

_AGENT_ROUND_ID = "_agent_round_id"
_AGENT_MESSAGE_ID = "_agent_message_id"
_AGENT_SUMMARY = "_agent_summary"

_last_id_ms = 0
_id_seq = 0

# 避免每条消息 append .raw 时 glob 扫 session 目录（工具多轮时会卡死 SSE，客户端断连 WinError 10054）
_session_json_path_cache: Dict[str, Path] = {}


def _next_time_ordered_id() -> str:
    """毫秒时间戳 ID；同毫秒内用 _N 后缀保证唯一且仍可按字符串排序。"""
    global _last_id_ms, _id_seq
    ms = int(time.time() * 1000)
    if ms == _last_id_ms:
        _id_seq += 1
        return f"{ms}_{_id_seq}"
    _last_id_ms = ms
    _id_seq = 0
    return str(ms)


def new_round_id() -> str:
    """轮次 ID：毫秒时间戳字符串，唯一且可排序。"""
    return _next_time_ordered_id()


def new_message_id() -> str:
    """单条消息 ID（v1）：时间戳序 ID。"""
    return _next_time_ordered_id()


def stamp_message_v2(
    msg: Dict[str, Any],
    *,
    new_round: bool = False,
    round_id: Optional[str] = None,
) -> str:
    """为 v2 消息写入 _agent_round_id 与 _agent_message_id；返回本条所属 round_id。"""
    if new_round or msg.get("role") == "user" or not round_id:
        rid = new_round_id()
    else:
        rid = str(round_id)
    msg[_AGENT_ROUND_ID] = rid
    if _AGENT_MESSAGE_ID not in msg:
        msg[_AGENT_MESSAGE_ID] = new_message_id()
    return rid


def stamp_message_v1(msg: Dict[str, Any]) -> str:
    """v1：每条消息独立 message_id（时间戳）。"""
    mid = new_message_id()
    msg[_AGENT_MESSAGE_ID] = mid
    return mid


def ensure_conversation_message_ids_v2(messages: List[Dict[str, Any]]) -> None:
    """为历史会话补全轮次/消息 ID（按 user 起轮切分）。"""
    current_round: Optional[str] = None
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "user":
            current_round = str(m.get(_AGENT_ROUND_ID) or "") or new_round_id()
            m[_AGENT_ROUND_ID] = current_round
            if not m.get(_AGENT_MESSAGE_ID):
                m[_AGENT_MESSAGE_ID] = new_message_id()
        elif current_round and role in ("assistant", "tool"):
            m.setdefault(_AGENT_ROUND_ID, current_round)
            if not m.get(_AGENT_MESSAGE_ID):
                m[_AGENT_MESSAGE_ID] = new_message_id()


def ensure_conversation_message_ids_v1(messages: List[Dict[str, Any]]) -> None:
    for m in messages:
        if isinstance(m, dict) and not m.get(_AGENT_MESSAGE_ID):
            m[_AGENT_MESSAGE_ID] = new_message_id()


def round_ids_from_messages(msgs: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for m in msgs:
        if not isinstance(m, dict):
            continue
        rid = str(m.get(_AGENT_ROUND_ID) or "").strip()
        if rid and rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def message_ids_from_messages(msgs: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for m in msgs:
        if not isinstance(m, dict):
            continue
        mid = str(m.get(_AGENT_MESSAGE_ID) or "").strip()
        if mid and mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def remove_messages_by_round_ids(messages: List[Dict[str, Any]], round_ids: List[str]) -> int:
    rid_set = {str(r).strip() for r in round_ids if str(r).strip()}
    if not rid_set:
        return 0
    removed = 0
    i = 0
    while i < len(messages):
        m = messages[i]
        if str(m.get(_AGENT_ROUND_ID) or "") in rid_set:
            messages.pop(i)
            removed += 1
        else:
            i += 1
    return removed


def remove_messages_by_message_ids(messages: List[Dict[str, Any]], message_ids: List[str]) -> int:
    mid_set = {str(x).strip() for x in message_ids if str(x).strip()}
    if not mid_set:
        return 0
    removed = 0
    i = 0
    while i < len(messages):
        m = messages[i]
        if str(m.get(_AGENT_MESSAGE_ID) or "") in mid_set:
            messages.pop(i)
            removed += 1
        else:
            i += 1
    return removed


def cache_session_json_path(cid: str, path: Path) -> None:
    key = str(cid or "").strip()
    if key:
        _session_json_path_cache[key] = path


def resolve_session_json_path(
    cid: str,
    locate: Callable[[str], Optional[Path]],
    default_for_new: Callable[[str], Path],
) -> Path:
    """解析会话 json 路径（带进程内缓存）；locate 应等价于 _find_conversation_file。"""
    key = str(cid or "").strip()
    if not key:
        return default_for_new(key)
    hit = _session_json_path_cache.get(key)
    if hit is not None:
        return hit
    existing = locate(key)
    if existing is not None:
        _session_json_path_cache[key] = existing
        return existing
    p = default_for_new(key)
    _session_json_path_cache[key] = p
    return p


def format_raw_line(msg: Dict[str, Any]) -> str:
    """单行一条消息：JSON（与 {role=...,content:...} 语义一致，便于解析）。"""
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":"), default=str)


def parse_raw_line(line: str) -> Optional[Dict[str, Any]]:
    """解析 .raw 一行（支持明文 JSON 行 / 按行加密 envelope）。"""
    try:
        plain_bytes = decrypt_raw_line(line)
    except Exception:
        return None
    if not plain_bytes:
        return None
    try:
        o = json.loads(plain_bytes.decode("utf-8"))
        return o if isinstance(o, dict) else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def session_raw_path(session_json_path: Path) -> Path:
    return session_json_path.with_suffix(".raw")


def append_raw_message(session_json_path: Path, msg: Dict[str, Any]) -> None:
    """追加一条消息：启用加密时每行独立 envelope，无需读全文件。"""
    rawp = session_raw_path(session_json_path)
    rawp.parent.mkdir(parents=True, exist_ok=True)
    line = encrypt_raw_line(format_raw_line(msg))
    with rawp.open("a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.write("\n")
        f.flush()


def load_messages_from_raw(session_json_path: Path) -> List[Dict[str, Any]]:
    """从 {session}.raw 按行恢复消息列表（json 被误合并清空时的兜底）。"""
    rawp = session_raw_path(session_json_path)
    if not rawp.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with rawp.open("r", encoding="utf-8") as f:
            for line in f:
                m = parse_raw_line(line)
                if m is not None:
                    out.append(m)
    except Exception:
        return []
    return out


def bootstrap_raw_from_messages(session_json_path: Path, messages: List[Dict[str, Any]]) -> None:
    """若尚无 .raw，用当前 messages 初始化全量追加日志。"""
    rawp = session_raw_path(session_json_path)
    if rawp.is_file():
        return
    rawp.parent.mkdir(parents=True, exist_ok=True)
    with rawp.open("w", encoding="utf-8", newline="\n") as f:
        for m in messages:
            if isinstance(m, dict):
                f.write(encrypt_raw_line(format_raw_line(m)))
                f.write("\n")


def excerpt_meta_round_ids(meta: Dict[str, Any]) -> List[str]:
    am = meta.get("agent_excerpt_meta") if isinstance(meta, dict) else meta
    if not isinstance(am, dict):
        return []
    raw = am.get("round_ids")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def excerpt_meta_message_ids(meta: Dict[str, Any]) -> List[str]:
    am = meta.get("agent_excerpt_meta") if isinstance(meta, dict) else meta
    if not isinstance(am, dict):
        return []
    raw = am.get("message_ids")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def excerpt_meta_index_range(meta: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    am = meta.get("agent_excerpt_meta") if isinstance(meta, dict) else meta
    if not isinstance(am, dict):
        return None
    try:
        s = int(am["start_idx"])
        e = int(am["end_idx"])
        return s, e
    except (KeyError, TypeError, ValueError):
        return None
