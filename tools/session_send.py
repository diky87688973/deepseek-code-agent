# -*- coding: utf-8 -*-
"""session_send 工具：Agent 间互发消息，触发接收方执行。"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import agent_common as ac


def _get_last_assistant_content(messages: list) -> str:
    """从消息列表中提取最后一条 assistant 的 content。"""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            return str(m.get("content") or "")
    return ""


def agent_main(
    *,
    target_id: str = "",
    message: str = "",
    priority: str = "normal",
    thread_id: str = "",
    requires_reply: Optional[bool] = None,
    channel: str = "direct",
    **_kwargs: Any,
) -> Dict[str, Any]:
    """session_send 入口：向目标 Agent 发消息并触发执行（无 action 参数）。"""
    _kwargs.pop("action", None)

    tid = str(target_id or "").strip()
    msg = str(message or "").strip()
    if not tid:
        return {"ok": False, "error": {"type": "missing_target", "message": "缺少 target_id"}}
    if not msg:
        return {"ok": False, "error": {"type": "missing_message", "message": "缺少 message"}}

    _rr_raw = _kwargs.get("requires_reply", requires_reply)
    if _rr_raw is None:
        return {
            "ok": False,
            "error": {
                "type": "missing_requires_reply",
                "message": "requires_reply 是必填参数。收到消息后，根据对方消息中的 requires_reply 元数据决定传 true（需回复）或 false（纯通知）。",
            },
        }
    requires_reply_bool = ac.parse_tool_bool(_rr_raw, True)
    from agent_v3.live_state import (
        CONVERSATIONS,
        _ACTIVE_CONVERSATION_RUNS,
        conversation_run_locks,
        enqueue_session_inbox,
        get_agent_session,
    )
    from agent_v3.agent_core import (
        _append_incoming_session_message_impl,
        _append_session_message_v2,
        _ensure_conversation_loaded,
        _save_conversation,
        publish_conversation_event,
        start_background_agent_turn,
    )

    src_cid = str(_kwargs.get("conversation_id") or "").strip()
    if not src_cid:
        return {
            "ok": False,
            "error": {
                "type": "missing_conversation",
                "message": "缺少 conversation_id（发送方会话 id），无法标识 Agent 身份。",
            },
        }

    sender_fields: Dict[str, str] = {}
    meta = get_agent_session(src_cid)
    if meta:
        sender_fields["_sender"] = src_cid
        sender_fields["_sender_role"] = str(meta.get("role") or "")
        sender_fields["_sender_name"] = str(meta.get("name") or "")
    else:
        sender_fields["_sender"] = src_cid
        sender_fields["_sender_role"] = "agent"
        sender_fields["_sender_name"] = src_cid[:12]

    _sender_tag = sender_fields.get("_sender", "")
    _sender_name = sender_fields.get("_sender_name", "")
    _thread_raw = str(thread_id or _kwargs.get("thread_id") or "").strip()
    if not _thread_raw:
        _prefix = (src_cid[:8] if len(src_cid) >= 8 else src_cid)[:8]
        _thread_raw = f"auto-{_prefix}-{int(time.time())}"
    _thread = _thread_raw
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
    preview = ac.utf8_preview(msg, 200)

    if requires_reply_bool and src_cid != tid:
        with conversation_run_locks(src_cid):
            _ensure_conversation_loaded(src_cid)
            src_msgs = CONVERSATIONS.get(src_cid)
            if src_msgs is not None:
                sentinel = {
                    "role": "system",
                    "content": "_requires_reply_sentinel",
                    "_requires_reply_sentinel": True,
                    "_target_id": tid,
                    "_thread_id": _thread,
                }
                _append_session_message_v2(src_cid, src_msgs, sentinel, new_round=False)
                CONVERSATIONS[src_cid] = src_msgs
                _save_conversation(src_cid, src_msgs)

    queued = False
    turn_started = False
    with conversation_run_locks(tid):
        _ensure_conversation_loaded(tid)
        target_messages = CONVERSATIONS.get(tid)
        if not target_messages:
            return {"ok": False, "error": {"type": "target_not_found", "message": f"目标会话 {tid} 不存在"}}

        if _ACTIVE_CONVERSATION_RUNS.get(tid):
            queued_count = enqueue_session_inbox(tid, user_msg)
            publish_conversation_event(
                tid,
                {
                    "type": "inbox_queued",
                    "queued_count": queued_count,
                    "from": sender_fields.get("_sender", ""),
                    "from_name": sender_fields.get("_sender_name", ""),
                },
            )
            queued = True
        else:
            turn_started = _append_incoming_session_message_impl(tid, user_msg)

    if queued:
        return {
            "ok": True,
            "data": {
                "target_id": tid,
                "queued": True,
                "queued_count": queued_count,
                "message_sent": preview,
                "_sender": sender_fields,
            },
        }

    run_id = str(_ACTIVE_CONVERSATION_RUNS.get(tid) or "")
    if not turn_started and not run_id:
        run_id = start_background_agent_turn(
            tid, "", resume_after_user_confirm=True, peer_triggered=True
        ) or ""

    return {
        "ok": True,
        "data": {
            "target_id": tid,
            "queued": False,
            "run_id": run_id,
            "message_sent": preview,
            "_sender": sender_fields,
        },
    }


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    p = argparse.ArgumentParser(description="session_send：人工调试 CLI → agent_main（无 --action）")
    p.add_argument("--conversation_id", required=True, help="发送方会话 ID")
    p.add_argument("--target_id", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--requires_reply", required=True, help="true 或 false")
    p.add_argument("--thread_id", default="")
    p.add_argument("--channel", default="direct")
    p.add_argument("--priority", default="normal")
    p.add_argument("--json_out", action="store_true")
    return p


def main() -> None:
    import json
    import sys

    args = build_parser().parse_args()
    r = agent_main(
        target_id=args.target_id,
        message=args.message,
        requires_reply=args.requires_reply,
        thread_id=args.thread_id,
        channel=args.channel,
        priority=args.priority,
        conversation_id=args.conversation_id,
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok"):
            print(json.dumps(r.get("data"), ensure_ascii=False, indent=2))
        else:
            err = r.get("error") or {}
            print(err.get("message", r), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
