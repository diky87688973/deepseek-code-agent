# -*- coding: utf-8 -*-
"""进程内会话表、并发 run、停止标志（从 deepseek_code_agent2 拆出）。"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List, Optional, Set


# ---------- in-memory multi-round conversations (stateless API per DeepSeek docs) ----------
CONVERSATIONS: Dict[str, List[Dict[str, Any]]] = {}
PENDING_USER_CONFIRM: Dict[str, Dict[str, Any]] = {}

# ── kling_generate 确认 ID 系统 ──
# 每次生成任务创建一个 UUID，模型需获取确认后才能使用
_KLING_CONFIRM_IDS: Dict[str, Dict[str, Any]] = {}
_KLING_CONFIRM_LOCK = threading.Lock()


def kling_create_confirm_id(action: str, params: dict) -> str:
    """创建待确认的确认ID，返回 UUID。"""
    import uuid
    cid = str(uuid.uuid4())
    with _KLING_CONFIRM_LOCK:
        _KLING_CONFIRM_IDS[cid] = {
            "confirmed": False,
            "action": action,
            "params": dict(params),
        }
    return cid


def kling_mark_confirmed(confirm_id: str) -> bool:
    """将确认ID标记为已确认。"""
    with _KLING_CONFIRM_LOCK:
        if confirm_id in _KLING_CONFIRM_IDS:
            _KLING_CONFIRM_IDS[confirm_id]["confirmed"] = True
            return True
    return False


def kling_consume_confirm_id(confirm_id: str) -> Optional[Dict[str, Any]]:
    """检查并消耗确认ID。返回任务信息表示放行，None 表示无效。"""
    with _KLING_CONFIRM_LOCK:
        info = _KLING_CONFIRM_IDS.get(confirm_id)
        if info is None:
            return None
        if not info.get("confirmed"):
            return None
        # 已确认，消耗删除
        del _KLING_CONFIRM_IDS[confirm_id]
        return dict(info)
CONVERSATION_MODES: Dict[str, str] = {}
SUMMARY_IN_PROGRESS: Dict[str, float] = {}
PENDING_EXCERPT_PATHS: Dict[str, List[str]] = {}
_SUMMARY_STATE_LOCK = threading.Lock()
_CONVERSATION_RUN_LOCKS: Dict[str, threading.RLock] = {}
_CONVERSATION_RUN_LOCKS_LOCK = threading.Lock()
_TOOL_EXEC_LOCK = threading.RLock()
_CONVERSATION_STOP_FLAGS: Dict[str, Set[str]] = {}
_ACTIVE_CONVERSATION_RUNS: Dict[str, str] = {}
_CONVERSATION_STOP_LOCK = threading.Lock()
_SERVER_SHUTTING_DOWN = False


def server_shutting_down() -> bool:
    """进程正在退出（Ctrl+C / 托盘退出）；用于打断 SSE 与 LLM 流式读。"""
    return _SERVER_SHUTTING_DOWN


def abort_all_conversation_runs_on_shutdown() -> None:
    """uvicorn 关闭时标记所有活跃 run 为停止，避免 graceful shutdown 被 SSE 挂死。"""
    global _SERVER_SHUTTING_DOWN
    _SERVER_SHUTTING_DOWN = True
    with _CONVERSATION_STOP_LOCK:
        for cid, run_id in list(_ACTIVE_CONVERSATION_RUNS.items()):
            if run_id:
                _CONVERSATION_STOP_FLAGS.setdefault(cid, set()).add(run_id)


def _begin_conversation_run(cid: str) -> Optional[str]:
    key = str(cid or "")
    with _CONVERSATION_STOP_LOCK:
        if key in _ACTIVE_CONVERSATION_RUNS:
            return None
        run_id = str(uuid.uuid4())
        _ACTIVE_CONVERSATION_RUNS[key] = run_id
        _CONVERSATION_STOP_FLAGS.pop(key, None)
        return run_id


def _end_conversation_run(cid: str, run_id: str = "") -> None:
    key = str(cid or "")
    with _CONVERSATION_STOP_LOCK:
        active = _ACTIVE_CONVERSATION_RUNS.get(key)
        if not run_id or active == run_id:
            _ACTIVE_CONVERSATION_RUNS.pop(key, None)
            _CONVERSATION_STOP_FLAGS.pop(key, None)


def _request_conversation_stop(cid: str, run_id: str = "") -> bool:
    key = str(cid or "")
    if not key:
        return False
    with _CONVERSATION_STOP_LOCK:
        active = _ACTIVE_CONVERSATION_RUNS.get(key)
        if not active:
            _CONVERSATION_STOP_FLAGS.pop(key, None)
            return False
        rid = str(run_id or active)
        if rid != active:
            return False
        _CONVERSATION_STOP_FLAGS.setdefault(key, set()).add(rid)
        return True


def _consume_conversation_stop_requested(cid: str, run_id: str = "") -> bool:
    key = str(cid or "")
    rid = str(run_id or "")
    with _CONVERSATION_STOP_LOCK:
        flags = _CONVERSATION_STOP_FLAGS.get(key)
        if not flags or rid not in flags:
            return False
        flags.discard(rid)
        if not flags:
            _CONVERSATION_STOP_FLAGS.pop(key, None)
        return True


def _peek_conversation_stop_requested(cid: str, run_id: str = "") -> bool:
    key = str(cid or "")
    with _CONVERSATION_STOP_LOCK:
        active = _ACTIVE_CONVERSATION_RUNS.get(key)
        eff = str(run_id or active or "")
        flags = _CONVERSATION_STOP_FLAGS.get(key)
        if not flags or not eff:
            return False
        return eff in flags


def _get_conversation_run_lock(cid: str) -> threading.RLock:
    key = str(cid or "")
    with _CONVERSATION_RUN_LOCKS_LOCK:
        lock = _CONVERSATION_RUN_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _CONVERSATION_RUN_LOCKS[key] = lock
        return lock
