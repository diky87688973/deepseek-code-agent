# -*- coding: utf-8 -*-
"""FastAPI HTTP 路由：显式依赖 agent_v4.agent_core，无动态 exec。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

from agent_v4 import agent_core as core
from agent_v4 import route_helpers as rh
from agent_v4.live_state import get_conversation_run_lock
from agent_v4.http_schemas import (
    ChatIn,
    ChatStopIn,
    ChatTitleIn,
    ChatUiStateIn,
    ChatCommandInputIn,
    ChatUserConfirmIn,
    KbCheckedIn,
    UsageAccumIn,
)
from agent_v4.version import AGENT_APP_VERSION
from util.http_pipeline_v2 import resolve_client_ip_from_request
from agent_v4.live_state import open_global_sse_channel, next_global_sse_event, is_global_sse_current

router = APIRouter()

_IMMERSIVE_HTML = core.AGENT_ROOT / "res" / "html" / "agent-immersive.html"
_IMMERSIVE_HTML_BODY = (
    _IMMERSIVE_HTML.read_text(encoding="utf-8").replace("{{APP_VERSION}}", AGENT_APP_VERSION)
    if _IMMERSIVE_HTML.is_file()
    else ""
)

_SIDER_HTML = core.AGENT_ROOT / "res" / "html" / "agent-sider.html"
_SIDER_HTML_BODY = (
    _SIDER_HTML.read_text(encoding="utf-8").replace("{{APP_VERSION}}", AGENT_APP_VERSION)
    if _SIDER_HTML.is_file()
    else ""
)


@router.get("/immersive", include_in_schema=False)
def immersive_index() -> HTMLResponse:
    if not _IMMERSIVE_HTML_BODY:
        raise HTTPException(404, "immersive UI not found")
    return HTMLResponse(_IMMERSIVE_HTML_BODY, media_type="text/html; charset=utf-8")


@router.get("/sider", include_in_schema=False)
def sider_index() -> HTMLResponse:
    if not _SIDER_HTML_BODY:
        raise HTTPException(404, "sider UI not found")
    return HTMLResponse(_SIDER_HTML_BODY, media_type="text/html; charset=utf-8")


@router.get("/")
def index() -> HTMLResponse:
    return HTMLResponse(core.INLINE_UI_HTML, media_type="text/html; charset=utf-8")


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@router.get("/api/model-pricing")
def model_pricing(conversation_id: str = "", model: str = "") -> Any:
    return core.get_model_pricing_snapshot(conversation_id, model)


@router.get("/api/usage-accumulator")
def usage_accumulator_get() -> Any:
    return core._load_usage_accumulator()


@router.put("/api/usage-accumulator")
def usage_accumulator_put(body: UsageAccumIn) -> Dict[str, bool]:
    core._save_usage_accumulator(body.model_dump())
    return {"ok": True}


@router.get("/api/chat/history")
def chat_history(conversation_id: str = "") -> Dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    stored, layout_messages = core.messages_for_history_api(cid)
    context_layout = rh.context_layout_event(cid, layout_messages)
    todo_list = None
    try:
        todo_r = core._todo_list_mod.execute(cid, {"action": "query"})
        if todo_r.get("ok") and todo_r.get("data") is not None:
            todo_list = todo_r["data"]
    except Exception:
        pass
    return {
        "ok": True,
        "conversation_id": cid,
        "items": core._chat_history_from_messages(stored),
        "todo_list": todo_list,
        "context_layout": context_layout,
    }


@router.get("/api/chat/sessions")
def chat_sessions() -> Dict[str, Any]:
    state = core._load_last_open_session_state()
    title_by_id: Dict[str, str] = {}
    for t in state.get("tabs") or []:
        if isinstance(t, dict):
            cid = str(t.get("id") or "")
            title = str(t.get("title") or "").strip()
            if cid and title:
                title_by_id[cid] = title
    rows: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    try:
        title_files = list(core.SESSION_DIR.glob("*.title")) + list(core.SESSION_DIR.glob("*/*.title"))
        for tf in title_files:
            cid = tf.stem
            if not re.match(r"^[A-Za-z0-9._:-]{8,128}$", cid) or cid in seen_ids:
                continue
            seen_ids.add(cid)
            title = tf.read_text(encoding="utf-8").strip()[:80] if tf.is_file() else ""
            try:
                updated_at = int(tf.stat().st_mtime * 1000)
            except Exception:
                updated_at = 0
            rows.append(
                {
                    "id": cid,
                    "title": title or title_by_id.get(cid) or f"会话 {cid[:8]}",
                    "updated_at": updated_at,
                    "date_group": core._session_date_group_from_path(tf),
                }
            )
        json_files = list(core.SESSION_DIR.glob("*.json")) + list(core.SESSION_DIR.glob("*/*.json"))
        for fp in json_files:
            cid = fp.stem
            if not re.match(r"^[A-Za-z0-9._:-]{8,128}$", cid) or cid in seen_ids:
                continue
            seen_ids.add(cid)
            messages = core.CONVERSATIONS.get(cid)
            if messages is None:
                messages = core._load_conversation(cid) or []
            title = (
                core._load_conversation_title(cid)
                or title_by_id.get(cid)
                or core._fallback_title_from_messages(cid, list(messages))
            )
            try:
                updated_at = int(fp.stat().st_mtime * 1000)
            except Exception:
                updated_at = 0
            rows.append(
                {
                    "id": cid,
                    "title": title[:80],
                    "updated_at": updated_at,
                    "date_group": core._session_date_group_from_path(fp),
                }
            )
    except Exception:
        pass
    rows.sort(key=lambda r: (r.get("date_group") or "", r.get("updated_at", 0)), reverse=True)
    return {"ok": True, "sessions": rows}


@router.post("/api/chat/title")
def chat_title(body: ChatTitleIn) -> Dict[str, Any]:
    cid = str(body.conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    core._ensure_conversation_loaded(cid)
    existing = core._load_conversation_title(cid)
    if existing and not core._is_placeholder_conversation_title(existing):
        return {"ok": True, "conversation_id": cid, "title": existing}
    with get_conversation_run_lock(cid):
        messages = list(core.CONVERSATIONS.get(cid, []))
    title = core._generate_conversation_title(cid, messages)
    if core._is_placeholder_conversation_title(title):
        fb = core._fallback_title_from_messages(cid, messages)
        if not core._is_placeholder_conversation_title(fb):
            title = fb
    if not core._is_placeholder_conversation_title(title):
        core._save_title_file(cid, title)
    return {"ok": True, "conversation_id": cid, "title": title}


@router.get("/api/chat/ui-state")
def chat_ui_state_get() -> Dict[str, Any]:
    state = core._load_last_open_session_state()
    return {"ok": True, "state": state}


@router.put("/api/chat/ui-state")
def chat_ui_state_put(body: ChatUiStateIn) -> Dict[str, bool]:
    tabs: List[Dict[str, str]] = []
    for t in body.tabs or []:
        cid = str(t.get("id") or "").strip()
        if not re.match(r"^[A-Za-z0-9._:-]{8,128}$", cid):
            continue
        title = str(t.get("title") or "").strip()
        tabs.append({"id": cid, "title": title[:80]})
    tabs = tabs[-core.UI_RESTORE_MAX_TABS:]
    active = str(body.active_conversation_id or "").strip()
    if not re.match(r"^[A-Za-z0-9._:-]{8,128}$", active):
        active = tabs[0]["id"] if tabs else ""
    elif tabs and not any(t["id"] == active for t in tabs):
        tabs = (
            tabs[1:] + [{"id": active, "title": f"会话 {active[:8]}"}]
            if len(tabs) >= core.UI_RESTORE_MAX_TABS
            else tabs + [{"id": active, "title": f"会话 {active[:8]}"}]
        )
    state = {"active_conversation_id": active, "tabs": tabs, "updated_at": int(time.time() * 1000)}
    core._save_last_open_session_state(state)
    return {"ok": True}


@router.get("/api/reasoning-effort")
async def reasoning_effort_get(request: Request) -> Dict[str, Any]:
    cid = str(request.query_params.get("conversation_id") or "").strip()
    return {
        "ok": True,
        "reasoning_effort": core._get_reasoning_effort(cid),
        "global_default": core._get_reasoning_effort(),
    }


@router.put("/api/reasoning-effort")
async def reasoning_effort_set(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    cid = str(body.get("cid") or body.get("conversation_id") or "").strip()
    effort = str(body.get("effort") or "").strip().lower()
    if not cid:
        return {"ok": False, "error": "conversation_id required"}
    ok = core._set_reasoning_effort(cid, effort)
    return {"ok": ok, "reasoning_effort": core._get_reasoning_effort(cid)}


@router.get("/api/kb/files")
def kb_files() -> Dict[str, Any]:
    if not core.KB_BASE_DIR:
        return {"ok": True, "enabled": False, "files": []}
    if not core.KB_BASE_DIR.is_dir():
        try:
            core.KB_BASE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            return {"ok": True, "enabled": False, "files": []}
    return {"ok": True, "enabled": True, "files": core.list_kb_files_for_api()}


@router.get("/api/kb/checked")
def kb_checked_get(conversation_id: str = "") -> Dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    with core._KB_CHECKED_LOCK:
        state = core._KB_CHECKED_STATE.get(cid, set())
    return {"ok": True, "checked": sorted(state)}


@router.put("/api/kb/checked")
def kb_checked_put(body: KbCheckedIn) -> Dict[str, Any]:
    cid = str(body.conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    raw_paths = set(body.checked or [])
    accepted: Set[str] = set()
    for rel in raw_paths:
        fp = core._kb_safe_resolve_rel(rel)
        if fp and core._kb_file_allowed_when_checked(fp):
            try:
                norm = str(fp.relative_to(core.KB_BASE_DIR.resolve())).replace("\\", "/")
            except ValueError:
                continue
            accepted.add(norm)
    with core._KB_CHECKED_LOCK:
        if accepted:
            core._KB_CHECKED_STATE[cid] = accepted
        else:
            core._KB_CHECKED_STATE.pop(cid, None)
        core._kb_persist_checked()
    return {"ok": True, "checked": sorted(accepted)}


@router.post("/api/chat/stop")
def chat_stop(inp: ChatStopIn) -> Dict[str, Any]:
    cid = str(inp.conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    stopped = core._request_conversation_stop(cid, str(inp.run_id or "").strip())
    return {"ok": True, "conversation_id": cid, "stopped": stopped}


@router.get("/api/events/stream")
def global_events_stream() -> StreamingResponse:
    token = open_global_sse_channel()

    def gen():
        while is_global_sse_current(token) and not core.server_shutting_down():
            ev = next_global_sse_event(token, timeout=15.0)
            if ev is None:
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/api/chat/send")
def chat_send(inp: ChatIn, request: Request) -> Dict[str, Any]:
    text = str(inp.message or "").strip()
    raw_images = list(inp.images or [])
    if not text and not raw_images:
        raise HTTPException(400, "empty message")
    if not text and raw_images:
        text = "请查看图片"
    if len(raw_images) > 4:
        raise HTTPException(400, "images 最多 4 张")
    if core.AT_MESSAGE_FILE_PREFETCH:
        import re as _re

        _at_files = _re.findall(
            r'@((?:"[^"]+")|(?:[A-Za-z]:[\\/][^\s]+)|(?:~[^\s]+)|(?:/[^\s]+))',
            text,
        )
        _injected = []
        for _fp in _at_files:
            _fp = core._strip_config_path_value(_fp.strip('"').strip("'"))
            if not _fp:
                continue
            try:
                _p = Path(_fp).expanduser()
                try:
                    _p = _p.resolve()
                except OSError:
                    pass
                if _p.is_dir():
                    continue
                if _p.is_file():
                    with open(_p, "r", encoding="utf-8", errors="replace") as _f:
                        _fc = _f.read()
                    if len(_fc) > 500_000:
                        _fc = _fc[:500_000] + "\n...(文件过大，已截断)"
                    _injected.append(f"\n\n【引用的文件 @{_fp} 内容如下】\n{_fc}\n【文件结束】")
            except Exception as _e:
                _injected.append(f"\n\n【警告：无法读取 @{_fp}：{_e}】")
        if _injected:
            text = "".join(_injected) + "\n\n---\n用户消息：" + text
    cid = str(inp.conversation_id or "").strip() or core._new_conversation_id()
    mode = str(inp.mode or "").strip().lower()
    if mode not in {"", "auto", "plan", "execute"}:
        raise HTTPException(400, "invalid mode")
    mod = str(inp.model or "").strip()
    if mod:
        okm, _m = core.set_conversation_model(cid, mod)
        if not okm:
            raise HTTPException(400, "invalid model")
    rh.apply_conversation_request_options(cid, mode, mod)
    attachments: list = []
    if raw_images:
        try:
            from agent_v4.core.attachments import save_chat_images

            attachments = save_chat_images(
                cid,
                [im.model_dump() if hasattr(im, "model_dump") else dict(im) for im in raw_images],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(400, f"图片处理失败: {exc}") from exc
        if not attachments:
            raise HTTPException(400, "images 无法解码或为空")
    with get_conversation_run_lock(cid):
        if cid not in core.CONVERSATIONS or not core.CONVERSATIONS.get(cid):
            loaded = core._load_conversation(cid)
            if loaded:
                core.CONVERSATIONS[cid] = loaded
    client_ip = resolve_client_ip_from_request(request, core._normalize_client_ip_for_tools)
    run_id = core.start_background_agent_turn(
        cid, text, client_ip=client_ip, mode_hint=mode, attachments=attachments or None
    )
    if not run_id:
        raise HTTPException(409, "当前会话仍在执行中，请等待完成或先停止。")
    return {
        "ok": True,
        "conversation_id": cid,
        "run_id": run_id,
        "queued": False,
        "attachments": [{"id": a.get("id"), "name": a.get("name")} for a in attachments],
    }


@router.get("/api/chat/attachment")
def chat_attachment_get(conversation_id: str = "", id: str = ""):
    """本机会话截图缩略图（非公网）。"""
    from agent_v4.core.attachments import read_attachment_file

    path, err = read_attachment_file(conversation_id, id)
    if err or path is None:
        raise HTTPException(404, err or "not found")
    media = "image/jpeg"
    suf = path.suffix.lower()
    if suf == ".png":
        media = "image/png"
    elif suf == ".webp":
        media = "image/webp"
    return FileResponse(path, media_type=media)


@router.post("/api/chat/command-input")
def chat_command_input_submit(inp: ChatCommandInputIn) -> Dict[str, Any]:
    """向执行中的 run_command 子进程发送 stdin（用于 winget 等交互确认）。"""
    cid = inp.conversation_id.strip()
    tid = inp.tool_call_id.strip()
    text = inp.input.strip()
    if not cid or not tid:
        raise HTTPException(400, "conversation_id and tool_call_id required")
    if not text:
        raise HTTPException(400, "empty input")
    from agent_v4.live_state import submit_command_input

    key = f"{cid}:{tid}"
    if not submit_command_input(key, text):
        raise HTTPException(404, "no active run_command awaiting input for this tool call")
    return {"ok": True, "conversation_id": cid, "tool_call_id": tid}


@router.post("/api/chat/user-confirm")
def chat_user_confirm_submit(inp: ChatUserConfirmIn, request: Request) -> Dict[str, Any]:
    cid = inp.conversation_id.strip()
    conf = inp.confirm.strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    pending = core.PENDING_USER_CONFIRM.get(cid)
    if not pending:
        raise HTTPException(400, "no pending user confirmation for this conversation")
    tool_call_id = str(pending.get("tool_call_id") or "")
    exec_args0 = pending.get("exec_args")
    if not isinstance(exec_args0, dict):
        core.PENDING_USER_CONFIRM.pop(cid, None)
        raise HTTPException(500, "invalid pending user_confirm state")
    script_name = str(pending.get("script", "user_confirm"))
    if script_name in ("kling_generate", "skill_manage"):
        _default_confirm = "确认覆盖" if script_name == "skill_manage" else "确认生成"
        _first_opt = str(pending.get("confirms", [None])[0]) if isinstance(pending.get("confirms"), list) and len(pending.get("confirms")) > 0 else _default_confirm
        if conf == _first_opt:
            if script_name == "skill_manage":
                try:
                    from agent_v4.live_state import mark_confirmed as _kmc
                    _cid = str(exec_args0.get("confirm_id") or "")
                    if _cid:
                        _kmc(_cid)
                except Exception:
                    pass
            result = core.execute_tool_script(script_name, exec_args0, conversation_id=cid)
            exec_args1 = exec_args0
        else:
            try:
                from agent_v4.live_state import mark_confirmed as _kmc, consume_confirm_id as _kcc
                _cid = str(exec_args0.get("confirm_id") or "")
                if _cid:
                    _kmc(_cid)
                    _kcc(_cid)
            except Exception:
                pass
            _message = "用户已取消操作，请立即停止当前任务，不要再调用任何生成工具！"
            if script_name == "skill_manage":
                _message = "用户已取消覆盖技能文件，请不要继续执行覆盖。"
            result = {"ok": True, "data": {"confirm": str(conf) if conf else "取消", "cancelled": True, "message": _message}}
            exec_args1 = exec_args0
    else:
        exec_args1 = core._merge_confirm_into_user_confirm_args(exec_args0, conf)
        result = core.execute_tool_script(script_name, exec_args1, conversation_id=cid)
    if not (isinstance(result, dict) and result.get("ok") is True):
        core._record_tool_debug_failure(
            conversation_id=cid,
            api_name=script_name.replace('.py', ''),
            script=script_name,
            tool_call_id=tool_call_id,
            request=exec_args1,
            response=result,
            source="user_confirm_resume",
        )
    with get_conversation_run_lock(cid):
        messages = list(core.CONVERSATIONS.get(cid, []))
        idx: Optional[int] = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "tool" and str(messages[i].get("tool_call_id") or "") == tool_call_id:
                idx = i
                break
        if idx is None:
            core.PENDING_USER_CONFIRM.pop(cid, None)
            raise HTTPException(400, "tool message not found for pending confirmation")
        messages[idx] = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": core._truncate_tool_result(result),
        }
        core.PENDING_USER_CONFIRM.pop(cid, None)
        core.CONVERSATIONS[cid] = messages
        core._save_conversation(cid, messages)
    mode = str(inp.mode or "").strip().lower()
    if mode not in {"", "auto", "plan", "execute"}:
        raise HTTPException(400, "invalid mode")
    mod = str(inp.model or "").strip()
    if mod:
        okm, _m = core.set_conversation_model(cid, mod)
        if not okm:
            raise HTTPException(400, "invalid model")
    rh.apply_conversation_request_options(cid, mode, mod)
    client_ip = resolve_client_ip_from_request(request, core._normalize_client_ip_for_tools)
    run_id = core.start_background_agent_turn(
        cid,
        "",
        client_ip=client_ip,
        mode_hint=mode,
        resume_after_user_confirm=True,
    )
    return {"ok": True, "conversation_id": cid, "run_id": run_id}


@router.get("/api/dir-browse")
def dir_browse(path: str = "") -> Dict[str, Any]:
    import os as _os
    import string as _string

    _workspace = core._strip_config_path_value(str(core.AGENT_CONFIG.get("AGENT_WORKSPACE_DIR") or ""))
    if _workspace:
        _default = Path(_workspace).expanduser().resolve()
    else:
        _default = Path.home()
    if not _default.is_dir():
        _default = Path.home()

    if str(path).strip() == "_drives_":
        items = []
        for _letter in _string.ascii_uppercase:
            _dp = f"{_letter}:\\"
            if _os.path.exists(_dp):
                _is_ready = _os.path.isdir(_dp)
                _label = ""
                if _is_ready:
                    try:
                        import ctypes as _ct

                        _buf = _ct.create_unicode_buffer(261)
                        _ct.windll.kernel32.GetVolumeInformationW(
                            _dp, _buf, _ct.sizeof(_buf), None, None, None, None, 0
                        )
                        _label = _buf.value or ""
                    except Exception:
                        pass
                _name = f"{_letter}:\\"
                if _label:
                    _name += f" ({_label})"
                elif _is_ready:
                    _name += " (本地磁盘)"
                else:
                    _name += " (未就绪)"
                items.append(
                    {
                        "name": _name,
                        "type": "dir",
                        "ext": "",
                        "path": f"{_letter}:/" if _is_ready else _dp,
                    }
                )
        return {"current": "计算机", "parent": "", "items": items}

    if not path or not str(path).strip():
        target = _default
    else:
        raw_s = core._strip_config_path_value(str(path).strip())
        raw = Path(raw_s).expanduser()
        if raw.is_absolute():
            cand = raw.resolve()
        else:
            cand = (_default / raw).resolve()
        target = cand if cand.is_dir() else _default
    try:
        entries = _os.scandir(str(target))
    except OSError:
        entries = []
    items = []
    for e in sorted(entries, key=lambda x: (not x.is_dir(), x.name.lower())):
        ext = _os.path.splitext(e.name)[1].lower()
        items.append(
            {
                "name": e.name,
                "type": "dir" if e.is_dir() else "file",
                "ext": ext,
                "path": _os.path.abspath(e.path).replace("\\", "/"),
            }
        )
    parent_p = target.parent.resolve()
    _t_str = str(target).rstrip("\\").rstrip("/")
    if len(_t_str) == 2 and _t_str[1] == ":":
        parent_p_str = "_drives_"
    else:
        parent_p_str = str(parent_p).replace("\\", "/")
    return {
        "current": str(target).replace("\\", "/"),
        "parent": parent_p_str,
        "items": items,
    }


@router.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "version": AGENT_APP_VERSION,
        "catalog": str(core.TOOL_LIST_JSON),
        "model": core.default_model_from_env(),
        "allowed_models": list(core.ALLOWED_MODELS),
    }


# ── TTS 开关 API ──

@router.put("/api/tts/state")
def tts_state_set(body: Dict[str, Any]) -> Dict[str, bool]:
    """设置会话 TTS 开关。body: {"conversation_id": str, "enabled": bool}"""
    cid = str(body.get("conversation_id") or "").strip()
    if not cid:
        return {"ok": False}
    try:
        from util.tts.manager import set_tts_enabled
        set_tts_enabled(cid, bool(body.get("enabled", True)))
    except Exception as exc:
        import sys
        print(f"[TTS] set_tts_enabled 失败: {exc}", file=sys.stderr, flush=True)
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


# ── 团队 API ──

@router.get("/api/team/state")
def team_state() -> Dict[str, Any]:
    """返回团队注册表状态。已废弃，始终返回空。"""
    return {"ok": True, "members": {}}



def observer_events(conversation_id: str = "") -> Dict[str, Any]:
    """拉取并清空指定会话的观察者事件队列。"""
    return {"ok": True, "events": [], "conversation_id": str(conversation_id or "").strip()}
