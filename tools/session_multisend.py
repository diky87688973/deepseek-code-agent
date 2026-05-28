# -*- coding: utf-8 -*-
"""session_multisend 工具：向指定多个 Agent 发送同一条消息。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import agent_common as ac


def _normalize_target_ids(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw or "").strip()
    if not s:
        return []
    return [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]


def agent_main(
    *,
    target_ids: Any = None,
    message: str = "",
    channel: str = "group",
    thread_id: str = "",
    priority: str = "normal",
    requires_reply: Optional[bool] = None,
    **_kwargs: Any,
) -> Dict[str, Any]:
    """向多个 target_ids 发送同一条 message（无 action 参数）。"""
    _kwargs.pop("action", None)
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
    from session_send import agent_main as _send

    sent, skipped = [], []
    for tid in ids:
        r = _send(
            target_id=tid,
            message=msg,
            channel=channel,
            thread_id=str(thread_id or _kwargs.get("thread_id") or ""),
            priority=str(priority or _kwargs.get("priority") or "normal"),
            requires_reply=ac.parse_tool_bool(_rr, True),
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


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    p = argparse.ArgumentParser(description="session_multisend：人工调试 CLI → agent_main（无 --action）")
    p.add_argument("--conversation_id", required=True)
    p.add_argument("--target_ids", required=True, help="逗号分隔的会话 ID")
    p.add_argument("--message", required=True)
    p.add_argument("--requires_reply", required=True, help="true 或 false")
    p.add_argument("--thread_id", default="")
    p.add_argument("--channel", default="group")
    p.add_argument("--priority", default="normal")
    p.add_argument("--json_out", action="store_true")
    return p


def main() -> None:
    import json
    import sys

    args = build_parser().parse_args()
    r = agent_main(
        target_ids=args.target_ids,
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
