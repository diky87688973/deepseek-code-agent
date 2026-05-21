# -*- coding: utf-8 -*-
"""session_wait 工具：非阻塞检查指定 Agent 是否已回复某个 thread。"""
from __future__ import annotations

from typing import Any, Dict, List


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "是"):
        return True
    if s in ("0", "false", "no", "off", "否", "不"):
        return False
    return default


def _normalize_target_ids(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw or "").strip()
    if not s:
        return []
    return [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]


def agent_main(*, action: str = "check", target_ids: Any = None, thread_id: str = "", suspend: Any = None, **_kwargs: Any) -> Dict[str, Any]:
    action = str(action or "check").strip().lower()
    if action not in ("check", "wait"):
        return {"ok": False, "error": {"type": "unknown_action", "message": "可选 action: check/wait（非阻塞）"}}
    targets = _normalize_target_ids(target_ids)
    if not targets:
        return {"ok": False, "error": {"type": "missing_targets", "message": "缺少 target_ids"}}
    thread = str(thread_id or _kwargs.get("thread_id") or "").strip()
    cid = str(_kwargs.get("conversation_id") or "").strip()
    if not cid:
        return {"ok": False, "error": {"type": "missing_conversation", "message": "缺少当前 conversation_id"}}
    from agent_v2.live_state import CONVERSATIONS
    from agent_v2.agent_core import _ensure_conversation_loaded

    _ensure_conversation_loaded(cid)
    msgs = CONVERSATIONS.get(cid) or []

    # ── 安全检查：等待前必须发过 requires_reply=true 的消息 ──
    _sent_reply_request: Dict[str, bool] = {}
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if not m.get("_requires_reply_sentinel"):
            continue
        m_target = str(m.get("_target_id") or "").strip()
        m_thread = str(m.get("_thread_id") or "").strip()
        for t in targets:
            if m_target == t:
                if thread and m_thread == thread:
                    _sent_reply_request[t] = True
                elif not thread:
                    _sent_reply_request[t] = True
    _no_request_targets = [t for t in targets if not _sent_reply_request.get(t)]
    if _no_request_targets:
        msg_suffix = ""
        if thread:
            msg_suffix = f"（thread={thread}）"
        return {
            "ok": False,
            "error": {
                "type": "wait_without_request",
                "message": (
                    f"目标 {'/'.join(_no_request_targets)} 未收到 requires_reply=true 的请求{msg_suffix}，"
                    "不会主动回复。调 session_wait 前须先用 session_send/session_multisend 发 requires_reply=true 的消息。"
                ),
            },
        }

    completed = set()
    replies = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        sender = str(m.get("_sender") or "").strip()
        if sender not in targets:
            continue
        if thread and str(m.get("_thread_id") or "").strip() != thread:
            continue
        if m.get("role") == "user":
            completed.add(sender)
            replies.append(
                {
                    "from": sender,
                    "from_name": str(m.get("_sender_name") or ""),
                    "content": str(m.get("content") or ""),
                    "thread_id": str(m.get("_thread_id") or ""),
                    "channel": str(m.get("_channel") or ""),
                }
            )
    pending = [t for t in targets if t not in completed]
    data: Dict[str, Any] = {
        "completed": sorted(completed),
        "pending": pending,
        "messages": replies,
        "all_done": not pending,
        "thread_id": thread,
    }
    suspend_requested = _as_bool(_kwargs.get("suspend", suspend), False) or action == "wait"
    if pending:
        data["should_stop_turn"] = True
        data["instruction"] = (
            "目标 Agent 尚未回复。不要在同一轮继续轮询 session_wait；"
            "请结束当前回复，等待对方通过 session_send 把消息写入本会话后再继续。"
        )
        if suspend_requested:
            from agent_v2.live_state import suspend_agent_wait
            wait_state = suspend_agent_wait(cid, targets, thread)
            data["suspend"] = True
            data["wait_state"] = wait_state
    return {"ok": True, "data": data}
