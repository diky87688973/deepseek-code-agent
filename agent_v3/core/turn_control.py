# -*- coding: utf-8
"""agent_v3.core.turn_control"""
from __future__ import annotations

from agent_v3.core.deps import *  # noqa: F403
from agent_v3.core.shared_state import *  # noqa: F403

def _ensure_conversation_loaded(cid: str) -> None:
    """加载到内存；优先从磁盘读取以捕获后台线程的异步落盘。"""
    key = str(cid or "").strip()
    if not key:
        return
    with get_conversation_run_lock(key):
        loaded = _load_conversation(key)
        if loaded:
            CONVERSATIONS[key] = loaded
        elif key not in CONVERSATIONS:
            CONVERSATIONS[key] = []

def _finish_conversation_stopped(
    cid: str,
    messages: List[Dict[str, Any]],
    *,
    round_id: Optional[str] = None,
    run_id: str = "",
) -> Dict[str, Any]:
    """用户停止：保留本轮已追加内容，为未执行工具补占位结果后落盘。"""
    # 清理该会话的预览缓存
    try:
        from agent_v3.core.agent_turn import _CONVERSATION_PREVIEWED
        _CONVERSATION_PREVIEWED.pop(cid, None)
    except Exception:
        pass
    _pad_trailing_missing_tool_results_for_user_stop(cid, messages, round_id=round_id)
    _normalize_persisted_conversation(messages)
    CONVERSATIONS[cid] = messages
    _save_conversation(cid, messages)
    if run_id:
        _consume_conversation_stop_requested(cid, run_id)
    return {"type": "stopped", "message": "任务已停止"}

def _pad_trailing_missing_tool_results_for_user_stop(
    cid: str,
    messages: List[Dict[str, Any]],
    *,
    round_id: Optional[str] = None,
) -> int:
    """为尾部未执行完的 tool_calls 补 ok=false 占位，避免下轮 API 因缺 tool 消息拒请求。"""
    padded = 0
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") != "assistant" or not isinstance(m.get("tool_calls"), list) or not m["tool_calls"]:
            continue
        need_ids = _assistant_tool_call_ids(m)
        if not need_ids:
            continue
        j = i + 1
        answered: Set[str] = set()
        while j < len(messages) and messages[j].get("role") == "tool":
            tid = str(messages[j].get("tool_call_id") or "")
            if tid in need_ids:
                answered.add(tid)
            j += 1
        missing = [tid for tid in sorted(need_ids) if tid not in answered]
        if not missing:
            return padded
        for tid in missing:
            _append_session_message_v2(
                cid,
                messages,
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": _user_stopped_tool_content(),
                },
                round_id=round_id,
            )
            padded += 1
        return padded
    return padded

def _turn_abort_requested(conversation_id: str, run_id: str = "") -> bool:
    return server_shutting_down() or _peek_conversation_stop_requested(conversation_id, run_id)

def _user_stopped_tool_content() -> str:
    return json.dumps(_user_stopped_tool_result_dict(), ensure_ascii=False)

def _user_stopped_tool_result_dict() -> Dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {"type": "Aborted", "message": USER_STOPPED_TOOL_MESSAGE},
    }

