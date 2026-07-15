# -*- coding: utf-8
"""agent_v4.core.conversation_store"""
from __future__ import annotations

from agent_v4.core.deps import *  # noqa: F403
from agent_v4.core.shared_state import *  # noqa: F403
import threading

def _append_session_message_v2(
    cid: str,
    messages: List[Dict[str, Any]],
    msg: Dict[str, Any],
    *,
    new_round: bool = False,
    round_id: Optional[str] = None,
) -> str:
    """追加一条消息：写入轮次/消息 ID，并追加到 {session}.raw。"""
    rid = _stamp_message_v2(msg, new_round=new_round, round_id=round_id)
    messages.append(msg)
    if cid:
        try:
            fp = _conversation_file_for_save(cid)
            _append_raw_message(fp, msg)
        except Exception as e:
            print(
                f"WARN: append session .raw failed cid={cid}: {e}",
                file=sys.stderr,
                flush=True,
            )
    return rid

def _best_message_reasoning_field(last_message: Dict[str, Any]) -> str:
    best = ""
    for name in _reasoning_delta_field_names():
        v = last_message.get(name)
        if isinstance(v, str) and v and len(v) > len(best):
            best = v
    return best

def _chat_history_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            text = content
            had_images = False
            if role == "user":
                try:
                    from agent_v4.core.attachments import strip_attachment_footer_for_ui

                    text, had_footer = strip_attachment_footer_for_ui(content)
                    had_images = bool(had_footer) or bool(m.get("_attachments"))
                except Exception:
                    text = content
                    had_images = bool(m.get("_attachments"))
                if not had_images and ("[图片 ×" in text or "[图片 x" in text):
                    had_images = True
            item: Dict[str, Any] = {"role": role, "content": text}
            if had_images:
                item["had_images"] = True
                raw_atts = m.get("_attachments")
                if isinstance(raw_atts, list):
                    pub: List[Dict[str, Any]] = []
                    for a in raw_atts:
                        if not isinstance(a, dict):
                            continue
                        aid = str(a.get("id") or "").strip()
                        if not aid:
                            continue
                        pub.append({"id": aid, "name": str(a.get("name") or aid)})
                    if pub:
                        item["attachments"] = pub
            for k in ("_sender", "_sender_name", "_sender_role", "_priority", "_agent_peer_message"):
                if k in m:
                    item[k] = m.get(k)
            out.append(item)
            continue
        rc = m.get("reasoning_content")
        if isinstance(rc, str) and rc.strip():
            item = {"role": role, "content": rc}
            for k in ("_sender", "_sender_name", "_sender_role", "_priority", "_agent_peer_message"):
                if k in m:
                    item[k] = m.get(k)
            out.append(item)
            continue
        continue
    return out[-UI_RESTORE_MAX_CHAT_ITEMS:]

def _clean_conversation_title(text: str) -> str:
    title = str(text or "").strip()
    title = re.sub(r"^[「『\"'`《【\[]+|[」』\"'`》】\]]+$", "", title).strip()
    title = re.sub(r"^(标题|会话标题)\s*[:：]\s*", "", title).strip()
    title = re.sub(r"\s+", "", title)
    if not title:
        return "新会话"
    return title[:18]

def _conversation_file_for_save(cid: str) -> Path:
    def _default_for_new(c: str) -> Path:
        day = time.strftime("%Y-%m-%d", time.localtime())
        return SESSION_DIR / day / f"{c}.json"

    return _resolve_session_json_path(cid, _find_conversation_file, _default_for_new)

def _enrich_tool_error_message(script_name: str, message: str) -> str:
    if not message:
        message = ""
    low = message.lower()
    if "\n--help:\n" in message or "usage:" in low or "optional arguments:" in low or "options:" in low:
        return message
    h = _subprocess_tool_help(script_name)
    if not h:
        return message
    return f"{message}\n\n--help:\n{h}"

def _enrich_tool_result_error(script_name: str, result: dict) -> dict:
    # If CLI returns ok:false and message lacks --help, append this script help.
    if not isinstance(result, dict) or result.get("ok"):
        return result
    err = result.get("error")
    if not isinstance(err, dict):
        return result
    msg = err.get("message")
    if not isinstance(msg, str):
        return result
    new_m = _enrich_tool_error_message(script_name, msg)
    if new_m != msg:
        err2 = dict(err)
        err2["message"] = new_m
        out = dict(result)
        out["error"] = err2
        return out
    return result

def _execute_run_type(conversation_id: str, exec_args: Dict[str, Any]) -> dict:
    rt = str(exec_args.get("run_type") or "").strip().lower()
    cid = str(conversation_id or "")
    # 不传 run_type 则为查询模式
    if not rt or rt not in {"auto", "plan", "execute"}:
        mode = CONVERSATION_MODES.get(cid, "auto")
        return {"ok": True, "data": {"run_type": mode, "action": "query"}}
    # 切换模式
    if rt == "auto":
        CONVERSATION_MODES.pop(cid, None)
    else:
        CONVERSATION_MODES[cid] = rt
    return {"ok": True, "data": {"run_type": rt, "action": "switch"}}

def _fallback_title_from_messages(cid: str, messages: List[Dict[str, Any]]) -> str:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            c = str(m.get("content") or "").strip()
            if c:
                return _clean_conversation_title(c)
    return f"会话 {cid[:8]}"

def _find_conversation_file(cid: str) -> Optional[Path]:
    if not cid:
        return None
    flat = SESSION_DIR / f"{cid}.json"
    if flat.is_file():
        return flat
    try:
        for p in SESSION_DIR.glob(f"*/{cid}.json"):
            if p.is_file():
                return p
    except Exception:
        pass
    return None

def _find_title_file(cid: str) -> Optional[Path]:
    """查找 cid 对应的 .title 文件（同目录同级）"""
    if not cid:
        return None
    # 先找 session 文件所在目录下的 .title 文件
    sfile = _find_conversation_file(cid)
    if sfile is not None:
        tfile = sfile.with_suffix(".title")
        if tfile.is_file():
            return tfile
    # 再平铺查找
    flat = SESSION_DIR / f"{cid}.title"
    if flat.is_file():
        return flat
    try:
        for p in SESSION_DIR.glob(f"*/{cid}.title"):
            if p.is_file():
                return p
    except Exception:
        pass
    return None

def _generate_conversation_title(cid: str, messages: List[Dict[str, Any]]) -> str:
    history = _chat_history_from_messages(messages)[:8]
    if not history:
        return "新会话"
    compact = []
    for item in history:
        role = "用户" if item["role"] == "user" else "助手"
        compact.append(f"{role}: {item['content'][:1000]}")
    reff = _get_reasoning_effort(cid)
    payload = {
        "model": effective_model(cid),
        "messages": [
            {"role": "system", "content": "你是会话标题生成器。请根据对话内容生成一个简短中文标题，6到16个字，不要标点，不要引号，不要解释。"},
            {"role": "user", "content": "\n\n".join(compact)},
        ],
        "reasoning_effort": reff,
        "thinking": {"type": "enabled"},
        "temperature": 0.2,
    }
    data = deepseek_request(payload)
    choices = data.get("choices") or []
    msg = (choices[0] if choices else {}).get("message") or {}
    return _clean_conversation_title(str(msg.get("content") or "新会话"))

def _intent_tool_hints(key_lower: str, names: List[str]) -> List[str]:
    """未知工具名时按常见臆造后缀给出可读推荐，避免 closest-match 跑偏到无关工具。"""
    hit: List[str] = []
    if any(
        s in key_lower
        for s in (
            "directory",
            "dir_list",
            "list_dir",
            "folder",
            "ls",
        )
    ):
        for c in ("glob_files", "file_ops", "grep_files"):
            if c in names:
                hit.append(c)
    if any(
        s in key_lower
        for s in (
            "find_replace",
            "replace",
            "substitute",
            "sed",
            "rewrite",
        )
    ):
        for c in ("replace_in_file", "read_write", "apply_patch", "write_file"):
            if c in names:
                hit.append(c)
    seen: Set[str] = set()
    out: List[str] = []
    for x in hit:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[:6]

def _is_placeholder_conversation_title(title: str) -> bool:
    """未命名占位标题：空、默认 tab 文案、生成失败占位。"""
    t = str(title or "").strip()
    if not t or t == "新会话" or t == "生成标题中…":
        return True
    if re.match(r"^会话\s+[A-Za-z0-9._:-]{8}$", t):
        return True
    return False

def _load_conversation(cid: str) -> Optional[List[Dict[str, Any]]]:
    if not cid:
        return None
    try:
        fp = _find_conversation_file(cid)
        if fp is None or not fp.is_file():
            return None
        raw_text = fp.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
        decrypted = _decrypt_session_payload(raw)
        if decrypted is not None:
            raw_text = decrypted.decode("utf-8")
            raw = json.loads(raw_text)
        if isinstance(raw, list) and all(isinstance(m, dict) for m in raw):
            _cache_session_json_path(cid, fp)
            _ensure_conversation_message_ids_v2(raw)
            try:
                _bootstrap_raw_from_messages(fp, raw)
            except Exception as boot_exc:
                print(
                    f"WARN: bootstrap .raw failed cid={cid}: {boot_exc}",
                    file=sys.stderr,
                    flush=True,
                )
            if not _chat_history_from_messages(raw):
                recovered = _load_messages_from_raw(fp)
                if recovered and _chat_history_from_messages(recovered):
                    print(
                        f"INFO: restored session messages from .raw cid={cid}",
                        file=sys.stderr,
                        flush=True,
                    )
                    _ensure_conversation_message_ids_v2(recovered)
                    return recovered
            return raw
        return None
    except Exception as load_exc:
        print(f"WARN: load conversation failed cid={cid}: {load_exc}", file=sys.stderr, flush=True)
        return None

def _load_conversation_title(cid: str) -> str:
    """从独立 .title 文件读取标题，没有则返回空"""
    try:
        tfile = _find_title_file(cid)
        if tfile is not None and tfile.is_file():
            title = tfile.read_text(encoding="utf-8").strip()[:80]
            if title:
                return title
    except Exception as exc:
        import sys
        print(f"WARN: _load_conversation_title failed for {cid}: {exc}", file=sys.stderr, flush=True)
    return ""

def _load_last_open_session_state() -> Dict[str, Any]:
    try:
        if not LAST_OPEN_SESSION_STATE_FILE.is_file():
            return {}
        state = json.loads(LAST_OPEN_SESSION_STATE_FILE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}

def _persisted_session_unreadable_after_load(cid: str) -> bool:
    """若 DATA_ROOT 下已存在该 cid 的 json 持久化文件，但无法加载为消息列表，且内存中无历史，则视为不可恢复，必须拒绝后续对话。"""
    key = str(cid or "").strip()
    if not key:
        return False
    if CONVERSATIONS.get(key):
        return False
    fp = _find_conversation_file(key)
    if fp is None or not fp.is_file():
        return False
    if _load_conversation(key) is not None:
        return False
    print(f"WARN: session file exists but is unreadable (refuse chat to avoid overwrite); cid={key} path={fp}", file=sys.stderr, flush=True)
    return True

def _reasoning_delta_field_names() -> List[str]:
    raw = str(AGENT_CONFIG.get("AGENT_REASONING_DELTA_FIELDS") or "").strip()
    names = [x.strip() for x in raw.replace(",", " ").split() if x.strip()]
    return names if names else ["reasoning_content", "reasoning"]

def _safe_json_loads(s: str) -> Optional[dict]:
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except Exception:
        return None

def _save_conversation(cid: str, messages: List[Dict[str, Any]], title: str = "") -> None:
    if not cid:
        return
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        fp = _conversation_file_for_save(cid)
        fp.parent.mkdir(parents=True, exist_ok=True)
        _bootstrap_raw_from_messages(fp, messages)
        # 仅存储 messages JSON，不再内嵌标题
        plain = json.dumps(messages, ensure_ascii=False)
        envelope = _encrypt_session_payload(plain.encode("utf-8"))
        if envelope is None:
            fp.write_text(plain, encoding="utf-8")
        else:
            fp.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        # 如果有标题则独立写入 .title 文件
        if title:
            _save_title_file(cid, title)
    except Exception as e:
        print(f"WARN: failed to save conversation {cid}: {e}", file=sys.stderr, flush=True)

def _save_last_open_session_state(state: Dict[str, Any]) -> None:
    try:
        LAST_OPEN_SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_OPEN_SESSION_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"WARN: failed to save last open session state: {e}", file=sys.stderr, flush=True)

def _save_title_file(cid: str, title: str, overwrite: bool = True) -> None:
    """将标题写入独立 .title 文件，不加密。
    overwrite=False 时若文件已存在则不覆盖。"""
    if not cid or not title:
        return
    try:
        # 优先放在 session 文件同目录
        sfile = _find_conversation_file(cid)
        if sfile is not None:
            tfile = sfile.with_suffix(".title")
        else:
            day = time.strftime("%Y-%m-%d", time.localtime())
            tfile = SESSION_DIR / day / f"{cid}.title"
            tfile.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite and tfile.is_file() and tfile.read_text(encoding="utf-8").strip():
            return
        tfile.write_text(title.strip()[:80], encoding="utf-8")
    except Exception as e:
        print(f"WARN: failed to save title file for {cid}: {e}", file=sys.stderr, flush=True)

def _session_date_group_from_path(fp: Path) -> str:
    parent = fp.parent.name
    return parent if re.match(r"^\d{4}-\d{2}-\d{2}$", parent) else ""

def _subprocess_tool_help(script_name: str) -> str:
    """历史名：现仅从 catalog 取 tool_help，不再调用工具 main/--help。"""
    try:
        from agent_v4.core.tool_runtime import _capture_tool_help_from_catalog

        return str(_capture_tool_help_from_catalog(script_name) or "")
    except Exception:
        return ""

def _unknown_tool_result(api_name: Any, script_by_api: Dict[str, str]) -> dict:
    key = str(api_name or "").strip()
    names = sorted(script_by_api.keys())
    lines = [f"unknown tool {key!r}", "", f"已注册的 function 名称（共 {len(names)} 个，字典序节选）："]
    cap = 80
    if len(names) <= cap:
        lines.append(", ".join(names))
    else:
        lines.append(", ".join(names[:cap]) + f" … 另有 {len(names) - cap} 个未列出，请查看 tools/tool_list_agent.json")
    intent = _intent_tool_hints(key.lower(), names)
    if intent:
        lines.extend(["", "按调用意图推荐（优先尝试）：" + ", ".join(intent)])
    if key:
        close = difflib.get_close_matches(key, names, n=5, cutoff=0.34)
        if not close and key.endswith("s") and len(key) > 1:
            close = difflib.get_close_matches(key[:-1], names, n=5, cutoff=0.45)
        for c in close[:1]:
            scr = script_by_api.get(c) or ""
            h = _subprocess_tool_help(scr) if scr else ""
            if h:
                lines.extend(["", f"与 {key!r} 最接近的已注册名称：{c!r}（脚本 {scr}），该脚本 --help：", h])
                break
            if scr:
                lines.extend(["", f"与 {key!r} 最接近的已注册名称：{c!r}（脚本 {scr}）；未能捕获 --help 文本"])
                break
    msg = "\n".join(lines)
    return {"ok": False, "data": None, "error": {"type": "UnknownTool", "message": msg}}

