# -*- coding: utf-8 -*-
"""session_broadcast 工具：向自由 Agent 网络广播消息。"""
from __future__ import annotations

from typing import Any, Dict, Optional

import agent_common as ac


def agent_main(
    *,
    role: str = "",
    tag: str = "",
    exclude_self: bool = True,
    message: str = "",
    requires_reply: Optional[bool] = None,
    thread_id: str = "",
    priority: str = "normal",
    **_kwargs: Any,
) -> Dict[str, Any]:
    """session_broadcast 入口：按 tag/role 筛选后群发（无 action 参数）。requires_reply 必填。"""
    _kwargs.pop("action", None)
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

    from agent_v3.live_state import list_agent_sessions
    from session_send import agent_main as _send

    src_cid = str(_kwargs.get("conversation_id") or "").strip()
    target_role = str(role or "").strip()
    sent, skipped = [], []
    members = list_agent_sessions()
    for mid, info in members.items():
        if ac.parse_tool_bool(exclude_self, True) and mid == src_cid:
            continue
        if target_role and info.get("role") != target_role:
            continue
        if target_tag:
            tags = info.get("tags") or []
            if target_tag not in tags:
                continue
        r = _send(
            target_id=mid,
            message=msg,
            channel="broadcast",
            thread_id=str(thread_id or _kwargs.get("thread_id") or ""),
            priority=str(priority or _kwargs.get("priority") or "normal"),
            conversation_id=src_cid,
            requires_reply=ac.parse_tool_bool(_rr, True),
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


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    p = argparse.ArgumentParser(description="session_broadcast：人工调试 CLI → agent_main（无 --action）")
    p.add_argument("--conversation_id", required=True)
    p.add_argument("--tag", required=True, help="目标群组标签，禁止空值全员广播")
    p.add_argument("--message", required=True)
    p.add_argument("--requires_reply", required=True, help="true 或 false")
    p.add_argument("--role", default="")
    p.add_argument("--thread_id", default="")
    p.add_argument("--priority", default="normal")
    p.add_argument("--include_self", action="store_true", help="默认排除发送者自己；加此开关则包含自己")
    p.add_argument("--json_out", action="store_true")
    return p


def main() -> None:
    import json
    import sys

    args = build_parser().parse_args()
    r = agent_main(
        role=args.role,
        tag=args.tag,
        exclude_self=not args.include_self,
        message=args.message,
        requires_reply=args.requires_reply,
        thread_id=args.thread_id,
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
