# -*- coding: utf-8 -*-
"""session_list 工具：列出自由 Agent 会话。"""
from __future__ import annotations

from typing import Any, Dict


def agent_main(**_kwargs: Any) -> Dict[str, Any]:
    """列出 Session Mesh 中自由 Agent（无 action 参数）。"""
    _kwargs.pop("action", None)
    from agent_v3.live_state import _ACTIVE_CONVERSATION_RUNS, list_agent_sessions, session_inbox_size

    agents = []
    for cid, meta in sorted(list_agent_sessions().items(), key=lambda kv: str(kv[1].get("name") or kv[0])):
        qn = session_inbox_size(cid)
        status = "running" if cid in _ACTIVE_CONVERSATION_RUNS else ("queued" if qn else str(meta.get("status") or "idle"))
        row = dict(meta)
        row.update({"cid": cid, "session_id": cid, "status": status, "queueSize": qn})
        agents.append(row)
    return {"ok": True, "data": {"agents": agents, "count": len(agents)}}


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    p = argparse.ArgumentParser(description="session_list：人工调试 CLI → agent_main（无参数）")
    p.add_argument("--json_out", action="store_true")
    return p


def main() -> None:
    import json
    import sys

    args = build_parser().parse_args()
    r = agent_main()
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
