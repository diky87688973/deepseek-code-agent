# -*- coding: utf-8 -*-
"""AgentRuntime：turn 主循环（只 yield，不 publish SSE）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent_v4.core import base as _core_base
from agent_v4.core import host_quality as _host_quality
from agent_v4.runtime.host_policy import (
    HostPolicy,
    _CONVERSATION_PREVIEWED,
    _PREVIEW_PATH_SCRIPTS,
)
from agent_v4.runtime.scenario_injection import ScenarioInjection
from agent_v4.runtime.turn_context import TurnContext
from agent_v4.core.tool_runtime import preflight_write_tool

for _k, _v in vars(_core_base).items():
    if not _k.startswith("__"):
        globals()[_k] = _v


class AgentRuntime:
    """薄编排 facade；事件经 yield 交给 turn_runner 唯一 publish。"""

    def __init__(self, policy=None, scenario=None):
        self.policy = policy or HostPolicy()
        self.scenario = scenario or ScenarioInjection()

    def run_turn(
        self,
        conversation_id: str,
        user_text: str,
        client_ip: str = "",
        mode_hint: str = "",
        *,
        resume_after_user_confirm: bool = False,
        run_id: str = "",
        attachments: Optional[List[Dict[str, Any]]] = None,
    ):
        """Yields SSE event dicts (caller / turn_runner publishes)."""
        catalog = load_catalog()
        otools, script_by_api = catalog_to_openai_tools(catalog)
        otools_sorted = sorted(otools, key=_openai_tools_sort_key)
        mode = _resolve_conversation_mode(
            conversation_id, "" if resume_after_user_confirm else user_text, mode_hint=mode_hint
        )
        messages = list(CONVERSATIONS.get(conversation_id, []))
        if not resume_after_user_confirm:
            _tail_drop_incomplete_tool_assistant(messages)
        _normalize_persisted_conversation(messages)
        _ensure_conversation_message_ids_v2(messages)
        _context_manager_v2(conversation_id).try_load_pending_mem_file(messages, _merge_pending_excerpts_for_conversation)
        active_round_id: Optional[str] = None
        for _m in reversed(messages):
            if _m.get("role") == "user":
                active_round_id = str(_m.get("_agent_round_id") or "").strip() or None
                break
        from agent_v4.core.attachments import (
            ephemeral_attachment_tail,
            format_user_attachment_footer,
            get_turn_attachments,
            set_turn_attachments,
            should_force_look_screenshot,
        )

        _atts = list(attachments or [])
        _looked_screenshot = False
        _vision_nudge_used = False
        if not resume_after_user_confirm:
            clear_agent_wait(conversation_id)
            set_turn_attachments(conversation_id, _atts)
            _user_content = str(user_text or "")
            if _atts:
                _user_content = _user_content + format_user_attachment_footer(_atts)
            _user_msg: Dict[str, Any] = {
                "role": "user",
                "content": _user_content,
                "_sender": "boss",
                "_sender_role": "boss",
            }
            if _atts:
                _user_msg["_attachments"] = [
                    {"id": a.get("id"), "path": a.get("path"), "mime": a.get("mime"), "name": a.get("name")}
                    for a in _atts
                ]
            active_round_id = _append_session_message_v2(
                conversation_id,
                messages,
                _user_msg,
                new_round=True,
            )
            _host_quality.detect_and_update_intent(conversation_id, user_text)
        else:
            # resume：尽量从最近 user 消息恢复附件索引
            for _m in reversed(messages):
                if _m.get("role") != "user":
                    continue
                _prev = _m.get("_attachments")
                if isinstance(_prev, list) and _prev:
                    _atts = [dict(x) for x in _prev if isinstance(x, dict)]
                    set_turn_attachments(conversation_id, _atts)
                break
        user_text_for_preview = user_text
        if resume_after_user_confirm:
            user_text_for_preview = ""
            for _m in reversed(messages):
                if _m.get("role") == "user":
                    user_text_for_preview = str(_m.get("content") or "")
                    break
            if user_text_for_preview:
                _host_quality.detect_and_update_intent(conversation_id, user_text_for_preview)
        _host_quality.reset_quality_turn_flags(conversation_id)
        active_sender = ""
        active_sender_is_peer = False
        for _m in reversed(messages):
            if _m.get("role") != "user":
                continue
            active_sender = str(_m.get("_sender") or "").strip()
            active_sender_is_peer = bool(_m.get("_agent_peer_message"))
            break
        with get_conversation_run_lock(conversation_id):
            _TURN_START_MESSAGE_IDS[conversation_id] = _message_id_set(messages)
        em = effective_model(conversation_id)
        # _emit 必须在任何 yield _emit 之前定义，否则 UnboundLocalError
        def _emit(ev):
            """投递给生成器契约；由 turn_runner 唯一 publish。不缓冲。"""
            return ev

        yield _emit({
            "type": "conversation",
            "conversation_id": conversation_id,
            "message_count": len(messages),
            "mode": mode,
            "audit_only": bool(CONVERSATION_AUDIT_ONLY.get(conversation_id)),
            "model": em,
            "reasoning_effort": _get_reasoning_effort(conversation_id),
        })
        if _atts:
            yield _emit({
                "type": "attachments",
                "conversation_id": conversation_id,
                "items": [{"id": a.get("id"), "name": a.get("name")} for a in _atts],
            })
        turn_tool_records: List[Dict[str, Any]] = []
        turn_tool_invocations_used = 0
        _turn_rr_state: Dict[str, Optional[Dict[str, Any]]] = {"tail": None}
        _vision_nudge_text = ""
        # 与门控共用同一 dict，避免 TurnContext 与局部变量双轨漂移
        _previewed_files = _CONVERSATION_PREVIEWED.setdefault(conversation_id, {})
        _written_files: Dict[str, str] = {}
        ctx = TurnContext(
            conversation_id,
            run_id=run_id,
            mode=mode,
            messages=messages,
            attachments=_atts,
            resume_after_user_confirm=resume_after_user_confirm,
            active_round_id=active_round_id,
            previewed_files=_previewed_files,
            written_files=_written_files,
        )
        ctx.looked_screenshot = _looked_screenshot
        ctx.vision_nudge_used = _vision_nudge_used

        def _sync_ephemeral_tail() -> None:
            """requires_reply / screenshot ephemeral rebuild via ScenarioInjection."""
            _turn_rr_state["tail"] = self.scenario.sync_ephemeral_tail(
                messages,
                conversation_id,
                attachments=_atts,
                looked_screenshot=_looked_screenshot,
                vision_nudge_text=_vision_nudge_text,
                get_turn_attachments=get_turn_attachments,
                ephemeral_attachment_tail=ephemeral_attachment_tail,
                find_pending_requires_reply_peer_message=_find_pending_requires_reply_peer_message,
                ephemeral_requires_reply_priority=_ephemeral_requires_reply_priority,
            )
            ctx.ephemeral_tail = _turn_rr_state["tail"]
            ctx.looked_screenshot = _looked_screenshot
            ctx.vision_nudge_used = _vision_nudge_used
            ctx.vision_nudge_text = _vision_nudge_text
            ctx.messages = messages
            ctx.previewed_files = _previewed_files
            ctx.written_files = _written_files
            ctx.turn_tool_records = turn_tool_records
            ctx.turn_tool_invocations_used = turn_tool_invocations_used

        _sync_ephemeral_tail()

        api_messages = _build_api_messages_for_model(messages, conversation_id)
        for _round in range(MAX_TOOL_ROUNDS):
            if server_shutting_down() or _consume_conversation_stop_requested(conversation_id, run_id):
                yield _emit(_finish_conversation_stopped(
                    conversation_id, messages, round_id=active_round_id, run_id=run_id
                ))
                return
            yield _emit({"type": "llm_round", "round": _round + 1})
            reff = _get_reasoning_effort(conversation_id)
            messages_for_llm = self.scenario.with_ephemeral_tail(
                api_messages, _turn_rr_state.get("tail"), _api_messages_with_ephemeral_tail
            )
            # 宿主质量禁止以独立消息灌入上下文（会干扰摘要连续性）；
            # 质量信息只挂在写工具返回 data.host_quality，硬约束靠写前门控。
            body: Dict[str, Any] = {
                "model": em,
                "messages": messages_for_llm,
                "reasoning_effort": reff,
                "thinking": {"type": "enabled"},
                "temperature": 0.2,
                "tools": otools_sorted,
            }
            yield _emit({"type": "llm_request", "round": _round + 1, "params": {"model": em, "thinking": True, "reasoning_effort": reff, "temperature": 0.2, "messagesCount": len(messages_for_llm), "toolsCount": len(otools_sorted), "hasTools": True}})
            last_choice_message: Optional[Dict[str, Any]] = None
            try:
                usage: Dict[str, Any] = {}
                content_parts: List[str] = []
                reasoning_parts: List[str] = []
                stream_tool_calls: List[dict] = []
                for chunk in deepseek_stream_request(body):
                    if _turn_abort_requested(conversation_id, run_id):
                        break
                    u = chunk.get("usage")
                    if isinstance(u, dict) and u:
                        usage = u
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    ch0 = choices[0] or {}
                    snap = _choice_snapshot_message(ch0)
                    if snap is not None:
                        last_choice_message = snap
                    delta = ch0.get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        content_parts.append(piece)
                        yield _emit({"type": "assistant_delta", "delta": piece})
                    for _rn in _reasoning_delta_field_names():
                        rp = delta.get(_rn)
                        if isinstance(rp, str) and rp:
                            reasoning_parts.append(rp)
                            yield _emit({"type": "reasoning_delta", "round": _round + 1, "delta": rp})
                    tdelta = delta.get("tool_calls")
                    if isinstance(tdelta, list) and tdelta:
                        stream_tool_calls.extend([x for x in tdelta if isinstance(x, dict)])
                yield _emit({"type": "llm_done"})
            except HTTPException as e:
                yield _emit({"type": "llm_response", "round": _round + 1, "params": {"error": e.detail}})
                yield _emit({"type": "error", "where": "chat_api", "detail": e.detail})
                CONVERSATIONS[conversation_id] = messages
                _save_conversation(conversation_id, messages)
                return

            if server_shutting_down() or _consume_conversation_stop_requested(conversation_id, run_id):
                yield _emit(_finish_conversation_stopped(
                    conversation_id, messages, round_id=active_round_id, run_id=run_id
                ))
                return

            if usage:
                yield _emit({"type": "usage", "usage": usage})

            content = "".join(content_parts)
            reasoning_before_finalize = "".join(reasoning_parts)
            reasoning_content = reasoning_before_finalize
            if last_choice_message is not None:
                reasoning_content = _finalize_stream_reasoning(reasoning_content, last_choice_message)
                content = _finalize_stream_content_text(content, last_choice_message)
            tcalls = _merge_stream_tool_calls_with_snapshot(stream_tool_calls, last_choice_message)
            for _rfe in _reasoning_stream_finalize_events(reasoning_before_finalize, reasoning_content, _round + 1):
                yield _emit(_rfe)
            yield _emit({"type": "llm_response", "round": _round + 1, "params": {"toolCallsCount": len(tcalls), "contentChars": len(content), "reasoningChars": len(reasoning_content), "usage": usage}})
            if tcalls:
                dispatch_title = _extract_dispatch_title(
                    _assistant_display_content_for_sse(content, reasoning_content)
                )
                if dispatch_title:
                    yield _emit({"type": "dispatch_title", "title": dispatch_title})
                assistant_msg = _assistant_message_for_persist(
                    content,
                    reasoning_content,
                    tool_calls=tcalls,
                )
                if not str(assistant_msg.get("content") or "").strip():
                    assistant_msg["content"] = None
                _append_session_message_v2(
                    conversation_id, messages, assistant_msg, round_id=active_round_id
                )
                CONVERSATIONS[conversation_id] = messages
                _save_conversation(conversation_id, messages)
                direct_preview_content: List[str] = []
                turn_stop_after_this_batch = False
                # ── 本批写盘追踪：previewed 会话级复用；written 每批清空 ──
                _previewed_files = _CONVERSATION_PREVIEWED.setdefault(conversation_id, {})
                _written_files.clear()
                ctx.previewed_files = _previewed_files
                ctx.written_files = _written_files
                for tc in tcalls:
                    if _turn_abort_requested(conversation_id, run_id):
                        yield _emit(_finish_conversation_stopped(
                            conversation_id, messages, round_id=active_round_id, run_id=run_id
                        ))
                        return
                    fn = tc.get("function") or {}
                    api_name = fn.get("name")
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except Exception:
                        args = {}
                    step_title = ""
                    if isinstance(args, dict):
                        _st_raw = args.pop("step_title", None)
                        if _st_raw is not None:
                            step_title = str(_st_raw).strip()
                            if len(step_title) > 80:
                                step_title = step_title[:79] + "…"
                    script = script_by_api.get(api_name or "")
                    exec_args = dict(args) if isinstance(args, dict) else {}
                    exec_args.pop("use_preview", None)
                    exec_args = _coerce_tool_arguments_for_agent(exec_args)
                    if script and isinstance(args, dict):
                        sl = script.lower()
                        if ("open_meteo" in sl or "ip_geolocate" in sl) and not str(exec_args.get("ip") or "").strip() and client_ip:
                            clean_ip = _normalize_client_ip_for_tools(client_ip)
                            if clean_ip:
                                args["ip"] = clean_ip
                                exec_args["ip"] = clean_ip
                    _budget_limit_blocked = turn_tool_budget_exhausted(
                        turn_tool_invocations_used, MAX_TOOL_ROUNDS
                    )
                    if _budget_limit_blocked:
                        result = tool_call_limit_reached_result(
                            used=MAX_TOOL_ROUNDS, limit=MAX_TOOL_ROUNDS
                        )
                        result = attach_tool_help_on_failure(script or "(unknown)", None, result)
                        if not script:
                            _record_tool_debug_failure(
                                conversation_id=conversation_id,
                                api_name=api_name,
                                script="(unknown)",
                                tool_call_id=tc.get("id"),
                                request=args,
                                response=result,
                                source="tool_call_limit",
                            )
                        _log_agent_console_tool(
                            conversation_id, api_name, script or "(unknown)", args, result
                        )
                        yield _emit({
                            "type": "tool_start",
                            "api_name": api_name,
                            "script": script or "(unknown)",
                            "args": args,
                            "tool_call_id": tc.get("id"),
                            "step_title": step_title,
                        })
                        yield _emit({
                            "type": "tool_end",
                            "api_name": api_name,
                            "script": script or "(unknown)",
                            "tool_call_id": tc.get("id"),
                            "ok": False,
                            "preview": preview_tool_result(script or "(unknown)", result),
                        })
                    elif not script:
                        result = _unknown_tool_result(api_name, script_by_api)
                        result = attach_tool_help_on_failure("(unknown)", None, result)
                        if not (isinstance(result, dict) and result.get("ok") is True):
                            _record_tool_debug_failure(
                                conversation_id=conversation_id,
                                api_name=api_name,
                                script="(unknown)",
                                tool_call_id=tc.get("id"),
                                request=args,
                                response=result,
                                source="unknown_tool",
                            )
                        _log_agent_console_tool(conversation_id, api_name, "(unknown)", args, result)
                        yield _emit({
                            "type": "tool_start",
                            "api_name": api_name,
                            "script": "(unknown)",
                            "args": args,
                            "tool_call_id": tc.get("id"),
                            "step_title": step_title,
                        })
                        yield _emit({
                            "type": "tool_end",
                            "api_name": api_name,
                            "script": "(unknown)",
                            "tool_call_id": tc.get("id"),
                            "ok": False,
                            "preview": preview_tool_result("(unknown)", result),
                        })
                    else:
                        yield _emit({
                            "type": "tool_start",
                            "api_name": api_name,
                            "script": script,
                            "args": args,
                            "tool_call_id": tc.get("id"),
                            "step_title": step_title,
                        })
                        if _turn_abort_requested(conversation_id, run_id):
                            result = _user_stopped_tool_result_dict()
                        else:
                            # 统一：先 preflight，再 progress 或 stoppable（均 skip 二次门控）
                            _write_block = preflight_write_tool(
                                conversation_id,
                                script,
                                exec_args,
                                step_title=step_title or "",
                                previewed_files=ctx.previewed_files,
                                written_files=ctx.written_files,
                                todo_list_mod=_todo_list_mod,
                            )
                            if _write_block is not None:
                                result = attach_tool_help_on_failure(script, None, _write_block)
                            elif script in _TOOL_PROGRESS_SCRIPTS:
                                    _search_progress: Dict[str, Any] = {}
                                    _exec_args_with_progress = dict(exec_args)
                                    _exec_args_with_progress["_progress_dict"] = _search_progress
                                    _ts_result_holder: Dict[str, Any] = {}
                                    _tool_aborted_by_user = False
                                    _cmd_input_key = ""
                                    _progress_last_seq = -1
                                    if script == "file_search.py":
                                        set_file_search_allowed(conversation_id, True)
                                    if script == "run_command.py":
                                        _search_progress["_shell_scope"] = conversation_id
                                        _cmd_input_key = f"{conversation_id}:{tc.get('id')}"
                                        _search_progress["command_input_key"] = _cmd_input_key
                                        try:
                                            from agent_v4.live_state import register_command_input_target

                                            register_command_input_target(_cmd_input_key, _search_progress)
                                        except Exception:
                                            pass
                                    elif script == "python_inline.py":
                                        _cmd_input_key = ""

                                    def _run_tool_with_progress() -> None:
                                        try:
                                            _ts_result_holder["r"] = execute_tool_script(
                                                script,
                                                _exec_args_with_progress,
                                                conversation_id=conversation_id,
                                                skip_write_preflight=True,
                                                step_title=step_title or "",
                                                previewed_files=ctx.previewed_files,
                                                written_files=ctx.written_files,
                                                todo_list_mod=_todo_list_mod,
                                            )
                                        except Exception as exc:
                                            _ts_result_holder["r"] = {
                                                "ok": False,
                                                "data": None,
                                                "error": {"type": "ToolError", "message": str(exc)},
                                            }

                                    import threading as _thr

                                    _t = _thr.Thread(target=_run_tool_with_progress, daemon=True)
                                    _t.start()
                                    _wall_deadline = time.monotonic() + _tool_host_wall_timeout_sec(
                                        script, exec_args
                                    )
                                    try:

                                        def _yield_tool_progress_ev() -> Dict[str, Any]:
                                            return _tool_progress_sse_event(
                                                _search_progress,
                                                conversation_id=conversation_id,
                                                tool_call_id=str(tc.get("id") or ""),
                                                script=script,
                                            )

                                        while _t.is_alive():
                                            if _turn_abort_requested(conversation_id, run_id):
                                                _search_progress["_abort"] = True
                                                _tool_aborted_by_user = True
                                                if script == "run_command.py":
                                                    try:
                                                        from command_safety import force_kill_active_shell_process

                                                        force_kill_active_shell_process()
                                                    except Exception:
                                                        pass
                                                for _join_i in range(40):
                                                    if not _t.is_alive():
                                                        break
                                                    _t.join(timeout=0.25)
                                                break
                                            if script == "run_command.py" and time.monotonic() >= _wall_deadline:
                                                try:
                                                    from command_safety import force_kill_active_shell_process

                                                    force_kill_active_shell_process()
                                                except Exception:
                                                    pass
                                                _wall_deadline = time.monotonic() + 20.0
                                            _seq_now = int(_search_progress.get("_seq") or 0)
                                            _tp_ev = _yield_tool_progress_ev()
                                            if _tp_ev and (
                                                _seq_now != _progress_last_seq
                                                or _search_progress.get("awaiting_input")
                                            ):
                                                _progress_last_seq = _seq_now
                                                yield _emit(_tp_ev)
                                            _t.join(timeout=0.35)
                                        _tp_final = _yield_tool_progress_ev()
                                        if _tp_final:
                                            yield _emit(_tp_final)
                                        if _tool_aborted_by_user:
                                            turn_stop_after_this_batch = True
                                            result = _user_stopped_tool_result_dict()
                                        else:
                                            result = _ts_result_holder.get("r", {})
                                    finally:
                                        if script == "file_search.py":
                                            set_file_search_allowed(conversation_id, False)
                                        if script == "run_command.py" and _cmd_input_key:
                                            try:
                                                from agent_v4.live_state import unregister_command_input_target

                                                unregister_command_input_target(_cmd_input_key)
                                            except Exception:
                                                pass
                            else:
                                result = _execute_tool_script_stoppable(
                                    conversation_id,
                                    run_id,
                                    script,
                                    exec_args,
                                    skip_write_preflight=True,
                                    step_title=step_title or "",
                                    previewed_files=ctx.previewed_files,
                                    written_files=ctx.written_files,
                                    todo_list_mod=_todo_list_mod,
                                )
                        result = maybe_attach_write_tool_host_dry_run_notice(
                            script,
                            result,
                            CONVERSATION_MODES.get(conversation_id, ""),
                        )
                        if isinstance(result, dict) and script:
                            _host_quality.note_evidence_tool(
                                conversation_id, script, exec_args, result
                            )
                            _host_quality.note_write_tool_result(
                                conversation_id, script, exec_args, result
                            )
                            # Plan 模式 dry_run 不走预览门：成功预览也要记入会话级 previewed，
                            # 否则切 Execute 真写会被 PreviewRequired 误拦。
                            if (
                                result.get("ok")
                                and script in _PREVIEW_PATH_SCRIPTS
                                and (
                                    exec_args.get("dry_run", True) is True
                                    or exec_args.get("dry_run") == 1
                                    or str(exec_args.get("dry_run", True)).strip().lower()
                                    in ("1", "true")
                                )
                            ):
                                _pp = _host_quality._resolve_write_path(exec_args, result)
                                if _pp:
                                    _previewed_files[_pp] = step_title or script
                            # 真写成功后再记入本轮 _written_files，并把质量报告挂到工具返回
                            if (
                                result.get("ok")
                                and script in _PREVIEW_PATH_SCRIPTS
                                and not (
                                    exec_args.get("dry_run", True) is True
                                    or exec_args.get("dry_run") == 1
                                    or str(exec_args.get("dry_run", True)).strip().lower()
                                    in ("1", "true")
                                )
                            ):
                                _wp = _host_quality._resolve_write_path(exec_args, result)
                                _data = result.get("data") if isinstance(result.get("data"), dict) else {}
                                if _wp and _data.get("written") is not False:
                                    _written_files[_wp] = step_title or script
                                    result = _host_quality.attach_host_quality_to_write_result(
                                        conversation_id, _wp, result
                                    )
                        # 预览记录持久到会话结束，允许同文件多次写入无需反复预览
                        if not (isinstance(result, dict) and result.get("ok") is True):
                            _record_tool_debug_failure(
                                conversation_id=conversation_id,
                                api_name=api_name,
                                script=script,
                                tool_call_id=tc.get("id"),
                                request=exec_args,
                                response=result
                                if isinstance(result, dict)
                                else {"ok": False, "data": None, "error": {"type": "InvalidResult", "message": repr(result)}},
                                source="run_agent_turn",
                            )
                        _log_agent_console_tool(conversation_id, api_name, script, exec_args, result)
                        print(f"[SSE_DEBUG] yielding tool_end for script={script} ok={result.get('ok')}", flush=True)
                        _te_tool_end: Dict[str, Any] = {
                            "type": "tool_end",
                            "api_name": api_name,
                            "script": script,
                            "tool_call_id": tc.get("id"),
                            "ok": bool(result.get("ok")),
                            "preview": preview_tool_result(script, result),
                        }
                        if (
                            active_sender_is_peer
                            and active_sender
                            and active_sender not in ("boss",)
                            and script == "user_confirm.py"
                            and _is_user_confirm_required(result)
                            and isinstance(exec_args, dict)
                        ):
                            result = {
                                "ok": True,
                                "data": {
                                    "message": (
                                        "当前消息来自其他 Agent。user_confirm 只用于请求人类用户确认；"
                                        f"如需澄清，请调用 session_send(target_id=\"{active_sender}\", message=\"...\") 与对方沟通。"
                                    ),
                                    "peer_sender": active_sender,
                                },
                            }
                            _te_tool_end["ok"] = True
                            _te_tool_end["preview"] = preview_tool_result(script, result)
                        if script in ("user_confirm.py", "kling_generate.py", "skill_manage.py") and _is_user_confirm_required(result) and isinstance(exec_args, dict):
                            _te_tool_end["user_confirm_required"] = True
                            _ucd = result.get("data") or {}
                            _te_tool_end["user_confirm_title"] = str(_ucd.get("title") or "")
                            _cos = _ucd.get("confirms")
                            _te_tool_end["user_confirm_options"] = list(_cos) if isinstance(_cos, list) else []
                            if bool(_ucd.get("multi")):
                                _te_tool_end["user_confirm_multi"] = True
                            _cix = _ucd.get("custom_option_index")
                            if isinstance(_cix, int):
                                _te_tool_end["user_confirm_custom_index"] = _cix
                            _pending_args = dict(exec_args)
                            if script in ("kling_generate.py", "skill_manage.py"):
                                _cid = _ucd.get("confirm_id")
                                if _cid:
                                    _pending_args["confirm_id"] = str(_cid)
                            PENDING_USER_CONFIRM[conversation_id] = {
                                "tool_call_id": str(tc.get("id") or ""),
                                "exec_args": _pending_args,
                                "script": script,
                                "confirms": list(_cos) if isinstance(_cos, list) else [],
                            }
                        _suspend_wait = script == "session_wait.py" and should_suspend_after_session_wait(result)
                        if script == "todo_list.py" and isinstance(result, dict) and result.get("ok"):
                            _td = result.get("data")
                            if _td is None:
                                # 无活跃清单 → 发送关闭信号
                                sse_data = {"type": "todo_list", "conversation_id": conversation_id, "close": True}
                                yield _emit(sse_data)
                            elif isinstance(_td, dict):
                                if _td.get("close") is True:
                                    sse_data = {"type": "todo_list", "conversation_id": conversation_id, "close": True}
                                    yield _emit(sse_data)
                                elif isinstance(_td.get("items"), list):
                                    _todo_list_mod.session_lists[conversation_id] = _td
                                    # 发送独立的 todo_list SSE 事件供前端专属区域渲染
                                    sse_data = {
                                        "type": "todo_list",
                                        "list_id": str(_td.get("list_id") or ""),
                                        "items": _td["items"],
                                        "all_done": all(it.get("done") for it in _td["items"]),
                                    }
                                    sse_data["collapsed"] = bool(_td.get("collapsed"))
                                    if _td.get("close"):
                                        sse_data["close"] = True
                                    yield _emit(sse_data)
                        yield _emit(_te_tool_end)
                        if _suspend_wait:
                            CONVERSATIONS[conversation_id] = messages
                            _save_conversation(conversation_id, messages)
                            yield _emit({
                                "type": "agent_wait_suspended",
                                "pending": list((result.get("data") or {}).get("pending") or []),
                                "thread_id": str((result.get("data") or {}).get("thread_id") or ""),
                            })
                            yield _emit(_context_layout_event(conversation_id, messages))
                            yield _emit({"type": "done"})
                            return

                        if script == "session_create.py" and isinstance(result, dict) and result.get("ok"):
                            _sd = (result.get("data") or {})
                            _agents = _sd.get("agents") or []
                            if _sd.get("session_id") and not _agents:
                                yield _emit({"type": "open_session", "session_id": _sd["session_id"], "name": _sd.get("name", "")})
                            for _agent in _agents:
                                if isinstance(_agent, dict) and _agent.get("session_id"):
                                    yield _emit({"type": "open_session", "session_id": _agent["session_id"], "name": _agent.get("name", "")})

                        if script == "run_type.py" and isinstance(result, dict) and result.get("ok"):
                            _dc = result.get("data") or {}
                            _rtm = _dc.get("run_type")
                            if _rtm in ("auto", "plan", "execute"):
                                yield _emit({"type": "mode_changed", "mode": _rtm})
                        md_chat = _chat_diff_markdown_for_tool(script, result, exec_args)
                        if md_chat:
                            yield _emit({"type": "assistant_markdown", "markdown": md_chat})
                        _preview = _build_direct_preview_message(script, result, user_text_for_preview)
                        if _preview:
                            direct_preview_content.append(_preview)
                    result, turn_tool_invocations_used = apply_turn_tool_budget_to_result(
                        result,
                        turn_tool_invocations_used=turn_tool_invocations_used,
                        limit=MAX_TOOL_ROUNDS,
                        limit_blocked=_budget_limit_blocked,
                    )
                    _tool_rec: Dict[str, Any] = {
                        "api_name": api_name,
                        "script": script or "(unknown)",
                        "ok": bool(result.get("ok")),
                    }
                    if script in ("session_send.py", "session_multisend.py", "session_broadcast.py"):
                        _out_rr = _exec_requires_reply_true(exec_args)
                        _tool_rec["requires_reply_out"] = _out_rr
                        _reply_tids = _extract_reply_tool_target_ids(script, exec_args, result)
                        if _reply_tids:
                            _tool_rec["target_ids"] = _reply_tids
                            if result.get("ok"):
                                _apply_inbound_requires_reply_answered(
                                    conversation_id,
                                    messages,
                                    _turn_rr_state,
                                    _reply_tids,
                                    thread_id=str(exec_args.get("thread_id") or ""),
                                )
                                _sync_ephemeral_tail()
                    turn_tool_records.append(_tool_rec)
                    if script == "look_screenshot.py" and isinstance(result, dict) and result.get("ok"):
                        _looked_screenshot = True
                        ctx.looked_screenshot = True
                        _sync_ephemeral_tail()
                    _append_session_message_v2(
                        conversation_id,
                        messages,
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": _truncate_tool_result(result),
                        },
                        round_id=active_round_id,
                    )
                    yield _emit(_context_layout_event(conversation_id, messages))
                    if _turn_abort_requested(conversation_id, run_id):
                        turn_stop_after_this_batch = True
                    if turn_stop_after_this_batch:
                        yield _emit(_finish_conversation_stopped(
                            conversation_id, messages, round_id=active_round_id, run_id=run_id
                        ))
                        return
                if conversation_id in PENDING_USER_CONFIRM:
                    CONVERSATIONS[conversation_id] = messages
                    _save_conversation(conversation_id, messages)
                    yield _emit({"type": "paused_for_user_confirm", "conversation_id": conversation_id})
                    return
                if direct_preview_content:
                    combined = "\n\n".join(direct_preview_content)
                    _append_session_message_v2(
                        conversation_id,
                        messages,
                        {"role": "assistant", "content": combined, "reasoning_content": ""},
                        round_id=active_round_id,
                    )
                    if not content_parts:
                        yield _emit({"type": "assistant", "content": combined})
                    yield _emit(_context_layout_event(conversation_id, messages))
                if _turn_abort_requested(conversation_id, run_id):
                    yield _emit(_finish_conversation_stopped(
                        conversation_id, messages, round_id=active_round_id, run_id=run_id
                    ))
                    return
                # ── 写盘后质量：已挂在各写工具返回的 data.host_quality，不再独占 system 消息 ──
                if _written_files:
                    yield _emit({
                        "type": "host_quality",
                        "mode": "attached_to_tool_result",
                        "files": list(_written_files.keys())[:12],
                    })
                api_messages = _build_api_messages_for_model(messages, conversation_id)
                continue

            assistant_msg = _assistant_message_for_persist(content, reasoning_content)
            _append_session_message_v2(
                conversation_id, messages, assistant_msg, round_id=active_round_id
            )
            display_content = str(assistant_msg.get("content") or "").strip()
            if display_content:
                _host_quality.note_assistant_may_complete_review(
                    conversation_id, display_content
                )
                _host_quality.note_assistant_claim_fixed(
                    conversation_id, display_content
                )
            CONVERSATIONS[conversation_id] = messages
            _save_conversation(conversation_id, messages)
            if display_content and not content_parts:
                yield _emit({"type": "assistant", "content": display_content})
            # requires_reply：仅入站 requires_reply=true 会挂 ephemeral；工具回复 peer 即可标记（出站 requires_reply 可为 false）
            _pending_rr = _find_pending_requires_reply_peer_message(messages)
            if _pending_rr is not None:
                _peer_cid = str(_pending_rr.get("_sender") or "").strip()
                if _turn_replied_to_peer(turn_tool_records, _peer_cid):
                    _apply_inbound_requires_reply_answered(
                        conversation_id,
                        messages,
                        _turn_rr_state,
                        [_peer_cid],
                        thread_id=str(_pending_rr.get("_thread_id") or ""),
                    )
                    _sync_ephemeral_tail()
            # 有截图却未 look_screenshot：轻量打回一轮（与 RR 提示合并，不互相覆盖）
            if (
                not _vision_nudge_used
                and should_force_look_screenshot(user_text, get_turn_attachments(conversation_id) or _atts, _looked_screenshot)
            ):
                _vision_nudge_used = True
                _vision_nudge_text = self.scenario.vision_nudge_content()
                ctx.vision_nudge_used = True
                ctx.vision_nudge_text = _vision_nudge_text
                _sync_ephemeral_tail()
                api_messages = _build_api_messages_for_model(messages, conversation_id)
                continue
            break
        else:
            # LLM 轮次用尽：无 tools 收尾一轮；ephemeral user 提示拟人收尾（不落盘）；次数见各 tool 返回 budget
            api_messages = _build_api_messages_for_model(messages, conversation_id)
            wrap_messages = list(api_messages)
            rr_tail = _turn_rr_state.get("tail")
            if rr_tail:
                wrap_messages.append(dict(rr_tail))
            wrap_messages.append(self.scenario.max_tool_rounds_wrap_user(_ephemeral_max_tool_rounds_wrap_user))
            if server_shutting_down() or _consume_conversation_stop_requested(conversation_id, run_id):
                yield _emit(_finish_conversation_stopped(
                    conversation_id, messages, round_id=active_round_id, run_id=run_id
                ))
                return
            yield _emit({"type": "llm_round", "round": MAX_TOOL_ROUNDS + 1})
            reff = _get_reasoning_effort(conversation_id)
            wrap_body: Dict[str, Any] = {
                "model": em,
                "messages": wrap_messages,
                "reasoning_effort": reff,
                "thinking": {"type": "enabled"},
                "temperature": 0.2,
            }
            yield _emit({"type": "llm_request", "round": MAX_TOOL_ROUNDS + 1, "params": {"model": em, "thinking": True, "reasoning_effort": reff, "temperature": 0.2, "messagesCount": len(wrap_messages), "toolsCount": 0, "hasTools": False}})
            last_choice_message_wrap: Optional[Dict[str, Any]] = None
            try:
                usage: Dict[str, Any] = {}
                content_parts: List[str] = []
                reasoning_parts: List[str] = []
                stream_tool_calls: List[dict] = []
                for chunk in deepseek_stream_request(wrap_body):
                    if _turn_abort_requested(conversation_id, run_id):
                        break
                    u = chunk.get("usage")
                    if isinstance(u, dict) and u:
                        usage = u
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    ch0 = choices[0] or {}
                    snap = _choice_snapshot_message(ch0)
                    if snap is not None:
                        last_choice_message_wrap = snap
                    delta = ch0.get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        content_parts.append(piece)
                        yield _emit({"type": "assistant_delta", "delta": piece})
                    for _rn in _reasoning_delta_field_names():
                        rp = delta.get(_rn)
                        if isinstance(rp, str) and rp:
                            reasoning_parts.append(rp)
                            yield _emit({"type": "reasoning_delta", "round": MAX_TOOL_ROUNDS + 1, "delta": rp})
                    tdelta = delta.get("tool_calls")
                    if isinstance(tdelta, list) and tdelta:
                        stream_tool_calls.extend([x for x in tdelta if isinstance(x, dict)])
                yield _emit({"type": "llm_done"})
            except HTTPException as e:
                yield _emit({"type": "llm_response", "round": MAX_TOOL_ROUNDS + 1, "params": {"error": e.detail}})
                yield _emit({"type": "error", "where": "chat_api", "detail": e.detail})
                CONVERSATIONS[conversation_id] = messages
                _save_conversation(conversation_id, messages)
                return

            if server_shutting_down() or _consume_conversation_stop_requested(conversation_id, run_id):
                yield _emit(_finish_conversation_stopped(
                    conversation_id, messages, round_id=active_round_id, run_id=run_id
                ))
                return

            if usage:
                yield _emit({"type": "usage", "usage": usage})

            content = "".join(content_parts)
            reasoning_before_finalize = "".join(reasoning_parts)
            rc = reasoning_before_finalize
            if last_choice_message_wrap is not None:
                rc = _finalize_stream_reasoning(rc, last_choice_message_wrap)
                content = _finalize_stream_content_text(content, last_choice_message_wrap)
            tcalls = _merge_stream_tool_calls_with_snapshot(
                stream_tool_calls, last_choice_message_wrap
            )
            for _rfe in _reasoning_stream_finalize_events(reasoning_before_finalize, rc, MAX_TOOL_ROUNDS + 1):
                yield _emit(_rfe)
            reasoning_content = rc.strip()
            yield _emit({"type": "llm_response", "round": MAX_TOOL_ROUNDS + 1, "params": {"toolCallsCount": len(tcalls), "contentChars": len(content), "reasoningChars": len(reasoning_content), "usage": usage}})
            assistant_msg = _assistant_message_for_persist(content, reasoning_content)
            _append_session_message_v2(
                conversation_id, messages, assistant_msg, round_id=active_round_id
            )
            display_content = str(assistant_msg.get("content") or "").strip()
            if display_content and not content_parts:
                yield _emit({"type": "assistant", "content": display_content})

        if not resume_after_user_confirm:
            _maybe_schedule_summarization(conversation_id, messages)
        _reconcile_peer_messages_from_store(conversation_id, messages)
        CONVERSATIONS[conversation_id] = messages
        _save_conversation(conversation_id, messages)
        yield _emit(_context_layout_event(conversation_id, messages))
        _CONVERSATION_PREVIEWED.pop(conversation_id, None)
        yield _emit({"type": "done"})

