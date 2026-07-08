# -*- coding: utf-8
"""agent_v3.core.turn_runner"""
from __future__ import annotations

from agent_v3.core import base as _core_base

for _k, _v in vars(_core_base).items():
    if not _k.startswith("__"):
        globals()[_k] = _v

def _append_incoming_session_message(target_id: str, user_msg: Dict[str, Any]) -> bool:
    """追加 peer 入站消息。若因 wait 恢复已启动 turn 则返回 True。"""
    cid = str(target_id or "").strip()
    with conversation_run_locks(cid):
        return _append_incoming_session_message_impl(cid, user_msg)

def _append_incoming_session_message_impl(target_id: str, user_msg: Dict[str, Any]) -> bool:
    """追加 peer 入站（无锁；调用方须已持有 conversation_run_locks(cid) 或保证单线程）。"""
    cid = str(target_id or "").strip()
    _ensure_conversation_loaded(cid)
    target_messages = CONVERSATIONS.get(cid)
    if not target_messages:
        raise ValueError(f"目标会话 {cid} 不存在")
    _append_session_message_v2(cid, target_messages, dict(user_msg), new_round=True)
    CONVERSATIONS[cid] = target_messages
    _save_conversation(cid, target_messages)
    turn_started = False
    if user_msg.get("_agent_peer_message"):
        sender = str(user_msg.get("_sender") or "")
        thread = str(user_msg.get("_thread_id") or "")
        publish_conversation_event(
            cid,
            {
                "type": "peer_message",
                "content": str(user_msg.get("content") or ""),
                "sender": sender,
                "sender_name": str(user_msg.get("_sender_name") or ""),
                "sender_role": str(user_msg.get("_sender_role") or ""),
            },
        )
        waits = pop_waits_satisfied_by(sender, cid, thread)
        for wait in waits:
            publish_conversation_event(
                cid,
                {
                    "type": "agent_wait_resumed",
                    "from": sender,
                    "thread_id": thread,
                },
            )
            start_background_agent_turn(cid, "", resume_after_user_confirm=True, peer_triggered=True)
            turn_started = True
            break
    return turn_started

def _drain_session_inbox_after_run(conversation_id: str) -> None:
    cid = str(conversation_id or "").strip()
    if not cid:
        return
    turn_started = False
    appended = 0
    while True:
        msg = pop_session_inbox(cid)
        if not msg:
            break
        try:
            if _append_incoming_session_message(cid, msg):
                turn_started = True
            appended += 1
        except Exception as exc:
            publish_conversation_event(cid, {"type": "error", "where": "session_inbox", "detail": str(exc)})
            return
    if not appended:
        return
    publish_conversation_event(
        cid,
        {
            "type": "inbox_dequeued",
            "queued_remaining": len(SESSION_INBOX.get(cid, []) or []),
            "batch_count": appended,
        },
    )
    if not turn_started:
        start_background_agent_turn(cid, "", resume_after_user_confirm=True, peer_triggered=True)

def publish_conversation_event(conversation_id: str, ev: Dict[str, Any]) -> Dict[str, Any]:
    ev2 = _conversation_sse_event(conversation_id, ev)
    _log_agent_console_sse(conversation_id, ev2)
    publish_global_sse_event(ev2)
    # ── TTS ──
    et = ev.get("type", "")
    
    # 根据发送者选择音色
    sender = ev.get("_sender", {}) or {}
    sender_cid = str(sender.get("_sender") or ev.get("sender") or "").strip()
    sender_name = str(sender.get("_sender_name") or ev.get("sender_name") or "").strip()
    from util.tts.manager import voice_for, feed_delta, flush_remaining
    voice = voice_for(sender_cid, sender_name)
    _dbg = f"{sender_cid[:20]}|{sender_name}" if sender_cid or sender_name else ""
    
    if et == "assistant_delta":
        content = str(ev.get("content") or ev.get("delta") or "")
        if content:
            try:
                feed_delta(conversation_id, content, voice=voice, _dbg_sender=_dbg)
            except Exception as exc:
                import sys
                print(f"[TTS] feed_delta 失败: {exc}", file=sys.stderr, flush=True)
    elif et == "peer_message":
        content = str(ev.get("content") or "")
        if content:
            if content.startswith("[") and "]" in content:
                ci = content.index("]")
                content = content[ci + 1:].strip()
            if content:
                # peer 消息可能缺 sender，用 conversation_id 兜底确定音色
                if not sender_cid:
                    sender_cid = conversation_id
                    voice = voice_for(sender_cid, sender_name)
                    _dbg = f"{sender_cid[:20]}|{sender_name}" if sender_cid or sender_name else ""
                if not re.search(r"[。！？，、：；!?,:;\n]$", content):
                    content += "。"
                try:
                    feed_delta(conversation_id, content, voice=voice, _dbg_sender=_dbg)
                except Exception as exc:
                    import sys
                    print(f"[TTS] peer 消息 TTS 失败: {exc}", file=sys.stderr, flush=True)
    elif et in ("done", "error", "stopped") or et.startswith("agent_wait"):
        try:
            flush_remaining(conversation_id)
        except Exception as exc:
            import sys
            print(f"[TTS] flush_remaining 失败: {exc}", file=sys.stderr, flush=True)
    return ev2

def start_background_agent_turn(
    conversation_id: str,
    user_text: str = "",
    *,
    client_ip: str = "",
    mode_hint: str = "",
    resume_after_user_confirm: bool = False,
    peer_triggered: bool = False,
) -> str:
    """后台运行一个会话 turn，所有事件发布到页面级全局 SSE。"""
    cid = str(conversation_id or "").strip()
    if not cid:
        return ""
    if str(user_text or "").strip():
        reset_peer_turn_chain(cid)
    elif peer_triggered:
        allowed, reason = try_acquire_peer_turn_slot(
            cid,
            max_consecutive=MAX_CONSECUTIVE_PEER_TURNS,
            min_interval_sec=MIN_PEER_TURN_INTERVAL_SEC,
        )
        if not allowed:
            publish_conversation_event(
                cid,
                {
                    "type": "error",
                    "where": "peer_turn_limit",
                    "detail": reason or "peer turn 被限流",
                },
            )
            return ""
    run_id = _begin_conversation_run(cid) or ""
    if not run_id:
        publish_conversation_event(
            cid,
            {
                "type": "error",
                "where": "server",
                "detail": "当前会话仍在执行中，消息已进入队列或请稍后重试。",
            },
        )
        return ""
    publish_conversation_event(cid, {"type": "run_started", "run_id": run_id})

    def _run() -> None:
        try:
            if not _chat_api_key_available():
                publish_conversation_event(
                    cid,
                    {
                        "type": "error",
                        "where": "config",
                        "detail": "请先在 config.ini 的 [model] 节配置 api_key（或设置环境变量 CHAT_API_KEY）",
                    },
                )
                return
            _ensure_conversation_loaded(cid)
            if _persisted_session_unreadable_after_load(cid):
                publish_conversation_event(
                    cid,
                    {
                        "type": "error",
                        "where": "session_persist",
                        "detail": SESSION_PERSIST_UNREADABLE_SSE_DETAIL,
                    },
                )
                return
            for ev in run_agent_turn(
                cid,
                user_text,
                client_ip=client_ip,
                mode_hint=mode_hint,
                resume_after_user_confirm=resume_after_user_confirm,
                run_id=run_id,
            ):
                publish_conversation_event(cid, ev)
        except Exception as exc:
            import traceback

            print(
                f"ERROR: background agent turn failed cid={cid}: {exc}\n{traceback.format_exc()}",
                file=sys.stderr,
                flush=True,
            )
            publish_conversation_event(cid, {"type": "error", "where": "server", "detail": str(exc)})
        finally:
            _end_conversation_run(cid, run_id)
            try:
                _drain_session_inbox_after_run(cid)
            except Exception as exc:
                print(f"WARN: drain inbox failed cid={cid}: {exc}", file=sys.stderr, flush=True)
            try:
                _resume_turn_for_pending_peer_messages(cid)
            except Exception as exc:
                print(f"WARN: resume peer turn failed cid={cid}: {exc}", file=sys.stderr, flush=True)
            finally:
                _clear_turn_start_message_ids(cid)

    threading.Thread(target=_run, daemon=True, name=f"agent-turn-{cid[:8]}").start()
    return run_id

