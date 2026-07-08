# -*- coding: utf-8
"""agent_v3.core.context_pipeline"""
from __future__ import annotations

from agent_v3.core.deps import *  # noqa: F403
from agent_v3.core.shared_state import *  # noqa: F403

def _approx_tokens_message(m: Dict[str, Any]) -> int:
    c = str(m.get("content") or "").strip()
    rc = m.get("reasoning_content")
    rs = str(rc).strip() if isinstance(rc, str) else ""
    n = _approx_tokens_text(c)
    if rs and rs != c:
        n += _approx_tokens_text(rs)
    tc = m.get("tool_calls")
    if isinstance(tc, list) and tc:
        try:
            n += _approx_tokens_text(json.dumps(tc, ensure_ascii=False))
        except (TypeError, ValueError):
            n += 16
    return n

def _approx_tokens_text(s: str) -> int:
    """上下文视图条：字符比例估算（系数见 AGENT_TOKEN_ESTIMATE_*）；AGENT_CONTEXT_TOKEN_METHOD 预留。"""
    if not s:
        return 0
    return _estimate_tokens_text_ratio(s)

def _assistant_tool_call_ids(assistant_msg: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for tc in assistant_msg.get("tool_calls") or []:
        if isinstance(tc, dict) and tc.get("id"):
            out.add(str(tc["id"]))
    return out

def _build_api_messages_for_model(persisted: List[Dict[str, Any]], conversation_id: str) -> List[Dict[str, Any]]:
    # ContextState.java：flatten 顺序为 system prompts → KB → summaries → dialogue → mode；
    # 须先 tryLoadPending（见 run_agent_turn）再进入此处组包。
    cm = _context_manager_v2(conversation_id)
    cm.rebuild_from_persisted(persisted, _build_context_segments, conversation_id)
    return cm.flatten_to_api_messages(_sanitize_tool_pairing_for_api)

def _build_auto_load_skill_messages() -> List[str]:
    """构建 auto_load skill 的 system 消息列表。"""
    mgr = _get_skill_manager()
    return mgr.build_auto_load_messages()

def _build_context_segments(
    persisted: List[Dict[str, Any]], conversation_id: str
) -> Tuple[
    str,
    str,
    str,
    str,
    List[str],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    int,
    Dict[str, Any],
]:
    """与 _build_api_messages_for_model 相同的语义拆分（未 sanitize），供布局统计。
    返回 13 元组：code_hint, user_rules, tool_system, catalog_hints, skill_registry, auto_load_skills,
    kb, summaries, pure_folded, full_pre, full_suf, pure_user_turns, mode_tail。
    """
    mode = _stored_mode_for_tail(conversation_id)
    fu = _find_first_user_index(persisted)
    kb_parts = _build_kb_system_messages(conversation_id)
    summaries: List[Dict[str, Any]] = []
    if fu is not None and fu > 1:
        for m in persisted[1:fu]:
            if m.get("role") == "system" and m.get("_agent_summary"):
                summaries.append(_strip_internal_message_for_api(m))
    dialogue = persisted[fu:] if fu is not None else []
    has_compressed = len(summaries) > 0
    pure_raw, full_pre_raw, full_suf_raw = _split_pure_and_full_dialogue(
        dialogue,
        CONTEXT_FULL_USER_ROUNDS,
        CONTEXT_PURE_USER_ROUNDS,
        has_compressed,
        conversation_id,
    )
    pure_user_turns = _count_user_turns_in_messages(pure_raw)
    pure_folded = _fold_pure_window_for_api(pure_raw)
    full_pre_stripped = [
        _strip_internal_message_for_api(m) for m in full_pre_raw if _include_message_for_api(m)
    ]
    full_suf_stripped = [
        _strip_internal_message_for_api(m) for m in full_suf_raw if _include_message_for_api(m)
    ]
    mode_tail = _ephemeral_mode_system_tail(mode, conversation_id)
    skill_registry = _build_skill_registry_message()
    auto_load_skills = _build_auto_load_skill_messages()
    sys_user_rules = str(USER_RULES_SYSTEM_PROMPT or "").strip()
    sys_catalog_hints = _get_catalog_hints_system_prompt()
    return (
        AGENT_CODE_HINT_SYSTEM_PROMPT,
        sys_user_rules,
        TOOL_AGENT_SYSTEM_PROMPT + _build_team_role_prefix(conversation_id),
        sys_catalog_hints,
        skill_registry,
        auto_load_skills,
        kb_parts,
        summaries,
        pure_folded,
        full_pre_stripped,
        full_suf_stripped,
        pure_user_turns,
        mode_tail,
    )

def _build_skill_registry_message() -> str:
    """构建 Skills 注册清单（注入前缀）。"""
    mgr = _get_skill_manager()
    return mgr.build_registry_message()

def _build_team_role_prefix(conversation_id: str) -> str:
    """若当前会话是团队成员，返回角色描述 + 协作规范前缀。结果缓存避免重复读取注册表。"""
    cached = _team_role_cache.get(conversation_id)
    if cached is not None:
        return cached
    result = ""
    try:
        from agent_v3.live_state import get_agent_session
        meta = get_agent_session(conversation_id)
        if meta:
                role = str(meta.get("role") or "Agent").strip()
                name = str(meta.get("name") or conversation_id[:8]).strip()
                persona = str(meta.get("persona") or "").strip()
                tags = meta.get("tags") or []
                tags_text = "、".join(str(x) for x in tags if str(x).strip()) if isinstance(tags, list) else str(tags or "").strip()
                from util.agent_prompt_constants_v2 import TEAM_ROLE_DEFAULT
                extra = ""
                if tags_text:
                    extra += f"\n【Agent 标签】{tags_text}"
                if persona:
                    extra += f"\n【Agent Persona】\n{persona}"
                result = f"\n\n{TEAM_ROLE_DEFAULT.format(role=role, name=name)}{extra}"
    except Exception as exc:
        import sys
        print(f"WARN: _build_team_role_prefix failed for {conversation_id}: {exc}", file=sys.stderr, flush=True)
    _team_role_cache[conversation_id] = result
    return result

def _char_token_estimate_weight(ch: str, en: float, zh: float) -> float:
    """ASCII 用 en；CJK 常用区及全角用 zh；其余拉丁扩展等用 en。"""
    o = ord(ch)
    if o <= 0x007F:
        return en
    if 0xFF00 <= o <= 0xFFEF:
        return zh
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
        return zh
    if 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF:
        return zh
    return en

def _clear_turn_start_message_ids(conversation_id: str) -> None:
    """兜底清理 turn 快照（与 _resume 配对；finally 中调用以防未 pop）。"""
    cid = str(conversation_id or "").strip()
    if not cid:
        return
    with get_conversation_run_lock(cid):
        _TURN_START_MESSAGE_IDS.pop(cid, None)

def _collect_excerpt_paths_to_merge(cid: str) -> List[Path]:
    """内存 pending 队列 + 磁盘上未 merged 的 excerpt；按 ID 删除时合并顺序无关，文件名时间戳降序。"""
    with _SUMMARY_STATE_LOCK:
        pending = list(PENDING_EXCERPT_PATHS.pop(cid, []) or [])
    seen: Set[str] = set()
    out: List[Path] = []
    for path_str in pending:
        p = Path(path_str)
        if not p.is_file():
            continue
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    for p in _excerpt_disk_paths_for_cid(cid):
        if not _excerpt_file_needs_merge(p):
            continue
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)

    out.sort(key=lambda path: path.name, reverse=True)
    return out

def _compute_context_layout_payload(conversation_id: str, persisted: List[Dict[str, Any]]) -> Dict[str, Any]:
    (
        sys_code_hint,
        sys_user_rules,
        sys_base,
        sys_catalog_hints,
        skill_registry,
        auto_load_skills,
        kb_parts,
        summaries,
        pure_folded,
        full_pre,
        full_suf,
        pure_user_turns,
        mode_tail,
    ) = _build_context_segments(persisted, conversation_id)
    t_system = (
        _approx_tokens_text(sys_code_hint)
        + _approx_tokens_text(sys_user_rules)
        + _approx_tokens_text(sys_base)
        + _approx_tokens_text(sys_catalog_hints)
    )
    t_skill = _approx_tokens_text(skill_registry) + sum(_approx_tokens_text(c) for c in (auto_load_skills or []))
    t_kb = sum(_approx_tokens_text(k) for k in (kb_parts or []))
    t_summary = sum(_approx_tokens_message(m) for m in summaries)
    t_pure = sum(_approx_tokens_message(m) for m in pure_folded)
    t_full = sum(_approx_tokens_message(m) for m in full_pre) + sum(_approx_tokens_message(m) for m in full_suf)
    t_mode = _approx_tokens_message(mode_tail)
    mgr = _get_skill_manager()
    skill_count = mgr.registry_count
    auto_load_count = mgr.auto_load_count
    auto_load_tokens = sum(_approx_tokens_text(c) for c in (auto_load_skills or []))
    # label 为前端上下文条/tooltip 标题（与 context_manager_v2 / 上下文视图一致）
    labels = {
        "system": "系统提示词",
        "skill": "Skills",
        "knowledge": "知识库",
        "summary": "记忆文件",
        "pure": "远期记忆",
        "full_recent": "近期记忆",
        "mode": "模式",
    }
    keys_tokens = [
        ("system", t_system),
        ("skill", t_skill),
        ("knowledge", t_kb),
        ("summary", t_summary),
        ("pure", t_pure),
        ("full_recent", t_full),
        ("mode", t_mode),
    ]
    counts_map: Dict[str, Optional[int]] = {
        "system": None,
        "skill": skill_count,
        "knowledge": len(kb_parts) if kb_parts else _kb_attached_file_count(conversation_id),
        "summary": len(summaries),
        "pure": pure_user_turns,
        "full_recent": _count_user_turns_in_messages(full_pre) + _count_user_turns_in_messages(full_suf),
    }
    total_used = sum(t for _, t in keys_tokens)
    model_cap = model_max_context_tokens(effective_model(conversation_id))
    layout_cap = min(int(CONTEXT_LAYOUT_BUDGET_TOKENS), int(model_cap))
    budget = max(layout_cap, int(total_used), 1)
    remainder = max(0, budget - int(total_used))
    segments: List[Dict[str, Any]] = []
    for key, tok in keys_tokens:
        pct = (100.0 * float(tok) / float(budget)) if budget > 0 else 0.0
        seg_item: Dict[str, Any] = {
            "key": key,
            "label": labels.get(key, key),
            "tokens": int(tok),
            "pct": round(pct, 6),
        }
        cn = counts_map.get(key)
        if cn is not None:
            seg_item["count"] = int(cn)
        if key == "skill":
            seg_item["auto_load_count"] = auto_load_count
            seg_item["auto_load_tokens"] = int(auto_load_tokens)
        segments.append(seg_item)
    pct_rem = (100.0 * float(remainder) / float(budget)) if budget > 0 else 0.0
    segments.append(
        {
            "key": "remaining",
            "label": "剩余容量",
            "tokens": int(remainder),
            "pct": round(pct_rem, 6),
        }
    )
    return {
        "segments": segments,
        "total_tokens": int(total_used),
        "budget_tokens": int(budget),
        "model_max_context_tokens": int(model_cap),
    }

def _context_manager_v2(conversation_id: str) -> ContextManager:
    """按 ContextState.java 构造会话级 ContextManager（锚定缓存与现网共用同一 dict）。"""
    return ContextManager(
        conversation_id,
        CONTEXT_PURE_USER_ROUNDS,
        CONTEXT_FULL_USER_ROUNDS,
        CONTEXT_SUMMARY_TOKEN_THRESHOLD,
        _PURE_ANCHOR_CACHE,
    )

def _count_user_turns_in_messages(msgs: List[Dict[str, Any]]) -> int:
    """统计消息列表中 role=user 条数（近期/远期「个」与配置回合对齐，不含 assistant/tool）。"""
    return sum(1 for m in msgs if m.get("role") == "user")

def _ephemeral_mode_system_tail(mode: str, conversation_id: str = "") -> Dict[str, Any]:
    parts: List[str] = []
    cid = str(conversation_id or "").strip()
    if cid and CONVERSATION_AUDIT_ONLY.get(cid):
        parts.append("⚠️ " + TOOL_AGENT_AUDIT_MODE_PROMPT)
    if mode == "plan":
        parts.append("⚠️ " + TOOL_AGENT_PLAN_MODE_PROMPT)
    elif mode == "execute":
        parts.append("⚠️ " + TOOL_AGENT_EXECUTE_MODE_PROMPT)
    else:
        parts.append(TOOL_AGENT_AUTO_MODE_PROMPT)
    return {"role": "system", "content": "\n".join(parts)}

def _estimate_tokens_text_ratio(s: str) -> int:
    """官方建议的字符比例估算：英文≈0.3、中文等≈0.6 token/字（系数来自 config）。"""
    if not s:
        return 0
    en = TOKEN_ESTIMATE_EN_PER_CHAR
    zh = TOKEN_ESTIMATE_ZH_PER_CHAR
    acc = 0.0
    for ch in s:
        acc += _char_token_estimate_weight(ch, en, zh)
    return max(0, int(round(acc)))

def _excerpt_disk_paths_for_cid(cid: str) -> List[Path]:
    if not cid or not EXCERPTS_DIR.is_dir():
        return []
    return sorted(p for p in EXCERPTS_DIR.glob(f"{cid}_*.md") if p.is_file())

def _excerpt_file_needs_merge(p: Path) -> bool:
    try:
        raw = p.read_text(encoding="utf-8")
        blob, _ = _parse_excerpt_file(raw)
        if not isinstance(blob, dict):
            return True
        am = blob.get("agent_excerpt_meta")
        if isinstance(am, dict) and am.get("merged"):
            return False
    except Exception:
        return True
    return True

def _find_first_user_index(messages: List[Dict[str, Any]]) -> Optional[int]:
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            return i
    return None

def _fold_pure_window_for_api(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """每轮只保留 user + 最后一条 assistant 的纯文本 content（无 tool/reasoning）。"""
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(msgs):
        if msgs[i].get("role") != "user":
            i += 1
            continue
        out.append(_strip_internal_message_for_api(msgs[i]))
        i += 1
        chunk: List[Dict[str, Any]] = []
        while i < len(msgs) and msgs[i].get("role") != "user":
            chunk.append(msgs[i])
            i += 1
        last_asst: Optional[Dict[str, Any]] = None
        for rm in chunk:
            if rm.get("role") == "assistant":
                last_asst = rm
        has_tool = any(rm.get("role") == "tool" for rm in chunk)
        if last_asst is None:
            if has_tool:
                out.append({"role": "assistant", "content": PURE_WINDOW_NO_FINAL_ASSISTANT})
            continue
        txt = _assistant_display_content_for_sse(
            str(last_asst.get("content") or ""),
            str(last_asst.get("reasoning_content") or ""),
        ).strip()
        tc = bool(last_asst.get("tool_calls"))
        if txt:
            out.append({"role": "assistant", "content": txt})
        elif has_tool or tc:
            out.append({"role": "assistant", "content": PURE_WINDOW_NO_FINAL_ASSISTANT})
        else:
            out.append({"role": "assistant", "content": ""})
    return out

def _has_new_peer_messages_after_turn(conversation_id: str, turn_start_ids: Set[str]) -> bool:
    cid = str(conversation_id or "").strip()
    if not cid or not turn_start_ids:
        return False
    with get_conversation_run_lock(cid):
        _ensure_conversation_loaded(cid)
        for m in CONVERSATIONS.get(cid) or []:
            if not isinstance(m, dict):
                continue
            if str(m.get("role") or "") != "user" or not m.get("_agent_peer_message"):
                continue
            mid = str(m.get("_agent_message_id") or "").strip()
            if not mid or mid not in turn_start_ids:
                return True
    return False

def _include_message_for_api(msg: Dict[str, Any]) -> bool:
    """落盘专用内部行（如 requires_reply 哨兵）不进入 LLM 请求。"""
    if not isinstance(msg, dict):
        return False
    if msg.get("_requires_reply_sentinel"):
        return False
    return True

def _insert_summary_message(
    cid: str, messages: List[Dict[str, Any]], body: str
) -> None:
    fu = _find_first_user_index(messages)
    ins = fu if fu is not None else len(messages)
    summary_msg = {
        "role": "system",
        "content": "【历史摘要】\n" + str(body or "").strip(),
        "_agent_summary": True,
    }
    messages.insert(ins, summary_msg)
    if cid:
        try:
            fp = _conversation_file_for_save(cid)
            _append_raw_message(fp, summary_msg)
        except Exception as e:
            print(
                f"WARN: append summary to .raw failed cid={cid}: {e}",
                file=sys.stderr,
                flush=True,
            )

def _is_degenerate_summary_body(body: str) -> bool:
    """无实质摘要：空串，或极短且仅含「摘要为空」类占位（避免误伤正文中提及该短语的长摘要）。"""
    b = str(body or "").strip()
    if not b:
        return True
    if len(b) < 10 and "摘要为空" in b:
        return True
    return False

def _mark_excerpt_merged(p: Path) -> None:
    try:
        raw = p.read_text(encoding="utf-8")
        blob, body = _parse_excerpt_file(raw)
        if not isinstance(blob, dict):
            return
        am = blob.get("agent_excerpt_meta")
        if not isinstance(am, dict):
            return
        am = dict(am)
        am["merged"] = True
        am["merged_at"] = int(time.time() * 1000)
        header = "---\n" + json.dumps({"agent_excerpt_meta": am}, ensure_ascii=False) + "\n---\n\n"
        p.write_text(header + body, encoding="utf-8")
    except Exception as exc:
        print(f"WARN: mark excerpt merged failed {p}: {exc}", file=sys.stderr, flush=True)

def _maybe_schedule_summarization(cid: str, messages: List[Dict[str, Any]]) -> None:
    """阈值触发异步摘要：区间与 token 判断经 ContextManager（ContextState.java / asyncExcerpt 路径）。"""
    cm = _context_manager_v2(cid)
    cm.rebuild_from_persisted(messages, _build_context_segments, cid)
    with _SUMMARY_STATE_LOCK:
        alive = SUMMARY_IN_PROGRESS.get(cid)
        if alive is not None and time.time() >= alive:
            SUMMARY_IN_PROGRESS.pop(cid, None)
            alive = None
        total_tokens = cm.total_token_estimate(_approx_tokens_message)
        if total_tokens <= cm.summary_token_threshold:
            return
        if alive is not None:
            return
        rng = cm.dialogue_summary_excerpt_half_open(messages)
        if rng is None:
            return
        s_adj, e_adj = rng[0], rng[1]
        if s_adj >= e_adj:
            return
        excerpt_src = messages[s_adj:e_adj]
        excerpt_round_ids = _round_ids_from_messages(excerpt_src)
        slice_copy = copy.deepcopy(excerpt_src)
        slice_copy = _strip_tool_trace_for_summary(slice_copy)
        SUMMARY_IN_PROGRESS[cid] = time.time() + SUMMARY_IN_PROGRESS_TTL_SEC

    def _run() -> None:
        try:
            text = _summarize_messages_slice_with_llm(slice_copy, cid)
            if not str(text or "").strip():
                return
            ts = int(time.time() * 1000)
            EXCERPTS_DIR.mkdir(parents=True, exist_ok=True)
            path = EXCERPTS_DIR / f"{cid}_{ts}.md"
            meta = {
                "round_ids": excerpt_round_ids,
                "conversation_id": cid,
                "end_exclusive": True,
                "start_idx": s_adj,
                "end_idx": e_adj,
            }
            header = "---\n" + json.dumps({"agent_excerpt_meta": meta}, ensure_ascii=False) + "\n---\n\n"
            path.write_text(header + text, encoding="utf-8")
            with _SUMMARY_STATE_LOCK:
                PENDING_EXCERPT_PATHS.setdefault(cid, []).append(str(path.resolve()))
        except Exception as exc:
            print(f"WARN: summarization failed for {cid}: {exc}", file=sys.stderr, flush=True)
        finally:
            with _SUMMARY_STATE_LOCK:
                SUMMARY_IN_PROGRESS.pop(cid, None)

    threading.Thread(target=_run, daemon=True).start()

def _merge_one_excerpt_file(p: Path, messages: List[Dict[str, Any]], cid: str = "") -> bool:
    try:
        raw = p.read_text(encoding="utf-8")
    except Exception:
        return False
    blob, body = _parse_excerpt_file(raw)
    am = blob.get("agent_excerpt_meta") if isinstance(blob, dict) else None
    if not isinstance(am, dict):
        return False
    round_ids = _excerpt_meta_round_ids({"agent_excerpt_meta": am})
    if round_ids:
        n_before = len(messages)
        _remove_messages_by_round_ids(messages, round_ids)
        if len(messages) == n_before:
            return False
        _insert_summary_message(cid, messages, body)
        return True
    try:
        s = int(am["start_idx"])
        e = int(am["end_idx"])
    except (KeyError, TypeError, ValueError):
        return False
    # 旧版仅下标 excerpt：上下文变短后 end_idx 仍很大时会误删整段会话
    if e > len(messages) or s >= len(messages):
        return False
    s, e = _adjust_excerpt_range_half_open(messages, s, e)
    if s >= len(messages):
        return False
    if s >= e or _range_is_only_summaries(messages, s, e):
        return False
    e = min(e, len(messages))
    del messages[s:e]
    _insert_summary_message(cid, messages, body)
    return True

def _merge_pending_excerpts_for_conversation(
    cid: str, messages: List[Dict[str, Any]], *, persist: bool = True
) -> None:
    if not cid:
        return
    try:
        _merge_pending_excerpts_for_conversation_impl(cid, messages, persist=persist)
    except Exception as exc:
        print(
            f"WARN: merge pending excerpts failed cid={cid}: {exc}",
            file=sys.stderr,
            flush=True,
        )

def _merge_pending_excerpts_for_conversation_impl(
    cid: str, messages: List[Dict[str, Any]], *, persist: bool = True
) -> None:
    paths = _collect_excerpt_paths_to_merge(cid)
    if not paths:
        return
    changed = False
    for p in paths:
        rid_present = {
            str(m.get("_agent_round_id") or "").strip()
            for m in messages
            if isinstance(m, dict) and m.get("_agent_round_id")
        }
        if _merge_one_excerpt_file(p, messages, cid):
            _mark_excerpt_merged(p)
            changed = True
        elif not _excerpt_file_needs_merge(p):
            continue
        else:
            try:
                raw = p.read_text(encoding="utf-8")
                blob, _ = _parse_excerpt_file(raw)
                am = blob.get("agent_excerpt_meta") if isinstance(blob, dict) else None
                if isinstance(am, dict):
                    round_ids = _excerpt_meta_round_ids({"agent_excerpt_meta": am})
                    if round_ids and not any(r in rid_present for r in round_ids):
                        _mark_excerpt_merged(p)
                        continue
                    s = int(am.get("start_idx", 0))
                    e = int(am.get("end_idx", 0))
                    if (
                        s >= len(messages)
                        or s >= e
                        or (not round_ids and e > len(messages))
                    ):
                        _mark_excerpt_merged(p)
            except Exception:
                pass
    if changed:
        CONVERSATIONS[cid] = messages
        if persist:
            _save_conversation(cid, messages)

def _message_id_set(msgs: List[Dict[str, Any]]) -> Set[str]:
    out: Set[str] = set()
    for m in msgs:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("_agent_message_id") or "").strip()
        if mid:
            out.add(mid)
    return out

def _normalize_persisted_conversation(messages: List[Dict[str, Any]]) -> None:
    if not messages:
        messages.append({"role": "system", "content": TOOL_AGENT_SYSTEM_PROMPT})
        return
    if messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": TOOL_AGENT_SYSTEM_PROMPT})
    else:
        messages[0] = {"role": "system", "content": TOOL_AGENT_SYSTEM_PROMPT}

def _parse_excerpt_file(raw: str) -> Tuple[dict, str]:
    lines = raw.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}, raw
    try:
        meta = json.loads(lines[1])
    except Exception:
        return {}, raw
    if not isinstance(meta, dict):
        return {}, raw
    if len(lines) < 3 or lines[2].strip() != "---":
        return meta, raw
    body = "\n".join(lines[4:]).lstrip("\n") if len(lines) > 4 else ""
    return meta, body

def _range_is_only_summaries(messages: List[Dict[str, Any]], s: int, e: int) -> bool:
    if s >= e or s < 0:
        return True
    e = min(e, len(messages))
    for i in range(s, e):
        m = messages[i]
        if m.get("role") != "system" or not m.get("_agent_summary"):
            return False
    return True

def _reconcile_peer_messages_from_store(conversation_id: str, messages: List[Dict[str, Any]]) -> bool:
    """CONVERSATIONS 与 turn 本地列表分叉时，把 store 多出的消息并入本地（避免 peer append 被覆盖丢失）。"""
    cid = str(conversation_id or "").strip()
    if not cid:
        return False
    with get_conversation_run_lock(cid):
        stored = CONVERSATIONS.get(cid)
        if not stored or stored is messages:
            return False
        seen = _message_id_set(messages)
        added = False
        for m in stored:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("_agent_message_id") or "").strip()
            if mid and mid in seen:
                continue
            messages.append(copy.deepcopy(m))
            if mid:
                seen.add(mid)
            added = True
        if added:
            CONVERSATIONS[cid] = messages
    return added

def _resume_turn_for_pending_peer_messages(conversation_id: str) -> None:
    """turn / inbox drain 结束后：若本 turn 期间有新 peer 入站且当前无 run，自动 resume 处理。"""
    cid = str(conversation_id or "").strip()
    if not cid:
        return
    with get_conversation_run_lock(cid):
        start_ids = _TURN_START_MESSAGE_IDS.pop(cid, None)
        if not start_ids:
            return
        busy = bool(_ACTIVE_CONVERSATION_RUNS.get(cid))
    if busy:
        return
    if not _has_new_peer_messages_after_turn(cid, start_ids):
        return
    start_background_agent_turn(cid, "", resume_after_user_confirm=True, peer_triggered=True)

def _sanitize_tool_pairing_for_api(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """发送前修正 tool_calls 与 tool 配对：缺一条时补占位 tool；缺多条或孤儿 tool 则删整条或删孤儿。"""
    work = copy.deepcopy(msgs)
    for _ in range(12):
        changed = False
        i = 0
        while i < len(work):
            m = work[i]
            if m.get("role") == "tool":
                prev = work[i - 1] if i > 0 else None
                prev_ok = (
                    isinstance(prev, dict)
                    and prev.get("role") == "assistant"
                    and isinstance(prev.get("tool_calls"), list)
                    and prev["tool_calls"]
                )
                if not prev_ok:
                    del work[i]
                    changed = True
                    continue
                i += 1
                continue
            if m.get("role") == "assistant" and isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
                need_ids = _assistant_tool_call_ids(m)
                if not need_ids:
                    mm = {k: v for k, v in m.items() if k != "tool_calls"}
                    work[i] = mm
                    changed = True
                    continue
                j = i + 1
                tool_row_idxs: List[int] = []
                while j < len(work) and work[j].get("role") == "tool":
                    tool_row_idxs.append(j)
                    j += 1
                orphan_idxs = [
                    idx
                    for idx in tool_row_idxs
                    if str(work[idx].get("tool_call_id") or "") not in need_ids
                ]
                if orphan_idxs:
                    for idx in sorted(orphan_idxs, reverse=True):
                        del work[idx]
                    changed = True
                    continue
                answered = {
                    str(work[idx].get("tool_call_id") or "")
                    for idx in tool_row_idxs
                    if str(work[idx].get("tool_call_id") or "") in need_ids
                }
                missing = [tid for tid in sorted(need_ids) if tid not in answered]
                if not missing:
                    i = j
                    continue
                if len(missing) <= TOOL_PAIRING_SYNTH_MAX_MISSING:
                    ins = j
                    for tid in missing:
                        work.insert(ins, _synthetic_tool_result(tid))
                        ins += 1
                    changed = True
                    i = ins
                    continue
                del work[i:j]
                changed = True
                continue
            i += 1
        if not changed:
            break
    return work

def _split_pure_and_full_dialogue(
    dialogue: List[Dict[str, Any]],
    full_n: int,
    pure_n: int,
    has_compressed: bool = False,
    conversation_id: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """远期：仅当已触发过摘要压缩后才存在——取对话前 pure_n 个 user 回合并锚定不动，
    保障 KV 缓存前缀稳定。未压缩时远期恒为空，新对话全归近期（铁律）。

    近期：未压缩时 = 全部对话；已压缩后 = 远期锚定区之后的所有 user 回合（含工具），
    随对话自然增长。
    """
    if not dialogue:
        return [], [], []

    user_idxs = [i for i, m in enumerate(dialogue) if m.get("role") == "user"]
    k = len(user_idxs)
    if k == 0:
        return [], [], []

    fn = max(1, int(full_n))
    pn = max(0, int(pure_n))

    # ── 铁律：未压缩过 → 远期恒空，全部归近期 ──
    if not has_compressed or pn <= 0:
        _PURE_ANCHOR_CACHE.pop(conversation_id, None)
        return [], [], list(dialogue)

    # ── 已压缩过：远期锚定 ──
    if k <= fn + pn:
        # 从缓存取锚定值；首次压缩时计算并缓存
        cached_pure = _PURE_ANCHOR_CACHE.get(conversation_id)
        if cached_pure is not None:
            pure_count = cached_pure
        else:
            pure_count = max(0, k - fn)
            if pure_count > 0 and conversation_id:
                _PURE_ANCHOR_CACHE[conversation_id] = pure_count
        if pure_count == 0 or pure_count >= k:
            return [], [], list(dialogue)
        pure_end = user_idxs[pure_count]
    else:
        # 远期锚定为前 pn 轮，近期自然增长
        pure_end = user_idxs[pn]                        # 第 pn 个 user = 前 pn 轮 → 远期（锚定！）

    pure_raw = list(dialogue[:pure_end])
    full_tail = list(dialogue[pure_end:])
    return pure_raw, [], full_tail

def _stored_mode_for_tail(conversation_id: str) -> str:
    m = CONVERSATION_MODES.get(conversation_id)
    if m == "plan":
        return "plan"
    if m == "execute":
        return "execute"
    return "auto"

def _strip_internal_message_for_api(msg: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(msg)
    for k in list(out.keys()):
        if str(k).startswith("_agent_"):
            out.pop(k, None)
    # thinking 模式：历史 assistant 必须原样回传 reasoning_content，不可因 content 非空而剥离
    return out

def _strip_tool_trace_for_summary(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """摘要模型输入：去掉 tool 消息；user/assistant/system 仅保留 content，不截断正文。"""
    out: List[Dict[str, Any]] = []
    for m in msgs:
        r = m.get("role")
        if r == "tool":
            continue
        if r == "user":
            out.append({"role": "user", "content": str(m.get("content") or "")})
        elif r == "assistant":
            out.append(
                {
                    "role": "assistant",
                    "content": _assistant_display_content_for_sse(
                        str(m.get("content") or ""),
                        str(m.get("reasoning_content") or ""),
                    ),
                }
            )
        elif r == "system":
            out.append({"role": "system", "content": str(m.get("content") or "")})
        else:
            out.append({"role": str(r or "user"), "content": str(m.get("content") or "")})
    return out

def _summarize_messages_slice_with_llm(slice_msgs: List[Dict[str, Any]], cid: str = "") -> str:
    sys_h = (
        "你是对话整理助手，任务是对下列「历史聊天记录」做摘要提取，不是续写对话、不是执行工具、不要输出工具调用。\n"
        "1) 识别对话场景（如开发、排障、写文档、数据分析等），按场景保留高价值信息。\n"
        "2) 降噪：去掉寒暄与无信息套话；多处矛盾时以用户最终意图与最后澄清为准；重复尝试可合并为一句。\n"
        "3) 事实粒度：保留可执行信息——路径、命令、版本号、明确数字、用户硬性约束（必须/禁止等）。\n"
        "4) 未完成：单独列出仍待处理或待用户确认的事项；已放弃的方案一句话带过即可。\n"
        "5) 输出：纯文本中文；建议分节（背景 / 关键结论 / 约束与约定 / 未完成与待确认 / 风险与注意点）；"
        f"总篇幅不超过约 {SUMMARY_OUTPUT_MAX_CHARS} 字，在限制内尽量保留关键细节与可执行信息。\n"
        "6) 脉络连贯：关注「用户要什么 → 做了什么 → 得到什么结论」的因果链，不要只罗列事实；对每个关键结论尽量保留。\n"
        "7) 禁止编造：不得引入记录中未出现的文件名、结论或数字；不确定处请写「未在记录中明确」。\n"
        "8) 若剔除噪声后确实无可保留的实质信息：请仅输出「摘要为空」五个字（不要加标点或换行），"
        "使全文总字符数少于 10；不要输出其它占位或解释。"
    )
    reff = _get_reasoning_effort(cid)
    th_type = "enabled" if SUMMARY_THINKING_ENABLED else "disabled"
    payload = {
        "model": default_model_from_env(),
        "messages": [
            {"role": "system", "content": sys_h},
            {"role": "user", "content": json.dumps(slice_msgs, ensure_ascii=False)},
        ],
        "reasoning_effort": reff,
        "thinking": {"type": th_type},
        "temperature": 0.2,
    }
    data = deepseek_request(payload)
    choices = data.get("choices") or []
    ch0 = choices[0] if choices else {}
    msg = (ch0 or {}).get("message") or {}
    c = msg.get("content")
    body = str(c or "").strip()
    if _is_degenerate_summary_body(body):
        return ""
    return body + _AGENT_SUMMARY_BRIDGE_TAIL

def _synthetic_tool_result(tool_call_id: str) -> Dict[str, Any]:
    return {"role": "tool", "tool_call_id": str(tool_call_id), "content": _TOOL_REPAIR_BODY}

def _tail_drop_incomplete_tool_assistant(messages: List[Dict[str, Any]]) -> None:
    """删除尾部未配齐每个 tool_call_id 对应 tool 消息的 assistant(tool_calls)及其后续不完整 tool 行。
    避免 execute_tool_script 卡死时已 append assistant 导致全局 CONVERSATIONS 与下轮 user 相连触发 API 报错。"""
    while messages:
        idx = -1
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if m.get("role") == "assistant" and isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
                idx = i
                break
        if idx < 0:
            return
        tc_list = messages[idx].get("tool_calls") or []
        need_ids = {
            str((t or {}).get("id") or "")
            for t in tc_list
            if isinstance(t, dict) and (t or {}).get("id")
        }
        if not need_ids:
            del messages[idx]
            continue
        j = idx + 1
        answered: Set[str] = set()
        while j < len(messages):
            row = messages[j]
            if row.get("role") != "tool":
                break
            tid = str(row.get("tool_call_id") or "")
            if tid in need_ids:
                answered.add(tid)
            j += 1
        if need_ids.issubset(answered):
            return
        del messages[idx:j]

def messages_for_history_api(cid: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """返回 (界面展示用原始消息, 上下文布局用合并预览)。打开历史时不写盘、不破坏 json。"""
    _ensure_conversation_loaded(cid)
    stored = list(CONVERSATIONS.get(cid) or [])
    if not stored:
        loaded = _load_conversation(cid)
        if loaded:
            stored = list(loaded)
            CONVERSATIONS[cid] = loaded
    layout_preview = copy.deepcopy(stored)
    _merge_pending_excerpts_for_conversation(cid, layout_preview, persist=False)
    return stored, layout_preview

