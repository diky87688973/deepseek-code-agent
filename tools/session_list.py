# -*- coding: utf-8 -*-
"""session_list 工具：列出自由 Agent 会话。"""
from __future__ import annotations

from typing import Any, Dict


def agent_main(*, action: str = "list", **_kwargs: Any) -> Dict[str, Any]:
    action = str(action or "list").strip().lower()
    if action != "list":
        return {"ok": False, "error": {"type": "unknown_action", "message": "可选 action: list"}}
    from agent_v2.live_state import _ACTIVE_CONVERSATION_RUNS, list_agent_sessions, session_inbox_size

    agents = []
    for cid, meta in sorted(list_agent_sessions().items(), key=lambda kv: str(kv[1].get("name") or kv[0])):
        qn = session_inbox_size(cid)
        status = "running" if cid in _ACTIVE_CONVERSATION_RUNS else ("queued" if qn else str(meta.get("status") or "idle"))
        row = dict(meta)
        row.update({"cid": cid, "session_id": cid, "status": status, "queueSize": qn})
        agents.append(row)
    return {"ok": True, "data": {"agents": agents, "count": len(agents)}}
