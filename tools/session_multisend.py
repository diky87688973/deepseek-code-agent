# -*- coding: utf-8 -*-
"""session_multisend 工具：向指定多个 Agent 发送同一条消息。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools.agent_common import parse_tool_bool


def _normalize_target_ids(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw or "").strip()
    if not s:
        return []
    return [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]


def agent_main(
    *,
    action: str = "send",
    target_ids: Any = None,
    message: str = "",
    channel: str = "group",
    thread_id: str = "",
    priority: str = "normal",
    requires_reply: Optional[bool] = None,
    **_kwargs: Any,
) -> Dict[str, Any]:
    action = str(action or "send").strip().lower()
    if action not in ("send", "multisend"):
        return {"ok": False, "error": {"type": "unknown_action", "message": "可选 action: send/multisend"}}
    ids = _normalize_target_ids(target_ids)
    msg = str(message or "").strip()
    if not ids:
        return {"ok": False, "error": {"type": "missing_targets", "message": "缺少 target_ids"}}
    if not msg:
        return {"ok": False, "error": {"type": "missing_message", "message": "缺少 message"}}

    # requires_reply 为必填参数
    _rr = _kwargs.get("requires_reply", requires_reply)
    if _rr is None:
        return {"ok": False, "error": {"type": "missing_requires_reply", "message": "requires_reply 是必填参数，必须显式设置 true 或 false。"}}
    from tools.session_send import agent_main as _send

    sent, skipped = [], []
    for tid in ids:
        r = _send(
            action="send",
            target_id=tid,
            message=msg,
            channel=channel,
            thread_id=str(thread_id or _kwargs.get("thread_id") or ""),
            priority=str(priority or _kwargs.get("priority") or "normal"),
            requires_reply=parse_tool_bool(_rr, True),
            conversation_id=str(_kwargs.get("conversation_id") or ""),
        )
        if r.get("ok"):
            sent.append({"target_id": tid, "queued": bool((r.get("data") or {}).get("queued"))})
        else:
            skipped.append({"target_id": tid, "error": r.get("error")})
    if not sent:
        return {
            "ok": False,
            "error": {
                "type": "all_targets_failed",
                "message": "所有目标发送均失败，请检查 target_ids 与网络状态。",
            },
            "data": {"sent": sent, "skipped": skipped, "count": 0, "thread_id": str(thread_id or ""), "partial": False, "all_sent": False},
        }
    return {
        "ok": True,
        "data": {
            "sent": sent,
            "skipped": skipped,
            "count": len(sent),
            "thread_id": str(thread_id or ""),
            "partial": bool(skipped),
            "all_sent": not skipped,
        },
    }
