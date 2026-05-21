# -*- coding: utf-8 -*-
"""session_broadcast 工具：向自由 Agent 网络广播消息。"""
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


def agent_main(
    *,
    action: str = "",
    role: str = "",
    tag: str = "",
    exclude_self: bool = True,
    message: str = "",
    requires_reply: Optional[bool] = None,
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

    from agent_v2.live_state import list_agent_sessions
    from tools.session_send import agent_main as _send

    src_cid = str(_kwargs.get("conversation_id") or "").strip()
    target_role = str(role or "").strip()
    target_tag = str(tag or "").strip()
    sent, skipped = [], []
    members = list_agent_sessions()
    for mid, info in members.items():
        if _as_bool(exclude_self, True) and mid == src_cid:
            continue
        if target_role and info.get("role") != target_role:
            continue
        if target_tag:
            tags = info.get("tags") or []
            if target_tag not in tags:
                continue
        r = _send(action="send", target_id=mid, message=msg, channel="broadcast",
                  conversation_id=src_cid, requires_reply=_as_bool(_rr, True))
        if r.get("ok"):
            sent.append(mid)
        else:
            skipped.append({"target_id": mid, "error": r.get("error")})
            continue
    return {"ok": True, "data": {"sent": sent, "skipped": skipped, "count": len(sent)}}
