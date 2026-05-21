# -*- coding: utf-8 -*-
"""session_send 工具：Agent 间互发消息，触发接收方执行。"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _as_bool(v: Any, default: bool = True) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("0", "false", "no", "off", "否", "不"):
        return False
    if s in ("1", "true", "yes", "on", "是"):
        return True
    return default


def _get_last_assistant_content(messages: list) -> str:
    """从消息列表中提取最后一条 assistant 的 content。"""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            return str(m.get("content") or "")
    return ""





def agent_main(
    *,
    action: str = "",
    target_id: str = "",
    message: str = "",
    priority: str = "normal",
    thread_id: str = "",
    requires_reply: Optional[bool] = None,
    channel: str = "direct",
    **_kwargs: Any,
) -> Dict[str, Any]:
    """session_send 入口。action=send 发消息给目标会话并触发执行。"""
    action = str(action or "").strip().lower()
    if not action:
        return {"ok": False, "error": {"type": "invalid_args", "message": "缺少 action 参数。可选: send"}}
    if action != "send":
        return {"ok": False, "error": {"type": "unknown_action", "message": f"未知 action: {action}。可选: send"}}

    tid = str(target_id or "").strip()
    msg = str(message or "").strip()
    if not tid:
        return {"ok": False, "error": {"type": "missing_target", "message": "缺少 target_id"}}
    if not msg:
        return {"ok": False, "error": {"type": "missing_message", "message": "缺少 message"}}

    # requires_reply 为必填参数，必须显式设置 true 或 false
    _rr_raw = _kwargs.get("requires_reply", requires_reply)
    if _rr_raw is None:
        return {
            "ok": False,
            "error": {
                "type": "missing_requires_reply",
                "message": "requires_reply 是必填参数。收到消息后，根据对方消息中的 requires_reply 元数据决定传 true（需回复）或 false（纯通知）。",
            },
        }
    requires_reply_bool = _as_bool(_rr_raw, True)
    from agent_v2.live_state import (
        CONVERSATIONS,
        _ACTIVE_CONVERSATION_RUNS,
        enqueue_session_inbox,
        get_agent_session,
    )
    from agent_v2.agent_core import (
        _append_incoming_session_message,
        _append_session_message_v2,
        _ensure_conversation_loaded,
        _save_conversation,
        publish_conversation_event,
        start_background_agent_turn,
    )

    # ── 获取发送方身份 ──
    src_cid = str(_kwargs.get("conversation_id") or "").strip()
    sender_fields: Dict[str, str] = {}
    if src_cid:
        meta = get_agent_session(src_cid)
        if meta:
            sender_fields["_sender"] = src_cid
            sender_fields["_sender_role"] = str(meta.get("role") or "")
            sender_fields["_sender_name"] = str(meta.get("name") or "")
    if "_sender" not in sender_fields:
        sender_fields["_sender"] = src_cid or "unknown"
        sender_fields["_sender_role"] = "boss"
        sender_fields["_sender_name"] = src_cid[:12] if src_cid else "unknown"

    # ── 确保目标会话已加载 ──
    _ensure_conversation_loaded(tid)
    target_messages = CONVERSATIONS.get(tid)
    if not target_messages:
        return {"ok": False, "error": {"type": "target_not_found", "message": f"目标会话 {tid} 不存在"}}

    # ── 构造消息并追加（带 _sender 字段）──
    # 发送方标识写入消息内容（LLM API 可能丢弃非标准字段，所以不能只靠 _sender 元数据）
    _sender_tag = sender_fields.get("_sender", "")
    _sender_name = sender_fields.get("_sender_name", "")
    _thread = str(thread_id or _kwargs.get("thread_id") or "").strip()
    _channel = str(channel or "direct").strip() or "direct"
    _meta_parts = []
    if _sender_tag:
        _meta_parts.append(f"from={_sender_name or _sender_tag}")
        _meta_parts.append(f"sender_cid={_sender_tag}")
    if _thread:
        _meta_parts.append(f"thread_id={_thread}")
    if _channel:
        _meta_parts.append(f"channel={_channel}")
    if requires_reply_bool:
        _meta_parts.append("requires_reply=true")
    _tag = ("[" + " | ".join(_meta_parts) + "]\n") if _meta_parts else ""
    user_msg: Dict[str, Any] = {
        "role": "user",
        "content": f"{_tag}{msg}",
        "_priority": str(priority or "normal"),
        "_agent_peer_message": True,
        "_message_kind": "peer",
        "_channel": _channel,
        "_thread_id": _thread,
        "_requires_reply": requires_reply_bool,
    }
    user_msg.update(sender_fields)

    # ── 在发送者会话中标记已发出 requires_reply=true ──
    if requires_reply_bool and src_cid and src_cid != tid:
        _ensure_conversation_loaded(src_cid)
        src_msgs = CONVERSATIONS.get(src_cid)
        if src_msgs is not None:
            sentinel = {
                "role": "system",
                "content": "",
                "_requires_reply_sentinel": True,
                "_target_id": tid,
                "_thread_id": _thread,
            }
            _append_session_message_v2(src_cid, src_msgs, sentinel, new_round=False)
            CONVERSATIONS[src_cid] = src_msgs
            _save_conversation(src_cid, src_msgs)

    if _ACTIVE_CONVERSATION_RUNS.get(tid):
        queued = enqueue_session_inbox(tid, user_msg)
        publish_conversation_event(
            tid,
            {
                "type": "inbox_queued",
                "queued_count": queued,
                "from": sender_fields.get("_sender", ""),
                "from_name": sender_fields.get("_sender_name", ""),
            },
        )
        return {
            "ok": True,
            "data": {
                "target_id": tid,
                "queued": True,
                "queued_count": queued,
                "message_sent": msg[:200] if len(msg.encode("utf-8")) <= 200 else msg.encode("utf-8")[:200].decode("utf-8", "ignore"),
                "_sender": sender_fields,
            },
        }

    _append_incoming_session_message(tid, user_msg)
    run_id = start_background_agent_turn(tid, "", resume_after_user_confirm=True)

    return {
        "ok": True,
        "data": {
            "target_id": tid,
            "queued": False,
            "run_id": run_id,
            "message_sent": msg[:200] if len(msg.encode("utf-8")) <= 200 else msg.encode("utf-8")[:200].decode("utf-8", "ignore"),
            "_sender": sender_fields,
        },
    }
