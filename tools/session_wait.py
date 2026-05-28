# -*- coding: utf-8 -*-
"""session_wait 工具：非阻塞检查指定 Agent 是否已回复某个 thread。"""
from __future__ import annotations

from typing import Any, Dict, List

import agent_common as ac


def _normalize_target_ids(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw or "").strip()
    if not s:
        return []
    return [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]


def _sentinel_matches_wait_thread(sentinel_thread: str, wait_thread: str) -> bool:
    """wait 未指定 thread 时匹配任意哨兵；指定时须与 send 时 thread_id 一致（send 未写 thread 视为通配）。"""
    wt = str(wait_thread or "").strip()
    st = str(sentinel_thread or "").strip()
    if not wt:
        return True
    if not st:
        return True
    return st == wt


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
        if not _sentinel_matches_wait_thread(th, thread):
            continue
        out[t] = i
    return out


def _collect_sentinel_hints(msgs: List[Dict[str, Any]], targets: List[str]) -> List[str]:
    """诊断：本会话已记录的 requires_reply 哨兵（便于 thread/target 对不上时自查）。"""
    hints: List[str] = []
    want = set(targets)
    for m in msgs:
        if not isinstance(m, dict) or not m.get("_requires_reply_sentinel"):
            continue
        t = str(m.get("_target_id") or "").strip()
        if want and t not in want:
            continue
        th = str(m.get("_thread_id") or "").strip() or "(无)"
        hints.append(f"{t}@thread={th}")
    return hints[:8]


def agent_main(
    *,
    target_ids: Any = None,
    thread_id: str = "",
    suspend: Any = None,
    sender_cid: str = "",
    **_kwargs: Any,
) -> Dict[str, Any]:
    """检查 target_ids 是否已回复；pending 时可 suspend 挂起（无 action 参数）。"""
    _kwargs.pop("action", None)
    targets = _normalize_target_ids(target_ids)
    if not targets:
        return {"ok": False, "error": {"type": "missing_targets", "message": "缺少 target_ids"}}
    thread = str(thread_id or _kwargs.get("thread_id") or "").strip()
    cid = str(sender_cid or _kwargs.get("conversation_id") or "").strip()
    if not cid:
        return {"ok": False, "error": {"type": "missing_conversation", "message": "缺少 conversation_id（当前会话未指定；跨会话等待请传 sender_cid）"}}
    from agent_v3.live_state import CONVERSATIONS
    from agent_v3.agent_core import _ensure_conversation_loaded

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
            if m_target == t and _sentinel_matches_wait_thread(m_thread, thread):
                _sent_reply_request[t] = True
    _no_request_targets = [t for t in targets if not _sent_reply_request.get(t)]
    if _no_request_targets:
        msg_suffix = f"（thread_id={thread!r}）" if thread else ""
        hint_list = _collect_sentinel_hints(msgs, targets)
        hint_extra = ""
        if hint_list:
            hint_extra = f" 本会话已有哨兵：{', '.join(hint_list)}。如跨会话等待请传 sender_cid。"
        elif thread:
            hint_extra = " 本会话尚无匹配哨兵；请确认 send 与 wait 使用同一 conversation_id，或传 sender_cid 指定发送方会话。"
        return {
            "ok": False,
            "error": {
                "type": "wait_without_request",
                "message": (
                    f"目标 {'/'.join(_no_request_targets)} 未在本会话记录到 requires_reply=true 的发送{msg_suffix}。"
                    "须先 session_send/session_multisend（requires_reply=true，thread_id 与 wait 一致）。"
                    f"{hint_extra}"
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
        _suspend_raw = suspend if suspend is not None else _kwargs.get("suspend")
        want_suspend = ac.parse_tool_bool(_suspend_raw, True)
        if want_suspend:
            from agent_v3.live_state import suspend_agent_wait

            wait_state = suspend_agent_wait(cid, targets, thread)
            data["suspend"] = True
            data["wait_state"] = wait_state
        else:
            data["suspend"] = False
    return {"ok": True, "data": data}


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    p = argparse.ArgumentParser(description="session_wait：人工调试 CLI → agent_main（无 --action）")
    p.add_argument("--conversation_id", required=True, help="发送方（等待方）会话 ID")
    p.add_argument("--target_ids", required=True, help="逗号分隔")
    p.add_argument("--thread_id", default="")
    p.add_argument("--suspend", default="", help="true/false，pending 时是否挂起")
    p.add_argument("--sender_cid", default="", help="跨会话等待时指定发送方 conversation_id")
    p.add_argument("--json_out", action="store_true")
    return p


def main() -> None:
    import json
    import sys

    args = build_parser().parse_args()
    suspend_val = args.suspend if str(args.suspend or "").strip() else None
    r = agent_main(
        target_ids=args.target_ids,
        thread_id=args.thread_id,
        suspend=suspend_val,
        sender_cid=str(args.sender_cid or "").strip(),
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
