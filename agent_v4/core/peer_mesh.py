# -*- coding: utf-8
"""agent_v4.core.peer_mesh"""
from __future__ import annotations

from agent_v4.core.deps import *  # noqa: F403
from agent_v4.core.shared_state import *  # noqa: F403

def _api_messages_with_ephemeral_tail(
    base: List[Dict[str, Any]], tail: Optional[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not tail:
        return base
    return base + [dict(tail)]

def _apply_inbound_requires_reply_answered(
    conversation_id: str,
    messages: List[Dict[str, Any]],
    turn_rr_state: Dict[str, Optional[Dict[str, Any]]],
    target_ids: Iterable[str],
    *,
    thread_id: str = "",
) -> bool:
    """入站 requires_reply=true 的应答：任意成功的 session_* 命中 sender 即标记，并清 ephemeral tail。"""
    if not _mark_requires_reply_answered_for_senders(
        messages, target_ids, thread_id=thread_id
    ):
        return False
    CONVERSATIONS[conversation_id] = messages
    _save_conversation(conversation_id, messages)
    # 仅清除 requires_reply 优先级；截图等其它 ephemeral 由调用方 _sync_ephemeral_tail 重建
    turn_rr_state["tail"] = None
    return True

def _ephemeral_requires_reply_priority(peer_cid: str, thread_id: str = "") -> Dict[str, Any]:
    """与执行模式 tail 相同：仅拼入当次 API 请求，不写入会话持久化。"""
    return {"role": "system", "content": ephemeral_requires_reply_priority_prompt(peer_cid, thread_id)}

def _exec_requires_reply_true(exec_args: Dict[str, Any]) -> bool:
    v = exec_args.get("requires_reply")
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")

def _extract_reply_tool_target_ids(
    script: str, exec_args: Dict[str, Any], result: dict
) -> List[str]:
    if not isinstance(result, dict) or not result.get("ok"):
        return []
    api = script[:-3] if script.endswith(".py") else script
    if api == "session_send":
        tid = str(exec_args.get("target_id") or "").strip()
        return [tid] if tid else []
    data = result.get("data")
    if not isinstance(data, dict):
        return []
    return _normalize_sent_target_ids(data.get("sent"))

def _find_pending_requires_reply_peer_message(
    messages: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """最近一条尚未应答的 peer requires_reply 入站消息。"""
    for m in reversed(messages):
        if not isinstance(m, dict):
            continue
        if str(m.get("role") or "") != "user":
            continue
        if not m.get("_requires_reply"):
            continue
        if not m.get("_agent_peer_message"):
            continue
        sender = str(m.get("_sender") or "").strip()
        if sender in ("", "boss"):
            continue
        if m.get("_requires_reply_answered"):
            continue
        return m
    return None

def _mark_requires_reply_answered_for_senders(
    messages: List[Dict[str, Any]],
    sender_cids: Iterable[str],
    *,
    thread_id: str = "",
) -> bool:
    """对方会话 ID 与入站 _sender 一致时，标记 requires_reply 已应答（可选 thread 粒度）。"""
    targets = {str(s).strip() for s in sender_cids if str(s).strip()}
    if not targets:
        return False
    thread = str(thread_id or "").strip()
    changed = False
    for m in messages:
        if not isinstance(m, dict) or str(m.get("role") or "") != "user":
            continue
        if not m.get("_requires_reply"):
            continue
        sender = str(m.get("_sender") or "").strip()
        if sender not in targets or m.get("_requires_reply_answered"):
            continue
        if thread:
            msg_thread = str(m.get("_thread_id") or "").strip()
            if msg_thread and msg_thread != thread:
                continue
        m["_requires_reply_answered"] = True
        changed = True
    return changed

def _normalize_sent_target_ids(sent: Any) -> List[str]:
    if not isinstance(sent, list):
        return []
    out: List[str] = []
    for x in sent:
        if isinstance(x, dict):
            tid = str(x.get("target_id") or "").strip()
        else:
            tid = str(x).strip()
        if tid:
            out.append(tid)
    return out

def _turn_replied_to_peer(turn_tool_records: List[Dict[str, Any]], peer_cid: str) -> bool:
    """本回合是否已用 session_* 工具成功回复指定 peer（与出站 requires_reply 无关）。"""
    if not peer_cid:
        return False
    for r in turn_tool_records:
        if not r.get("ok"):
            continue
        api = str(r.get("api_name") or "").replace(".py", "")
        if api not in _PEER_REPLY_TOOL_APIS:
            continue
        tids = r.get("target_ids") or []
        if peer_cid in tids:
            return True
    return False

