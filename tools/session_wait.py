# -*- coding: utf-8 -*-
"""session_wait 工具：非阻塞检查指定 Agent 是否已回复某个 thread。"""
from __future__ import annotations

from typing import Any, Dict, List


def _normalize_target_ids(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw or "").strip()
    if not s:
        return []
    return [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]


def _sentinel_index_by_target(msgs: List[Dict[str, Any]], thread: str) -> Dict[str, int]:
    """每个 target 最近一次 requires_reply 请求哨兵的下标。"""
    out: Dict[str, int] = {}
    for i, m in enumerate(msgs):
        if not isinstance(m, dict) or not m.get("_requires_reply_sentinel"):
            continue
        t = str(m.get("_target_id") or "").strip()
        if not t:
            continue
        th = str(m.get("_thread_id") or "").strip()
        if thread and th and th != thread:
            continue
        out[t] = i
    return out


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
                    "不会主动回复。调 session_wait 前须先用 session_send/session_multisend/session_broadcast "
                    "发 requires_reply=true 的消息。"
                ),
            },
        }

    sentinel_at = _sentinel_index_by_target(msgs, thread)
    completed = set()
    replies = []
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        sender = str(m.get("_sender") or "").strip()
        if sender not in targets:
            continue
        if thread and str(m.get("_thread_id") or "").strip() != thread:
            continue
        if m.get("role") != "user" or not m.get("_agent_peer_message"):
            continue
        # 纯通知（入站 requires_reply=false）不算 wait 意义上的「回复」
        if m.get("_requires_reply") is False:
            continue
        sent_idx = sentinel_at.get(sender)
        if sent_idx is None or i <= sent_idx:
            continue
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
    if pending:
        data["should_stop_turn"] = True
        data["instruction"] = (
            "目标 Agent 尚未回复。不要在同一轮继续轮询 session_wait；"
            "请结束当前回复，等待对方通过 session_send 把消息写入本会话后再继续。"
        )
        from agent_v2.live_state import suspend_agent_wait

        wait_state = suspend_agent_wait(cid, targets, thread)
        data["suspend"] = True
        data["wait_state"] = wait_state
    return {"ok": True, "data": data}
