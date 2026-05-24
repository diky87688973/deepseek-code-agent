# -*- coding: utf-8 -*-
"""session_broadcast 工具：向自由 Agent 网络广播消息。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from tools.agent_common import parse_tool_bool


def agent_main(
    *,
    action: str = "",
    role: str = "",
    tag: str = "",
    exclude_self: bool = True,
    message: str = "",
    requires_reply: Optional[bool] = None,
    thread_id: str = "",
    priority: str = "normal",
    **_kwargs: Any,
) -> Dict[str, Any]:
    """session_broadcast 入口。action=broadcast 向所有/筛选 Agent 群发。
    requires_reply 控制收信方是否被标记为需要回复，必填参数。"""
    action = str(action or "").strip().lower()
    if not action:
        return {"ok": False, "error": {"type": "invalid_args", "message": "缺少 action 参数。可选: broadcast"}}
    if action != "broadcast":
        return {"ok": False, "error": {"type": "unknown_action", "message": f"未知 action: {action}。可选: broadcast"}}
    msg = str(message or "").strip()
    if not msg:
        return {"ok": False, "error": {"type": "missing_message", "message": "缺少 message"}}

    # requires_reply 为必填参数
    _rr = _kwargs.get("requires_reply", requires_reply)
    if _rr is None:
        return {"ok": False, "error": {"type": "missing_requires_reply", "message": "requires_reply 是必填参数，必须显式设置 true 或 false。"}}

    # tag 为必填参数：广播必须按 tag 过滤目标群组，禁止全员广播
    target_tag = str(tag or "").strip()
    if not target_tag:
        return {"ok": False, "error": {"type": "missing_tag", "message": "session_broadcast 必须传 tag 指定目标群组（如 tag=\"spy-game\"），禁止全员广播。"}}

    from agent_v2.live_state import list_agent_sessions
    from tools.session_send import agent_main as _send

    src_cid = str(_kwargs.get("conversation_id") or "").strip()
    target_role = str(role or "").strip()
    sent, skipped = [], []
    members = list_agent_sessions()
    for mid, info in members.items():
        if parse_tool_bool(exclude_self, True) and mid == src_cid:
            continue
        if target_role and info.get("role") != target_role:
            continue
        if target_tag:
            tags = info.get("tags") or []
            if target_tag not in tags:
                continue
        r = _send(
            action="send",
            target_id=mid,
            message=msg,
            channel="broadcast",
            thread_id=str(thread_id or _kwargs.get("thread_id") or ""),
            priority=str(priority or _kwargs.get("priority") or "normal"),
            conversation_id=src_cid,
            requires_reply=parse_tool_bool(_rr, True),
        )
        if r.get("ok"):
            sent.append(mid)
        else:
            skipped.append({"target_id": mid, "error": r.get("error")})
            continue
    if not sent:
        return {
            "ok": False,
            "error": {"type": "all_targets_failed", "message": "广播未送达任何目标，请检查 tag/role 筛选条件。"},
            "data": {"sent": sent, "skipped": skipped, "count": 0, "partial": bool(skipped), "all_sent": False},
        }
    return {"ok": True, "data": {"sent": sent, "skipped": skipped, "count": len(sent), "partial": bool(skipped), "all_sent": not skipped}}
