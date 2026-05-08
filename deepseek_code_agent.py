#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek Code Agent: OpenAI-compatible Chat Completions + tool library (cli_*.py entrypoints).

Configure CHAT_API_BASE_URL + CHAT_API_KEY (DEEPSEEK_* still accepted as fallback).
Typical body: messages, tools, stream with SSE data: lines — see provider docs for JSON mode, tools, and errors.
- 本仓库工具库与 Agent 落盘文本（JSON 等）默认 UTF-8。
- 工具调用失败时（ok 非 true）可向 DATA_ROOT 下 debug 目录写入 JSON 记录；AGENT_TOOL_DEBUG=0 关闭。
"""
from __future__ import annotations

# ── 配置加载器（必须在其他 import 前执行，确保 env var 就绪）──
# 注：实际 os.environ 覆盖由 loader 内部完成
import asyncio
import copy
import difflib
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 加载配置（静默模式，避免重复输出）
try:
    from util.config_loader import load_config
    AGENT_CONFIG = load_config(verbose=False)
except ImportError:
    AGENT_CONFIG = {}
from typing import Any, Dict, List, Optional, Set, Tuple

from util.agent_prompt_constants import (
    AGENT_MAX_TOOL_ROUNDS_USER_HINT,
    TOOL_AGENT_EXECUTE_MODE_PROMPT,
    TOOL_AGENT_PLAN_MODE_PROMPT,
	TOOL_AGENT_AUTO_MODE_PROMPT,
    TOOL_AGENT_SYSTEM_PROMPT,
)
from util.agent_model_dispatch import ALLOWED_MODELS, default_model_from_env, effective_model, set_conversation_model
from util.agent_deepseek_pricing import get_model_pricing_snapshot
from util.agent_openai_compatible_client import chat_completion_request, chat_completion_stream
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
import uvicorn

# PyInstaller 打包后 __file__ 指向 sys._MEIPASS，源码模式正常
if getattr(sys, 'frozen', False):
    _base = Path(sys._MEIPASS)
else:
    _base = Path(__file__).resolve().parent
AGENT_ROOT = _base

# 可写运行时数据目录：默认 ~/AI_DATA_ROOT（用户目录下），可通过 config.json 修改
_dr = str(AGENT_CONFIG.get("AGENT_DATA_ROOT_DIR") or "").strip()
if _dr:
    DATA_ROOT = Path(_dr).resolve()
else:
    DATA_ROOT = Path.home() / "AI_DATA_ROOT"

# ── 知识库配置 ──
_KB_DIR_STR = str(AGENT_CONFIG.get("AGENT_KNOWLEDGE_BASE_DIR") or "").strip()
KB_BASE_DIR: Optional[Path] = Path(_KB_DIR_STR).resolve() if _KB_DIR_STR else None
_KB_CHECKED_STATE: Dict[str, Set[str]] = {}
_KB_CHECKED_LOCK = threading.Lock()

def _kb_persist_checked():
    if not KB_BASE_DIR:
        return
    try:
        state_file = KB_BASE_DIR / ".checked_state.json"
        ser = {k: sorted(v) for k, v in _KB_CHECKED_STATE.items()}
        state_file.write_text(json.dumps(ser, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _kb_load_single_cid_checked(cid: str):
    """从磁盘文件恢复指定会话的勾选状态"""
    if not KB_BASE_DIR:
        return
    state_file = KB_BASE_DIR / ".checked_state.json"
    if state_file.is_file():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if cid in data:
                _KB_CHECKED_STATE[cid] = set(data[cid])
        except Exception:
            pass


def _kb_load_checked():
    if not KB_BASE_DIR:
        return
    state_file = KB_BASE_DIR / ".checked_state.json"
    if state_file.is_file():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            for cid, paths in data.items():
                _KB_CHECKED_STATE[cid] = set(paths)
        except Exception:
            pass

# 启动时恢复勾选状态
_kb_load_checked()

TOOLS_DIR = AGENT_ROOT / "tools"
TOOL_LIST_JSON = TOOLS_DIR / "tool_list_cli.json"
USAGE_ACCUM_FILE = DATA_ROOT / "model_usage_accumulator.json"
SESSION_DIR = DATA_ROOT / "cache" / "sessions"
EXCERPTS_DIR = DATA_ROOT / "cache" / "excerpts"
TOOL_DEBUG_DIR = DATA_ROOT / "debug"
LAST_OPEN_SESSION_STATE_FILE = DATA_ROOT / "cache" / "last_open_session_state.json"
_skf = str(AGENT_CONFIG.get("AGENT_SESSION_KEY_FILE") or "").strip()
SESSION_KEY_FILE = Path(_skf).expanduser().resolve() if _skf else (DATA_ROOT / "cache" / "session_encryption.key")
SESSION_ENCRYPTION_MAGIC = "__code_web_agent_session_encrypted__"
SESSION_APP_ENTROPY = hashlib.sha256((str(AGENT_ROOT) + "|code-web-agent-session-v1").encode("utf-8")).digest()


CONTEXT_HISTORY_MAX_MESSAGES = 1000 # 历史最多放1000次对话内容(含工具记录)
CONTEXT_EXCERPT_START_INDEX = 99 # 截取做摘要的起始位置
CONTEXT_RECENT_USER_ROUNDS = 100 # 每次压缩上下文,依旧保留最近100次对话(含工具记录)
SUMMARY_IN_PROGRESS_TTL_SEC = 300.0
MAX_TOOL_ROUNDS = 100 # 每轮对话允许调用工具次数的上限
UI_RESTORE_MAX_TABS = 8 # 页面重开时最多恢复最近几个会话标签
UI_RESTORE_MAX_CHAT_ITEMS = 40 # 每个会话只恢复最近若干条聊天气泡，不恢复步骤区
PREVIEW_INTENT_KEYS = ("预览", "原文", "全文", "完整内容", "原始内容", "显示文件")

# @路径：是否在进模型前由服务端预读并注入全文。False=由模型按需用工具读取；True=恢复预注入。
AT_MESSAGE_FILE_PREFETCH = False

# ---------- 控制台调试：stderr 输出 JSON 行（SSE 事件、工具完整入参/出参）。AGENT_CONSOLE_LOG=0 关闭 ----------
def _agent_console_log_enabled() -> bool:
    v = os.environ.get("AGENT_CONSOLE_LOG", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _sse_event_console_repr(ev: Dict[str, Any]) -> Dict[str, Any]:
    """避免 assistant_delta / reasoning_* 长文本刷屏；其余事件原样记录。"""
    t = ev.get("type")
    if t == "assistant_delta":
        d = ev.get("delta")
        if not isinstance(d, str) or len(d) <= 800:
            return ev
        return {**ev, "delta": d[:800] + f"... ({len(d)} chars total)"}
    if t == "reasoning_delta":
        d = ev.get("delta")
        if not isinstance(d, str) or len(d) <= 800:
            return ev
        return {**ev, "delta": d[:800] + f"... ({len(d)} chars total)"}
    if t == "reasoning_sync":
        txt = ev.get("text")
        if not isinstance(txt, str) or len(txt) <= 800:
            return ev
        return {**ev, "text": txt[:800] + f"... ({len(txt)} chars total)"}
    return ev


def _log_agent_console_sse(conversation_id: str, ev: Dict[str, Any]) -> None:
    if not _agent_console_log_enabled():
        return
    row = {"channel": "sse", "conversation_id": conversation_id, "sse": _sse_event_console_repr(ev)}
    print(json.dumps(row, ensure_ascii=False), file=sys.stderr, flush=True)


def _log_agent_console_tool(
    conversation_id: str,
    api_name: Any,
    script: Any,
    args: Any,
    result: dict,
) -> None:
    if not _agent_console_log_enabled():
        return
    row = {
        "channel": "tool",
        "conversation_id": conversation_id,
        "api_name": api_name,
        "script": script,
        "args": args,
        "result": result,
    }
    print(json.dumps(row, ensure_ascii=False), file=sys.stderr, flush=True)


def _tool_debug_file_enabled() -> bool:
    v = os.environ.get("AGENT_TOOL_DEBUG", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _safe_debug_filename_segment(name: str, max_len: int = 48) -> str:
    s = str(name or "tool")[:max_len]
    out = []
    for c in s:
        if c.isalnum() or c in "._-":
            out.append(c)
        else:
            out.append("_")
    return "".join(out) or "tool"


def _record_tool_debug_failure(
    *,
    conversation_id: str,
    api_name: Any,
    script: Any,
    tool_call_id: Any,
    request: Any,
    response: Any,
    source: str,
) -> None:
    """工具返回 ok≠true 时，将请求与响应写入 DATA_ROOT/debug 下的 JSON，供离线分析。"""
    if not _tool_debug_file_enabled():
        return
    try:
        TOOL_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        uid = uuid.uuid4().hex[:10]
        seg = _safe_debug_filename_segment(str(script))
        cid_part = _safe_debug_filename_segment(str(conversation_id)[:16], 24)
        fn = f"{ts}_{seg}_{cid_part}_{uid}.json"
        out_path = TOOL_DEBUG_DIR / fn
    except Exception as exc:
        print(f"WARN: tool debug path prepare failed: {exc}", file=sys.stderr, flush=True)
        return
    payload: Dict[str, Any] = {
        "recordedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": str(source or ""),
        "conversationId": str(conversation_id or ""),
        "apiName": api_name,
        "script": script,
        "toolCallId": str(tool_call_id or ""),
        "request": request,
        "response": response,
    }
    try:
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"WARN: tool debug write failed {out_path}: {exc}", file=sys.stderr, flush=True)


# ---------- in-memory multi-round conversations (stateless API per DeepSeek docs) ----------
CONVERSATIONS: Dict[str, List[Dict[str, Any]]] = {}
PENDING_USER_CONFIRM: Dict[str, Dict[str, Any]] = {}
TODO_LISTS: Dict[str, Dict[str, Any]] = {}
_TODO_DATA_DIR = DATA_ROOT / "cache" / "todo_lists"

CONVERSATION_MODES: Dict[str, str] = {}
SUMMARY_IN_PROGRESS: Dict[str, float] = {}
PENDING_EXCERPT_PATHS: Dict[str, List[str]] = {}
_SUMMARY_STATE_LOCK = threading.Lock()
_CONVERSATION_RUN_LOCKS: Dict[str, threading.RLock] = {}
_CONVERSATION_RUN_LOCKS_LOCK = threading.Lock()
_TOOL_EXEC_LOCK = threading.RLock()
_CONVERSATION_STOP_FLAGS: Dict[str, Set[str]] = {}
_ACTIVE_CONVERSATION_RUNS: Dict[str, str] = {}
_CONVERSATION_STOP_LOCK = threading.Lock()


def _begin_conversation_run(cid: str) -> Optional[str]:
    key = str(cid or "")
    with _CONVERSATION_STOP_LOCK:
        if key in _ACTIVE_CONVERSATION_RUNS:
            return None
        run_id = str(uuid.uuid4())
        _ACTIVE_CONVERSATION_RUNS[key] = run_id
        _CONVERSATION_STOP_FLAGS.pop(key, None)
        return run_id


def _end_conversation_run(cid: str, run_id: str = "") -> None:
    key = str(cid or "")
    with _CONVERSATION_STOP_LOCK:
        active = _ACTIVE_CONVERSATION_RUNS.get(key)
        if not run_id or active == run_id:
            _ACTIVE_CONVERSATION_RUNS.pop(key, None)
            _CONVERSATION_STOP_FLAGS.pop(key, None)


def _request_conversation_stop(cid: str, run_id: str = "") -> bool:
    key = str(cid or "")
    if not key:
        return False
    with _CONVERSATION_STOP_LOCK:
        active = _ACTIVE_CONVERSATION_RUNS.get(key)
        if not active:
            _CONVERSATION_STOP_FLAGS.pop(key, None)
            return False
        rid = str(run_id or active)
        if rid != active:
            return False
        _CONVERSATION_STOP_FLAGS.setdefault(key, set()).add(rid)
        return True


def _consume_conversation_stop_requested(cid: str, run_id: str = "") -> bool:
    key = str(cid or "")
    rid = str(run_id or "")
    with _CONVERSATION_STOP_LOCK:
        flags = _CONVERSATION_STOP_FLAGS.get(key)
        if not flags or rid not in flags:
            return False
        flags.discard(rid)
        if not flags:
            _CONVERSATION_STOP_FLAGS.pop(key, None)
        return True


def _finish_conversation_stopped(cid: str, rollback_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    messages = copy.deepcopy(rollback_messages)
    _tail_drop_incomplete_tool_assistant(messages)
    _normalize_persisted_conversation(messages)
    CONVERSATIONS[cid] = messages
    _save_conversation(cid, messages)
    return {"type": "stopped", "message": "任务已停止"}


def _get_conversation_run_lock(cid: str) -> threading.RLock:
    key = str(cid or "")
    with _CONVERSATION_RUN_LOCKS_LOCK:
        lock = _CONVERSATION_RUN_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _CONVERSATION_RUN_LOCKS[key] = lock
        return lock


def _ensure_conversation_loaded(cid: str) -> None:
    if cid and (cid not in CONVERSATIONS or not CONVERSATIONS.get(cid)):
        loaded = _load_conversation(cid)
        if loaded:
            CONVERSATIONS[cid] = loaded


_HELP_CAPTURE_MAX = 24000


def _subprocess_cli_help(script_name: str) -> str:
    # Best-effort: import tool and get --help text in-process (no subprocess)
    if not script_name or not str(script_name).endswith(".py"):
        return ""
    script_path = TOOLS_DIR / script_name
    if not script_path.is_file():
        return ""
    try:
        import importlib as _il, io as _io
        mod_name = script_name[:-3]
        mod = _il.import_module(mod_name)
        with _TOOL_EXEC_LOCK:
            old_argv = sys.argv
            old_stdout = sys.stdout
            captured = _io.StringIO()
            try:
                sys.argv = [str(script_path), "--help"]
                sys.stdout = captured
                mod.main()
            except SystemExit:
                pass
            except Exception:
                pass
            finally:
                sys.stdout = old_stdout
                sys.argv = old_argv
        txt = captured.getvalue().strip()
        if not txt:
            return ""
        if len(txt) > _HELP_CAPTURE_MAX:
            txt = txt[:_HELP_CAPTURE_MAX] + "\n...(help truncated)"
        return txt
    except Exception:
        return ""


def _enrich_tool_error_message(script_name: str, message: str) -> str:
    if not message:
        message = ""
    low = message.lower()
    if "\n--help:\n" in message or "usage:" in low or "optional arguments:" in low or "options:" in low:
        return message
    h = _subprocess_cli_help(script_name)
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


def _unknown_tool_result(api_name: Any, script_by_api: Dict[str, str]) -> dict:
    key = str(api_name or "").strip()
    names = sorted(script_by_api.keys())
    lines = [f"unknown tool {key!r}", "", f"已注册的 function 名称（共 {len(names)} 个，字典序节选）："]
    cap = 80
    if len(names) <= cap:
        lines.append(", ".join(names))
    else:
        lines.append(", ".join(names[:cap]) + f" … 另有 {len(names) - cap} 个未列出，请查看 tool_list_cli.json")
    if key:
        close = difflib.get_close_matches(key, names, n=5, cutoff=0.34)
        if not close and key.endswith("s") and len(key) > 1:
            close = difflib.get_close_matches(key[:-1], names, n=5, cutoff=0.45)
        for c in close[:1]:
            scr = script_by_api.get(c) or ""
            h = _subprocess_cli_help(scr) if scr else ""
            if h:
                lines.extend(["", f"与 {key!r} 最接近的已注册名称：{c!r}（脚本 {scr}），该脚本 --help：", h])
                break
            if scr:
                lines.extend(["", f"与 {key!r} 最接近的已注册名称：{c!r}（脚本 {scr}）；未能捕获 --help 文本"])
                break
    msg = "\n".join(lines)
    return {"ok": False, "data": None, "error": {"type": "UnknownTool", "message": msg}}


def _execute_cli_run_type(conversation_id: str, exec_args: Dict[str, Any]) -> dict:
    rt = str(exec_args.get("runType") or exec_args.get("run_type") or "").strip().lower()
    cid = str(conversation_id or "")
    # 不传 runType 则为查询模式
    if not rt or rt not in {"auto", "plan", "execute"}:
        mode = CONVERSATION_MODES.get(cid, "auto")
        return {"ok": True, "data": {"runType": mode, "action": "query"}}
    # 切换模式
    if rt == "auto":
        CONVERSATION_MODES.pop(cid, None)
    else:
        CONVERSATION_MODES[cid] = rt
    return {"ok": True, "data": {"runType": rt, "action": "switch"}}



def _todo_persist(cid: str) -> None:
    """将 cid 的 Todo-List 写入磁盘"""
    if not cid:
        return
    try:
        _TODO_DATA_DIR.mkdir(parents=True, exist_ok=True)
        lst = TODO_LISTS.get(cid)
        fp = _TODO_DATA_DIR / f"{cid}.json"
        if lst is None:
            if fp.exists():
                fp.unlink()
            return
        fp.write_text(json.dumps(lst, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _todo_load(cid: str) -> None:
    """从磁盘加载 cid 的 Todo-List 到内存"""
    if not cid or cid in TODO_LISTS:
        return
    try:
        fp = _TODO_DATA_DIR / f"{cid}.json"
        if fp.exists():
            raw = fp.read_text(encoding="utf-8")
            lst = json.loads(raw)
            if isinstance(lst, dict) and "listId" in lst and "items" in lst:
                TODO_LISTS[cid] = lst
    except Exception:
        pass


def _execute_cli_todo_list(conversation_id: str, exec_args: Dict[str, Any]) -> dict:
    """处理 cli_todo_list 调用，使用 TODO_LISTS 内存状态。"""
    action = str(exec_args.get("action") or "").strip().lower()
    cid = str(conversation_id or "")
    if action == "create":
        items_raw = str(exec_args.get("items") or "")
        if not items_raw.strip():
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "create 需要 items"}}
        try:
            parsed = json.loads(items_raw)
        except json.JSONDecodeError as e:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": f"items 不是合法 JSON: {e}"}}
        if not isinstance(parsed, list) or not parsed:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "items 必须是非空数组"}}
        items = []
        for i, it in enumerate(parsed):
            if not isinstance(it, str) or not it.strip():
                return {"ok": False, "data": None, "error": {"type": "ValueError", "message": f"items[{i}] 不是有效字符串"}}
            items.append({"text": str(it).strip(), "done": False})
        lid = uuid.uuid4().hex[:12]
        TODO_LISTS[cid] = {"listId": lid, "items": items, "collapsed": False}
        _todo_persist(cid)
        return {"ok": True, "data": {"listId": lid, "items": items, "collapsed": False}, "error": None}
    elif action == "check":
        lst = TODO_LISTS.get(cid)
        if lst is None:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "当前对话无活跃清单，请先用 create"}}
        idx = int(exec_args.get("itemIndex", -1))
        if idx < 0 or idx >= len(lst["items"]):
            return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"itemIndex {idx} 越界，共 {len(lst['items'])} 项"}}
        lst["items"][idx]["done"] = True
        _todo_persist(cid)
        return {"ok": True, "data": {"listId": lst["listId"], "items": lst["items"]}, "error": None}
    elif action == "uncheck":
        lst = TODO_LISTS.get(cid)
        if lst is None:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "当前对话无活跃清单，请先用 create"}}
        idx = int(exec_args.get("itemIndex", -1))
        if idx < 0 or idx >= len(lst["items"]):
            return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"itemIndex {idx} 越界，共 {len(lst['items'])} 项"}}
        lst["items"][idx]["done"] = False
        _todo_persist(cid)
        return {"ok": True, "data": {"listId": lst["listId"], "items": lst["items"]}, "error": None}
    elif action == "add_item":
        lst = TODO_LISTS.get(cid)
        if lst is None:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "当前对话无活跃清单，请先用 create"}}
        text = str(exec_args.get("text") or "").strip()
        if not text:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "add_item 需要 text"}}
        idx = exec_args.get("itemIndex")
        item = {"text": text, "done": False}
        if idx is not None and isinstance(idx, int) and 0 <= idx <= len(lst["items"]):
            lst["items"].insert(idx, item)
        else:
            lst["items"].append(item)
        _todo_persist(cid)
        return {"ok": True, "data": {"listId": lst["listId"], "items": lst["items"]}, "error": None}
    elif action == "remove_item":
        lst = TODO_LISTS.get(cid)
        if lst is None:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "当前对话无活跃清单，请先用 create"}}
        idx = int(exec_args.get("itemIndex", -1))
        if idx < 0 or idx >= len(lst["items"]):
            return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"itemIndex {idx} 越界，共 {len(lst['items'])} 项"}}
        lst["items"].pop(idx)
        _todo_persist(cid)
        return {"ok": True, "data": {"listId": lst["listId"], "items": lst["items"]}, "error": None}
    elif action == "replace_item":
        lst = TODO_LISTS.get(cid)
        if lst is None:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "当前对话无活跃清单，请先用 create"}}
        idx = int(exec_args.get("itemIndex", -1))
        text = str(exec_args.get("text") or "").strip()
        if idx < 0 or idx >= len(lst["items"]):
            return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"itemIndex {idx} 越界，共 {len(lst['items'])} 项"}}
        if not text:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "replace_item 需要 text"}}
        lst["items"][idx]["text"] = text
        _todo_persist(cid)
        return {"ok": True, "data": {"listId": lst["listId"], "items": lst["items"]}, "error": None}
    elif action == "collapse":
        lst = TODO_LISTS.get(cid)
        if lst is None:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "当前对话无活跃清单，请先用 create"}}
        # 切换折叠状态
        lst["collapsed"] = not lst.get("collapsed", False)
        return {"ok": True, "data": {"listId": lst["listId"], "items": lst["items"], "collapsed": lst["collapsed"]}, "error": None}
    elif action == "close":
        lst = TODO_LISTS.get(cid)
        if lst is None:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "当前对话无活跃清单，请先用 create"}}
        TODO_LISTS.pop(cid, None)
        _todo_persist(cid)
        return {"ok": True, "data": {"close": True}, "error": None}
    elif action == "query":
        _todo_load(cid)
        lst = TODO_LISTS.get(cid)
        if lst is None:
            return {"ok": True, "data": None, "error": None}
        return {"ok": True, "data": list(lst["items"]), "error": None}
    else:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": f"未知 action: {action}"}}


def _safe_json_loads(s: str) -> Optional[dict]:
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except Exception:
        return None


def _reasoning_delta_field_names() -> List[str]:
    raw = (os.environ.get("CHAT_API_REASONING_DELTA_FIELDS") or "reasoning_content,reasoning").strip()
    names = [x.strip() for x in raw.split(",") if x.strip()]
    return names if names else ["reasoning_content", "reasoning"]


def _best_message_reasoning_field(last_message: Dict[str, Any]) -> str:
    best = ""
    for name in _reasoning_delta_field_names():
        v = last_message.get(name)
        if isinstance(v, str) and v and len(v) > len(best):
            best = v
    return best


def _dpapi_crypt(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    in_buf = ctypes.create_string_buffer(data)
    in_blob = DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    entropy_buf = ctypes.create_string_buffer(SESSION_APP_ENTROPY)
    entropy_blob = DATA_BLOB(len(SESSION_APP_ENTROPY), ctypes.cast(entropy_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(in_blob), None, ctypes.byref(entropy_blob), None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _session_key_material() -> bytes:
    SESSION_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SESSION_KEY_FILE.is_file():
        val = SESSION_KEY_FILE.read_text(encoding="ascii").strip()
        return base64.urlsafe_b64decode(val.encode("ascii"))
    key = os.urandom(32)
    SESSION_KEY_FILE.write_text(base64.urlsafe_b64encode(key).decode("ascii"), encoding="ascii")
    try:
        os.chmod(str(SESSION_KEY_FILE), 0o600)
    except Exception:
        pass
    return key


def _session_fallback_key() -> bytes:
    # Cross-platform default for macOS/Linux, and Windows when DPAPI is disabled/unavailable.
    # The app entropy means the raw local key file alone is not the complete decrypt key.
    return hmac.new(SESSION_APP_ENTROPY, _session_key_material(), hashlib.sha256).digest()


def _legacy_session_fallback_keys() -> List[bytes]:
    material = _session_key_material()
    return [material, hashlib.sha256(material).digest()]


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(data, out[:len(data)]))


def _encrypt_session_payload(plain: bytes) -> Dict[str, Any]:
    mode = str(AGENT_CONFIG.get("AGENT_SESSION_ENCRYPTION") or "auto").strip().lower() or "auto"
    if mode not in {"auto", "dpapi", "local", "none"}:
        mode = "auto"
    if mode == "none":
        return None  # 通知调用方直接存明文
    if os.name == "nt" and mode in {"auto", "dpapi"}:
        try:
            data = _dpapi_crypt(plain, True)
            return {
                SESSION_ENCRYPTION_MAGIC: 1,
                "alg": "dpapi-user-v1",
                "data": base64.b64encode(data).decode("ascii"),
            }
        except Exception as exc:
            if mode == "dpapi":
                raise
            print(f"WARN: DPAPI session encryption failed, using local key fallback: {exc}", file=sys.stderr, flush=True)
    key = _session_fallback_key()
    nonce = os.urandom(16)
    cipher = _xor_stream(plain, key, nonce)
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return {
        SESSION_ENCRYPTION_MAGIC: 1,
        "alg": "local-hmac-sha256-stream-v1",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "data": base64.b64encode(cipher).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
    }


def _decrypt_session_payload(raw: Any) -> Optional[bytes]:
    if not isinstance(raw, dict) or raw.get(SESSION_ENCRYPTION_MAGIC) != 1:
        return None
    alg = str(raw.get("alg") or "")
    if alg == "dpapi-user-v1":
        data = base64.b64decode(str(raw.get("data") or "").encode("ascii"))
        return _dpapi_crypt(data, False)
    if alg in {"local-hmac-sha256-stream-v1", "hmac-sha256-stream-v1"}:
        key = _session_fallback_key()
        nonce = base64.b64decode(str(raw.get("nonce") or "").encode("ascii"))
        cipher = base64.b64decode(str(raw.get("data") or "").encode("ascii"))
        tag = base64.b64decode(str(raw.get("tag") or "").encode("ascii"))
        expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        if hmac.compare_digest(tag, expected):
            return _xor_stream(cipher, key, nonce)
        for legacy_key in _legacy_session_fallback_keys():
            expected = hmac.new(legacy_key, nonce + cipher, hashlib.sha256).digest()
            if hmac.compare_digest(tag, expected):
                return _xor_stream(cipher, legacy_key, nonce)
        raise ValueError("session encryption tag mismatch")
    raise ValueError(f"unsupported session encryption alg: {alg}")


def _session_date_group_from_path(fp: Path) -> str:
    parent = fp.parent.name
    return parent if re.match(r"^\d{4}-\d{2}-\d{2}$", parent) else ""


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


def _conversation_file_for_save(cid: str) -> Path:
    existing = _find_conversation_file(cid)
    if existing is not None:
        return existing
    day = time.strftime("%Y-%m-%d", time.localtime())
    return SESSION_DIR / day / f"{cid}.json"


def _save_conversation(cid: str, messages: List[Dict[str, Any]], title: str = "") -> None:
    if not cid:
        return
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        fp = _conversation_file_for_save(cid)
        fp.parent.mkdir(parents=True, exist_ok=True)
        if not title:
            title = _fallback_title_from_messages(cid, messages)
        # 明文存储：第一行标题，后续messages
        plain = f"__title__:{title[:80]}\n" + json.dumps(messages, ensure_ascii=False)
        envelope = _encrypt_session_payload(plain.encode("utf-8"))
        if envelope is None:
            fp.write_text(plain, encoding="utf-8")
        else:
            fp.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"WARN: failed to save conversation {cid}: {e}", file=sys.stderr, flush=True)


def _load_conversation(cid: str) -> Optional[List[Dict[str, Any]]]:
    if not cid:
        return None
    try:
        fp = _find_conversation_file(cid)
        if fp is None or not fp.is_file():
            return None
        raw_text = fp.read_text(encoding="utf-8")
        if raw_text.startswith("__title__:"):
            idx = raw_text.find("\n")
            if idx != -1:
                raw_text = raw_text[idx+1:]
        raw = json.loads(raw_text)
        decrypted = _decrypt_session_payload(raw)
        if decrypted is not None:
            raw_text = decrypted.decode("utf-8")
            raw = json.loads(raw_text)
        if isinstance(raw, list) and all(isinstance(m, dict) for m in raw):
            return raw
        return None
    except Exception:
        return None


def _chat_history_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content:
            continue
        out.append({"role": role, "content": content})
    return out[-UI_RESTORE_MAX_CHAT_ITEMS:]


def _load_conversation_title(cid: str) -> str:
    """从 session 文件第一行读取标题，没有则返回空"""
    try:
        fp = _find_conversation_file(cid)
        if fp is None or not fp.is_file():
            return ""
        first_line = fp.read_text(encoding="utf-8").split("\n", 1)[0]
        if first_line.startswith("__title__:"):
            return first_line[len("__title__:"):].strip()[:80]
    except Exception:
        pass
    return ""


def _fallback_title_from_messages(cid: str, messages: List[Dict[str, Any]]) -> str:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            c = str(m.get("content") or "").strip()
            if c:
                return _clean_conversation_title(c)
    return f"会话 {cid[:8]}"


def _clean_conversation_title(text: str) -> str:
    title = str(text or "").strip()
    title = re.sub(r"^[「『\"'`《【\[]+|[」』\"'`》】\]]+$", "", title).strip()
    title = re.sub(r"^(标题|会话标题)\s*[:：]\s*", "", title).strip()
    title = re.sub(r"\s+", "", title)
    if not title:
        return "新会话"
    return title[:18]


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


def _save_last_open_session_state(state: Dict[str, Any]) -> None:
    try:
        LAST_OPEN_SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_OPEN_SESSION_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"WARN: failed to save last open session state: {e}", file=sys.stderr, flush=True)


def _load_last_open_session_state() -> Dict[str, Any]:
    try:
        if not LAST_OPEN_SESSION_STATE_FILE.is_file():
            return {}
        state = json.loads(LAST_OPEN_SESSION_STATE_FILE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def load_catalog() -> dict:
    if not TOOL_LIST_JSON.exists():
        raise RuntimeError(f"missing {TOOL_LIST_JSON}")
    raw = TOOL_LIST_JSON.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"invalid JSON in {TOOL_LIST_JSON}: {e}"
        doc = getattr(e, "doc", None)
        pos = getattr(e, "pos", None)
        if isinstance(doc, str) and isinstance(pos, int):
            lo = max(0, pos - 60)
            hi = min(len(doc), pos + 60)
            snippet = doc[lo:hi].replace(chr(10), "\\n")
            msg += f"; context near {pos}: ...{repr(snippet)}..."
        raise RuntimeError(msg) from e


def api_function_name(script_name: str) -> str:
    return script_name[:-3] if script_name.endswith(".py") else script_name


def catalog_to_openai_tools(catalog: dict) -> Tuple[List[dict], Dict[str, str]]:
    """Return OpenAI-format tools + mapping api_name -> script filename cli_*.py."""
    tools: List[dict] = []
    name_map: Dict[str, str] = {}
    for t in catalog.get("tools", []):
        fn = t["name"]
        if not fn.endswith(".py"):
            continue
        api = api_function_name(fn)
        name_map[api] = fn
        props: Dict[str, Any] = {}
        required: List[str] = []
        for arg in t.get("args", []):
            flag = str(arg.get("flag", ""))
            pname = flag[2:] if flag.startswith("--") else flag
            typ = arg.get("type", "string")
            desc = str(arg.get("description", flag))
            if typ == "integer":
                sch: Dict[str, Any] = {"type": "integer", "description": desc}
            elif typ == "boolean":
                sch = {"type": "boolean", "description": desc}
            elif typ == "enum":
                sch = {"type": "string", "description": desc, "enum": list(arg.get("values", []))}
            else:
                sch = {"type": "string", "description": desc}
            props[pname] = sch
            if arg.get("required"):
                required.append(pname)
        if "usePreview" not in props and api != "cli_preview_render":
            _preview_desc = "仅 function/schema 参数，多数 cli 子进程无对应 argv；含义见 tool_list_cli.json 的 agent_hints.function_call_extras。写盘/改动类默认尝试联动预览；extract 等只读默认不预览。"
            if api in ("cli_command_exec", "cli_python_inline"):
                _preview_desc = "cli_command_exec、cli_python_inline 由服务端强制联动预览（不可关闭）；含义仍见 agent_hints.function_call_extras。"
            props["usePreview"] = {
                "type": "boolean",
                "description": _preview_desc,
            }
        if "step_title" not in props and api != "cli_preview_render":
            props["step_title"] = {
                "type": "string",
                "description": "可选。一句简短中文说明本次调用用途，将用作侧栏步骤主标题（建议≤40字）；省略则使用系统默认动作标题。",
            }
        tools.append({
            "type": "function",
            "function": {
                "name": api,
                "description": str(t.get("purpose", fn)),
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return tools, name_map




def _openai_tools_sort_key(t: dict) -> Tuple[int, str]:
    """OpenAI tools list order: cli_command_exec last (lower implicit weight)."""
    name = str((t.get("function") or {}).get("name") or "")
    deprioritize = 1 if name == "cli_command_exec" else 0
    return (deprioritize, name)


def normalize_cli_args(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        key = str(k)
        if not key.startswith("--"):
            key = "--" + key
        out[key] = v
    return out


def _normalize_replace_literal_payload_keys(obj: Dict[str, Any]) -> None:
    if obj.get("type") != "replace_literal":
        return
    if "oldText" not in obj and isinstance(obj.get("oldString"), str):
        obj["oldText"] = obj.pop("oldString")
    if "newText" not in obj and isinstance(obj.get("newString"), str):
        obj["newText"] = obj.pop("newString")


def _repair_payload_nested_aliases(fixed: Dict[str, Any]) -> None:
    p = fixed.get("payload")
    if isinstance(p, str):
        try:
            o = json.loads(p)
        except Exception:
            return
        if isinstance(o, dict):
            _normalize_replace_literal_payload_keys(o)
            fixed["payload"] = json.dumps(o, ensure_ascii=False)
    elif isinstance(p, dict):
        _normalize_replace_literal_payload_keys(p)


def _repair_structured_edit_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort repair for malformed model args targeting cli_structured_edit.
    Keeps compatibility when model mistakenly puts extract instruction in text/top-level.
    """
    fixed = dict(args or {})
    _repair_payload_nested_aliases(fixed)
    if fixed.get("payload") is not None:
        return fixed

    # Case A: top-level extract fields were mistakenly provided
    mode = fixed.get("mode")
    if isinstance(mode, str) and mode in {"lines", "lines_columns", "offsets"}:
        payload = {"type": "extract", "mode": mode}
        for k in ("startLine", "endLine", "startColumn", "endColumn", "start", "end", "outFile", "outEncoding", "usePreview"):
            if k in fixed:
                payload[k] = fixed.pop(k)
        fixed["payload"] = payload
        return fixed

    # Case B: text contains pseudo instruction, e.g. mode=extract|offsets-0,-1
    txt = fixed.get("text")
    if isinstance(txt, str):
        m = re.search(r"extract\s*\|\s*offsets\s*[-:]\s*(-?\d+)\s*,\s*(-?\d+)", txt, flags=re.IGNORECASE)
        if m:
            fixed.pop("text", None)
            fixed["payload"] = {
                "type": "extract",
                "mode": "offsets",
                "start": int(m.group(1)),
                "end": int(m.group(2)),
            }
            return fixed
        if re.search(r"extract", txt, flags=re.IGNORECASE):
            fixed.pop("text", None)
            fixed["payload"] = {"type": "extract", "mode": "offsets", "start": 0, "end": -1}
            return fixed

    return fixed


_STRUCTURED_EDIT_WRITE_TYPES = frozenset(
    {
        "append",
        "append_line",
        "insert",
        "replace_range",
        "replace_literal",
        "replace_markers",
        "delete_segments",
    }
)


def _structured_edit_payload_type_from_args(ad: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(ad, dict):
        return None
    pl = ad.get("payload")
    if pl is None:
        pl = ad.get("--payload")
    if isinstance(pl, str):
        try:
            obj = json.loads(pl)
        except Exception:
            return None
        pl = obj
    if isinstance(pl, dict):
        t = pl.get("type")
        return t if isinstance(t, str) else None
    return None


def _default_use_preview_link(script: str, args: Optional[Dict[str, Any]]) -> bool:
    """写盘/改动类默认尝试联动预览；只读默认关闭（未传 usePreview 时）。"""
    if not script or script == "cli_preview_render.py":
        return False
    sl = script.lower()
    if sl == "cli_structured_edit.py":
        t = _structured_edit_payload_type_from_args(args)
        if t is None:
            return False
        return t in _STRUCTURED_EDIT_WRITE_TYPES
    if sl == "cli_file_ops.py":
        if not isinstance(args, dict):
            return False
        act = args.get("action") or args.get("--action")
        return isinstance(act, str) and bool(act.strip())
    if sl == "cli_patch_apply.py":
        return True
    return False


_AGENT_MAIN_ARG_ALIASES: Dict[str, str] = {
    "glob": "glob_pattern",
    "type": "type_filter",
}


def _camel_to_snake(name: str) -> str:
    name = str(name or "").strip().lstrip("-").replace("-", "_")
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _agent_main_param_name(raw_key: str) -> str:
    key = str(raw_key or "").strip().lstrip("-")
    snake = _camel_to_snake(key)
    return _AGENT_MAIN_ARG_ALIASES.get(snake, snake)


def _strip_internal_tool_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if not str(k).startswith("_")}


def _try_execute_tool_agent_main(script_name: str, mod: Any, args: Dict[str, Any]) -> Optional[dict]:
    """优先直接调用工具的 agent_main；参数无法映射时返回 None 交给 CLI main 回退。"""
    import inspect

    fn = getattr(mod, "agent_main", None)
    if not callable(fn):
        return None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None

    params = sig.parameters
    kwargs: Dict[str, Any] = {}
    for k, v in args.items():
        if v is None:
            continue
        pn = _agent_main_param_name(k)
        if pn == "json_out":
            continue
        if pn not in params:
            # 例如 cli_command_exec/cli_python_inline 的 outFile 属于 CLI 外壳能力，回退 main() 保持兼容。
            return None
        kwargs[pn] = v

    if "parser_for_help" in params and "parser_for_help" not in kwargs:
        build_parser = getattr(mod, "build_parser", None)
        if callable(build_parser):
            try:
                kwargs["parser_for_help"] = build_parser()
            except Exception:
                pass

    try:
        result = fn(**kwargs)
    except TypeError:
        return None
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": "ToolError", "message": f"工具 {script_name} agent_main 执行异常: {e}"}}

    if isinstance(result, dict):
        return _strip_internal_tool_result(result)
    return {"ok": False, "data": None, "error": {"type": "ToolError", "message": f"工具 {script_name} agent_main 返回非 dict"}}


def execute_tool_script(script_name: str, args: Dict[str, Any]) -> dict:
    """统一进程内执行工具（源码运行 / PyInstaller 打包后均走此路）"""
    import importlib
    import io

    with _TOOL_EXEC_LOCK:
        return _execute_tool_script_locked(script_name, args)


def _execute_tool_script_locked(script_name: str, args: Dict[str, Any]) -> dict:
    """execute_tool_script 的加锁实现；保护进程内工具对 sys.argv/stdout 的全局修改。"""
    import importlib
    import io

    script_path = TOOLS_DIR / script_name
    if not script_path.exists():
        hint = [str(script_path)]
        try:
            cands = sorted({p.name for p in TOOLS_DIR.glob("cli_*.py")})[:50]
            hint.append("\n\n现有 cli_*.py（节选）：" + ", ".join(cands))
        except Exception:
            pass
        return {"ok": False, "data": None, "error": {"type": "ToolNotFound", "message": "\n".join(hint)}}

    args = normalize_cli_args(args)
    mod_name = script_name.replace('.py', '')
    tools_path = str(TOOLS_DIR)
    if tools_path not in sys.path:
        sys.path.insert(0, tools_path)

    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": "ImportError", "message": f"进程内加载 {script_name} 失败: {e}"}}

    agent_result = _try_execute_tool_agent_main(script_name, mod, args)
    if agent_result is not None:
        return agent_result

    # agent_main 不可用或参数属于 CLI 外壳能力时，回退到 main() 模拟 argv。
    argv = [script_name]
    for k, v in args.items():
        if v is None:
            continue
        if isinstance(v, bool):
            if k == "--safeMode" and script_name == "cli_command_exec.py":
                argv.append("--safeMode" if v else "--no-safeMode")
                continue
            if v:
                argv.append(k)
            continue
        argv.append(k)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        argv.append(str(v))
    if "--jsonOut" not in args:
        argv.append("--jsonOut")

    old_argv = sys.argv
    old_stdout = sys.stdout
    captured = io.StringIO()
    try:
        sys.argv = argv
        sys.stdout = captured
        mod.main()
    except SystemExit:
        pass  # argparse 正常退出
    except Exception as e:
        sys.stdout = old_stdout
        sys.argv = old_argv
        return {"ok": False, "data": None, "error": {"type": "ToolError", "message": f"工具 {script_name} 执行异常: {e}"}}
    finally:
        sys.stdout = old_stdout
        sys.argv = old_argv

    output = captured.getvalue().strip()
    if output:
        lines = output.splitlines()
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError:
            return {"ok": False, "data": None, "error": {"type": "JSONParseError", "message": f"工具 {script_name} 输出解析失败: {lines[-1][:300]}"}}
    return {"ok": False, "data": None, "error": {"type": "EmptyOutput", "message": f"工具 {script_name} 无有效输出"}}


def preview_payload(d: dict, limit: int = 50000) -> str:
    """返回完整的 JSON，不截断。limit<=0 时不检查大小。"""
    if limit > 0:
        s = json.dumps(d, ensure_ascii=False)
        if len(s) <= limit:
            return json.dumps(d, ensure_ascii=False, indent=2)
    try:
        return json.dumps(d, ensure_ascii=False, indent=2)
    except Exception:
        return str(d)


def preview_tool_result(script_name: str, result: dict, text_limit: int = 12000) -> str:
    """SSE tool_end.preview：extract 返回文本可能很大，preview_payload 整段截断会导致 JSON 不完整，前端无法解析出 data.text。"""
    sn = (script_name or "").lower()
    if "preview_render" in sn:
        return preview_payload(result, limit=0)
    if isinstance(result, dict) and result.get("ok") and isinstance(result.get("data"), dict):
        d = result["data"]
        if d.get("type") == "extract" and isinstance(d.get("text"), str) and "structured_edit" in sn:
            text = d["text"]
            snippet = text if len(text) <= text_limit else text[:text_limit] + "\n…"
            slim = {
                "ok": True,
                "data": {
                    "type": "extract",
                    "resultLen": len(text),
                    "written": bool(d.get("written")),
                    "text": snippet,
                },
            }
            return json.dumps(slim, ensure_ascii=False)
        if (
            d.get("type") == "replace_range"
            and "structured_edit" in sn
            and isinstance(d.get("previewFullBefore"), str)
            and isinstance(d.get("previewFullAfter"), str)
        ):
            bef = d["previewFullBefore"]
            aft = d["previewFullAfter"]
            lim = text_limit
            if len(bef) > lim:
                bef = bef[:lim] + "\n…"
            if len(aft) > lim:
                aft = aft[:lim] + "\n…"
            slim = {
                "ok": True,
                "data": {
                    "type": "replace_range",
                    "file": d.get("file"),
                    "previewFullBefore": bef,
                    "previewFullAfter": aft,
                    "previewTruncated": bool(
                        len(d["previewFullBefore"]) > lim or len(d["previewFullAfter"]) > lim
                    ),
                },
            }
            return json.dumps(slim, ensure_ascii=False)
        if ("command_exec" in sn or "python_inline" in sn) and isinstance(d.get("stdout"), str):
            out = d["stdout"]
            snippet = out if len(out) <= text_limit else out[:text_limit] + "\n…"
            slim = {"ok": True, "data": {"stdout": snippet, "stdoutLen": len(out)}}
            return json.dumps(slim, ensure_ascii=False)
        if "orch_dispatch" in sn and isinstance(d.get("cards"), list):
            cards = d.get("cards") or []
            slim = {
                "ok": True,
                "data": {
                    "exitCode": d.get("exitCode"),
                    "cards": cards,
                    "writeTargets": d.get("writeTargets") or [],
                    "stdout": (d.get("stdout") or "")[:2000],
                    "stderr": (d.get("stderr") or "")[:2000],
                },
            }
            return json.dumps(slim, ensure_ascii=False)
        if "regex_locate" in sn and isinstance(d.get("items"), list):
            items = d["items"]
            snippets = []
            for item in items[:30]:
                fp = item.get("file", "")
                ln = item.get("line", 0)
                col = item.get("column", 0)
                mt = item.get("match", "")
                context = ""
                try:
                    if fp and ln:
                        with open(fp, "r", encoding="utf-8", errors="replace") as _f:
                            _lines = _f.readlines()
                        if 1 <= ln <= len(_lines):
                            raw = _lines[ln - 1].rstrip("\r\n")
                            s = max(0, col - 1)
                            e = min(len(raw), s + len(mt))
                            marked = raw[:s] + "【" + raw[s:e] + "】" + raw[e:]
                            context = marked
                except Exception:
                    context = ""
                snippets.append(
                    "%s:%d:%d %s"
                    % (
                        fp.split("/")[-1] if "/" in fp else fp.split("\\")[-1] if "\\" in fp else fp,
                        ln,
                        col,
                        context if context else mt,
                    )
                )
            slim = {
                "ok": True,
                "data": {
                    "type": "regex_locate",
                    "count": d["count"],
                    "snippets": snippets,
                },
            }
            return json.dumps(slim, ensure_ascii=False)
    return preview_payload(result)


_CHAT_DIFF_BODY_MAX = 16000


def _fenced_diff_from_unified_lines(lines: List[str]) -> str:
    body = "\n".join(lines)
    if len(body) > _CHAT_DIFF_BODY_MAX:
        body = body[:_CHAT_DIFF_BODY_MAX] + "\n…"
    return "```diff\n" + body + "\n```"


def _chat_diff_markdown_for_tool(script_name: str, result: dict, exec_args: Dict[str, Any]) -> Optional[str]:
    sn = (script_name or "").lower()
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    if "text_diff" in sn:
        dm = data.get("diffMarkdown")
        return dm if isinstance(dm, str) and dm.strip() else None
    if "find_replace" in sn:
        dm = data.get("diffMarkdown")
        return dm if isinstance(dm, str) and dm.strip() else None
    if "structured_edit" not in sn:
        return None
    t = data.get("type")
    if t == "replace_range" and isinstance(data.get("previewFullBefore"), str) and isinstance(
        data.get("previewFullAfter"), str
    ):
        bef = data["previewFullBefore"]
        aft = data["previewFullAfter"]
        fp = exec_args.get("--file") if isinstance(exec_args, dict) else None
        if fp is None and isinstance(exec_args, dict):
            fp = exec_args.get("file")
        label = "file"
        if isinstance(fp, str) and fp.strip():
            label = Path(fp).name
        dl = list(
            difflib.unified_diff(
                bef.splitlines(),
                aft.splitlines(),
                fromfile=label,
                tofile=label,
                lineterm="",
                n=3,
            )
        )
        if not dl:
            return None
        return _fenced_diff_from_unified_lines(dl)
    if t != "replace_literal":
        return None
    pl_raw = exec_args.get("payload") if isinstance(exec_args, dict) else None
    if pl_raw is None and isinstance(exec_args, dict):
        pl_raw = exec_args.get("--payload")
    if isinstance(pl_raw, str):
        try:
            pl_obj: Any = json.loads(pl_raw)
        except Exception:
            pl_obj = None
    elif isinstance(pl_raw, dict):
        pl_obj = pl_raw
    else:
        pl_obj = None
    if not isinstance(pl_obj, dict) or pl_obj.get("type") != "replace_literal":
        return None
    ot, nt = pl_obj.get("oldText"), pl_obj.get("newText")
    if not isinstance(ot, str) or not isinstance(nt, str):
        return None
    fp = exec_args.get("--file") if isinstance(exec_args, dict) else None
    if fp is None and isinstance(exec_args, dict):
        fp = exec_args.get("file")
    label = "文件"
    if isinstance(fp, str) and fp.strip():
        label = Path(fp).name
    dl = list(
        difflib.unified_diff(
            ot.splitlines(),
            nt.splitlines(),
            fromfile=label + " · 改前",
            tofile=label + " · 改后",
            lineterm="",
            n=3,
        )
    )
    if not dl:
        return None
    if "\n" not in ot and "\n" not in nt:
        dl = [
            (
                "# 字面替换对照；@@ 内为片段行号，不是文件全文第几行。"
                if re.match(r"^@@ -\d+ \+\d+ @@$", line)
                else line
            )
            for line in dl
        ]
    return _fenced_diff_from_unified_lines(dl)


MAX_TOOL_RESULT_CHARS: int = 32000


def _truncate_tool_result(result: dict, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """安全截断工具 result：在 dict 值级别截断大字符串/列表，始终输出合法 JSON。"""
    raw = json.dumps(result, ensure_ascii=False)
    if len(raw) <= max_chars:
        return raw

    truncated = copy.deepcopy(result)
    data = truncated.get("data")
    if isinstance(data, dict):
        _truncate_large_values(data, max_chars, level=0)

    rebuilt = json.dumps(truncated, ensure_ascii=False)
    if len(rebuilt) <= max_chars:
        return rebuilt

    # 兜底：值级截断后仍超限 → 全量硬截断后以摘要 JSON 形式返回
    return json.dumps(
        {
            "ok": bool(result.get("ok")),
            "_truncated": True,
            "_notice": f"工具返回超出限额({max_chars}字符)，已截断。需完整内容请自行调用工具分批读取。",
            "resultLen": len(raw),
        },
        ensure_ascii=False,
    )


def _is_user_confirm_required(result: dict) -> bool:
    if not isinstance(result, dict) or result.get("ok"):
        return False
    err = result.get("error") or {}
    return err.get("code") == "E_USER_CONFIRM_REQUIRED"


def _merge_confirm_into_user_confirm_args(exec_args: Dict[str, Any], confirm: str) -> Dict[str, Any]:
    out = dict(exec_args)
    pkey = "--payload" if "--payload" in out else ("payload" if "payload" in out else None)
    if pkey:
        raw = out[pkey]
        if isinstance(raw, dict):
            obj = dict(raw)
        elif isinstance(raw, str):
            try:
                obj = json.loads(raw) if raw.strip() else {}
            except Exception:
                obj = {}
        else:
            obj = {}
        if not isinstance(obj, dict):
            obj = {}
        obj["confirm"] = confirm
        out[pkey] = json.dumps(obj, ensure_ascii=False)
    else:
        out["--confirm"] = confirm
    return out




def _truncate_large_values(d: dict, budget: int, level: int = 0) -> None:
    """递归截断 dict 中的大字符串/大列表（就地修改）。

    level=0 时单段 extract.text 优先占满 budget（预留 JSON 开销），避免数千字全文被误标「截断」；
    level>=1 强截断（200 字封顶）。
    """
    limit = 200 if level >= 1 else max(budget // 4, 200)
    extract_text = level == 0 and str(d.get("type")) == "extract"
    for k, v in list(d.items()):
        if isinstance(v, str):
            eff = limit
            if level == 0 and k == "diffMarkdown":
                eff = max(200, budget - 800)
            elif extract_text and k == "text":
                eff = max(200, budget - 800)
            if len(v) > eff:
                orig_len = len(v)
                d[k] = v[:eff] + f"\n[…截断，原文 {orig_len} 字]"
        elif isinstance(v, list) and v:
            sample = str(v[0]) if v else ""
            item_len = len(sample)
            keep_max = 5 if level >= 1 else max(1, budget // 3 // max(item_len, 1))
            if (item_len > 0 and len(v) * item_len > budget // 3) or level >= 1:
                keep = min(keep_max, len(v))
                tail = [] if keep >= len(v) else [f"[…剩余 {len(v) - keep} 项已截断]"]
                d[k] = v[:keep] + tail
        elif isinstance(v, dict):
            _truncate_large_values(v, budget, level)



def _is_preview_intent(user_text: str) -> bool:
    t = str(user_text or "")
    return any(k in t for k in PREVIEW_INTENT_KEYS)


def _build_direct_preview_message(script_name: str, result: dict, user_text: str) -> Optional[str]:
    # 仅在用户明确提出“预览/原文”时，才允许把预览内容直出到主对话。
    if not _is_preview_intent(user_text):
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    sn = (script_name or "").lower()
    if "preview_render" in sn and isinstance(data.get("previewText"), str):
        return data["previewText"]
    return None


def _pick_preview_args_from_result(result: dict, fallback_args: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        data = {}
    for k in ("outFile", "file", "path", "targetFile"):
        v = data.get(k)
        if isinstance(v, str) and v.strip() and Path(v).exists() and Path(v).is_file():
            return {"file": v, "label": "预览"}
    if isinstance(fallback_args, dict):
        for k in ("file", "path", "targetFile"):
            v = fallback_args.get(k)
            if isinstance(v, str) and v.strip() and Path(v).exists() and Path(v).is_file():
                return {"file": v, "label": "预览"}
    for k in ("text", "stdout", "previewText", "content", "stderr"):
        v = data.get(k)
        if isinstance(v, str) and v:
            return {"text": v, "label": "预览"}
    return None





_REASONING_EFFORTS: Dict[str, str] = {}



def _chat_api_key_available() -> bool:
    """检查 API Key 是否已配置"""
    key = os.environ.get("CHAT_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    return bool(key and key.strip() and key != "你的 DeepSeek API KEY" and key != "sk-你的API密钥")


def _get_reasoning_effort(cid: str = "") -> str:
    """从会话级或全局环境变量获取 reasoning_effort（默认 high），可选 high/max。"""
    cid_key = str(cid or "").strip()
    if cid_key and cid_key in _REASONING_EFFORTS:
        return _REASONING_EFFORTS[cid_key]
    raw = (os.environ.get("REASONING_EFFORT") or "high").strip().lower()
    if raw in ("high", "max"):
        return raw
    if raw in ("low", "medium"):
        return "high"
    if raw in ("xhigh",):
        return "max"
    return "high"


def _set_reasoning_effort(cid: str, effort: str) -> bool:
    """设置会话级 reasoning_effort。返回是否设置成功。"""
    e = str(effort or "").strip().lower()
    if e not in ("high", "max"):
        return False
    _REASONING_EFFORTS[str(cid or "").strip()] = e
    return True


def deepseek_request(payload: dict) -> dict:
    return chat_completion_request(payload)


def deepseek_stream_request(payload: dict):
    yield from chat_completion_stream(payload)


def _choice_snapshot_message(choice0: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(choice0, dict):
        return None
    m = choice0.get("message")
    return m if isinstance(m, dict) and m else None


def _finalize_stream_reasoning(reasoning_delta: str, last_message: Optional[Dict[str, Any]]) -> str:
    """用末帧 choices[0].message 补全 delta 未收齐的推理字段（CHAT_API_REASONING_DELTA_FIELDS）。"""
    out = str(reasoning_delta or "")
    if not isinstance(last_message, dict):
        return out
    lm_r = _best_message_reasoning_field(last_message)
    if lm_r:
        if len(lm_r) >= len(out):
            return lm_r
        if not out:
            return lm_r
    return out


def _reasoning_stream_finalize_events(before: str, after: str, round_num: int) -> List[Dict[str, Any]]:
    """流式已推送片段后，finalize 若多出正文则再推一条 delta；若整体替换则推 reasoning_sync。"""
    if after == before:
        return []
    if after.startswith(before) and len(after) > len(before):
        return [{"type": "reasoning_delta", "round": round_num, "delta": after[len(before):]}]
    return [{"type": "reasoning_sync", "round": round_num, "text": after}]


def _finalize_stream_content_text(content_delta: str, last_message: Optional[Dict[str, Any]]) -> str:
    """末帧 content 补齐（如正文仅在 message 里）。"""
    out = str(content_delta or "")
    if not isinstance(last_message, dict):
        return out
    lm_c = last_message.get("content")
    if isinstance(lm_c, str) and lm_c and not out.strip():
        return lm_c
    return out


def _merge_stream_tool_calls(chunks: List[dict]) -> List[dict]:
    merged: Dict[int, dict] = {}
    for item in chunks:
        idx = int(item.get("index", 0) or 0)
        cur = merged.get(idx)
        if cur is None:
            cur = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            merged[idx] = cur
        if item.get("id"):
            cur["id"] = str(item["id"])
        fn = item.get("function") or {}
        name_part = fn.get("name")
        args_part = fn.get("arguments")
        if isinstance(name_part, str) and name_part:
            cur["function"]["name"] += name_part
        if isinstance(args_part, str) and args_part:
            cur["function"]["arguments"] += args_part
    out: List[dict] = []
    for idx in sorted(merged.keys()):
        tc = merged[idx]
        if not tc["function"]["arguments"]:
            tc["function"]["arguments"] = "{}"
        if tc["function"]["name"]:
            out.append(tc)
    return out




def _normalize_client_ip_for_tools(ip_raw: Optional[str]) -> str:
    ip = str(ip_raw or "").strip()
    if not ip:
        return ""
    low = ip.lower()
    if low in {"localhost", "0.0.0.0", "127.0.0.1", "::1"}:
        return ""
    try:
        obj = ipaddress.ip_address(ip)
        if obj.is_loopback or obj.is_unspecified or obj.is_private:
            return ""
        return ip
    except ValueError:
        return ""


PLAN_MODE_KEYS = ("plan模式", "规划模式", "先给方案", "只给方案", "仅方案", "进入plan", "先不要执行")
EXECUTE_MODE_KEYS = ("执行模式", "进入执行", "开始执行", "落地", "实施", "按方案执行")
PLAN_MODE_COMMANDS = ("/plan", "#plan", "\\plan")
EXECUTE_MODE_COMMANDS = ("/execute", "#execute", "\\execute")


def _has_explicit_mode_command(text: str, commands: Tuple[str, ...]) -> bool:
    raw = str(text or "")
    parts = [x.strip().lower() for x in re.split(r"[\s,;，。]+", raw) if x.strip()]
    return any(p in commands for p in parts)


def _resolve_conversation_mode(conversation_id: str, user_text: str, mode_hint: str = "") -> str:
    t = str(user_text or "").lower()
    mode = CONVERSATION_MODES.get(conversation_id, "execute")
    hint = str(mode_hint or "").strip().lower()
    if hint in {"auto", "plan", "execute"}:
        mode = hint
    elif _has_explicit_mode_command(t, PLAN_MODE_COMMANDS):
        mode = "plan"
    elif _has_explicit_mode_command(t, EXECUTE_MODE_COMMANDS):
        mode = "execute"
    elif any(k in t for k in PLAN_MODE_KEYS):
        mode = "plan"
    elif any(k in t for k in EXECUTE_MODE_KEYS):
        mode = "execute"
    CONVERSATION_MODES[conversation_id] = mode
    return mode



# KB max file size (from config, default 200KB)
_KB_MAX_FILE_SIZE = int(AGENT_CONFIG.get("AGENT_KB_MAX_FILE_SIZE", 200000))
def _build_kb_prompt(cid: str) -> str:
    """读取当前会话勾选的知识库文件，拼接为提示词片段"""
    if not KB_BASE_DIR or not KB_BASE_DIR.is_dir():
        return ""
    with _KB_CHECKED_LOCK:
        checked = _KB_CHECKED_STATE.get(cid, set())
        # 如内存无状态，尝试从磁盘文件恢复
        if not checked:
            _kb_load_single_cid_checked(cid)
            checked = _KB_CHECKED_STATE.get(cid, set())
    if not checked:
        return ""
    parts = ["【知识库参考内容】"]
    for rel in sorted(checked):
        fpath = KB_BASE_DIR / rel
        if not fpath.is_file() or fpath.stat().st_size > _KB_MAX_FILE_SIZE:
            continue
        ext = fpath.suffix.lower()
        try:
            if ext in (".xlsx", ".xls"):
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(fpath, read_only=True, data_only=True)
                    rows = []
                    for ws in wb.worksheets:
                        sheet_name = ws.title
                        rows.append(f"[Sheet: {sheet_name}]")
                        for row in ws.iter_rows(values_only=True):
                            row_vals = [str(v) if v is not None else "" for v in row]
                            rows.append("\t".join(row_vals))
                    wb.close()
                    text = "\n".join(rows)
                except ImportError:
                    text = f"[需安装 openpyxl 才能读取: pip install openpyxl]"
            elif ext == ".csv":
                text = fpath.read_text(encoding="utf-8", errors="replace")
            else:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            parts.append(f"\n--- {rel} ---\n{text.strip()}")
        except Exception:
            continue
    if len(parts) == 1:
        return ""
    return "\n\n".join(parts)


def _extract_dispatch_title(content: Optional[str], max_len: int = 20) -> Optional[str]:
    if not content:
        return None
    text = str(content)
    m = re.search(r"\[\[TOOL_TITLE\]\]\s*(.+)", text, flags=re.IGNORECASE)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    if not title:
        return None
    # 安全截断，避免前端标题过长撑破布局
    if len(title) > max_len:
        title = title[:max_len].rstrip() + "…"
    return title



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




def _strip_internal_message_for_api(msg: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(msg)
    for k in list(out.keys()):
        if str(k).startswith("_agent_"):
            out.pop(k, None)
    return out


def _normalize_persisted_conversation(messages: List[Dict[str, Any]]) -> None:
    if not messages:
        messages.append({"role": "system", "content": TOOL_AGENT_SYSTEM_PROMPT})
        return
    if messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": TOOL_AGENT_SYSTEM_PROMPT})
    else:
        messages[0] = {"role": "system", "content": TOOL_AGENT_SYSTEM_PROMPT}


def _find_first_user_index(messages: List[Dict[str, Any]]) -> Optional[int]:
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            return i
    return None


def _take_last_n_user_rounds(dialogue: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    if n <= 0 or not dialogue:
        return []
    user_idxs = [i for i, m in enumerate(dialogue) if m.get("role") == "user"]
    if len(user_idxs) <= n:
        return list(dialogue)
    cut = user_idxs[-n]
    return list(dialogue[cut:])


def _adjust_excerpt_range_half_open(messages: List[Dict[str, Any]], start: int, end: int) -> Tuple[int, int]:
    """0-based 半开区间 [start, end)。扩展以尽量覆盖完整 tool 链。"""
    n = len(messages)
    start = max(0, min(start, n))
    end = max(start, min(end, n))
    if start >= end:
        return start, end
    if start < n and messages[start].get("role") == "tool":
        t = start
        while t > 0 and messages[t - 1].get("role") == "tool":
            t -= 1
        if t > 0:
            prev = messages[t - 1]
            if prev.get("role") == "assistant" and prev.get("tool_calls"):
                start = t - 1
    i = start
    while i < end:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tc_list = m.get("tool_calls") or []
            need_ids = {
                str((tc or {}).get("id") or "")
                for tc in tc_list
                if isinstance(tc, dict) and (tc or {}).get("id")
            }
            need_ids.discard("")
            if not need_ids:
                i += 1
                continue
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                j += 1
            if j > end:
                end = min(j, n)
        i += 1
    return start, min(end, n)


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


def _merge_pending_excerpts_for_conversation(cid: str, messages: List[Dict[str, Any]]) -> None:
    with _SUMMARY_STATE_LOCK:
        paths = list(PENDING_EXCERPT_PATHS.pop(cid, []) or [])
    for path_str in paths:
        p = Path(path_str)
        if not p.is_file():
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            continue
        blob, body = _parse_excerpt_file(raw)
        am = blob.get("agent_excerpt_meta") if isinstance(blob, dict) else None
        if not isinstance(am, dict):
            continue
        try:
            s = int(am["start_idx"])
            e = int(am["end_idx"])
        except (KeyError, TypeError, ValueError):
            continue
        s, e = _adjust_excerpt_range_half_open(messages, s, e)
        if s >= e or s >= len(messages):
            continue
        e = min(e, len(messages))
        del messages[s:e]
        fu = _find_first_user_index(messages)
        ins = fu if fu is not None else len(messages)
        summary_msg = {
            "role": "system",
            "content": "【历史摘要】\n" + str(body or "").strip(),
            "_agent_summary": True,
        }
        messages.insert(ins, summary_msg)


def _ephemeral_mode_system_tail(mode: str) -> Dict[str, Any]:
    if mode == "plan":
        text = "⚠️ " + TOOL_AGENT_PLAN_MODE_PROMPT
    elif mode == "execute":
        text = "⚠️ " + TOOL_AGENT_EXECUTE_MODE_PROMPT
    else:
        text = TOOL_AGENT_AUTO_MODE_PROMPT
    return {"role": "system", "content": text}


def _stored_mode_for_tail(conversation_id: str) -> str:
    m = CONVERSATION_MODES.get(conversation_id)
    if m == "plan":
        return "plan"
    if m == "execute":
        return "execute"
    return "auto"


TOOL_PAIRING_SYNTH_MAX_MISSING = 1
_TOOL_REPAIR_BODY = json.dumps(
    {"ok": False, "error": {"type": "ClientRepair", "message": "工具回合未完整配对，会话自检已填入占位结果"}},
    ensure_ascii=False,
)


def _assistant_tool_call_ids(assistant_msg: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for tc in assistant_msg.get("tool_calls") or []:
        if isinstance(tc, dict) and tc.get("id"):
            out.add(str(tc["id"]))
    return out


def _synthetic_tool_result(tool_call_id: str) -> Dict[str, Any]:
    return {"role": "tool", "tool_call_id": str(tool_call_id), "content": _TOOL_REPAIR_BODY}


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


def _build_api_messages_for_model(persisted: List[Dict[str, Any]], conversation_id: str) -> List[Dict[str, Any]]:
    mode = _stored_mode_for_tail(conversation_id)
    fu = _find_first_user_index(persisted)
    sys_content = TOOL_AGENT_SYSTEM_PROMPT
    # 注入知识库内容
    kb_part = _build_kb_prompt(conversation_id)
    if kb_part:
        sys_content += "\n\n" + kb_part
    prefix = [{"role": "system", "content": sys_content}]
    summaries: List[Dict[str, Any]] = []
    if fu is not None and fu > 1:
        for m in persisted[1:fu]:
            if m.get("role") == "system" and m.get("_agent_summary"):
                summaries.append(_strip_internal_message_for_api(m))
    dialogue = persisted[fu:] if fu is not None else []
    recent = _take_last_n_user_rounds(dialogue, CONTEXT_RECENT_USER_ROUNDS)
    tail = [_strip_internal_message_for_api(m) for m in recent]
    built = prefix + summaries + tail + [_ephemeral_mode_system_tail(mode)]
    return _sanitize_tool_pairing_for_api(built)


def _summarize_messages_slice_with_llm(slice_msgs: List[Dict[str, Any]], cid: str = "") -> str:
    sys_h = (
        "你是对话整理助手。将下列聊天记录压缩为简明中文摘要，保留关键决定、未完成项、重要路径与工具结论；"
        "不要编造。输出纯文本。"
    )
    compact: List[Dict[str, Any]] = []
    for m in slice_msgs:
        r = m.get("role")
        if r == "user":
            compact.append({"role": "user", "content": str(m.get("content") or "")[:8000]})
        elif r == "assistant":
            compact.append({
                "role": "assistant",
                "content": str(m.get("content") or "")[:6000],
                "tool_calls": m.get("tool_calls"),
            })
        elif r == "tool":
            compact.append({"role": "tool", "content": str(m.get("content") or "")[:6000]})
        elif r == "system":
            compact.append({"role": "system", "content": str(m.get("content") or "")[:4000]})
    reff = _get_reasoning_effort(cid)
    payload = {
        "model": default_model_from_env(),
        "messages": [
            {"role": "system", "content": sys_h},
            {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
        ],
        "reasoning_effort": reff,
        "thinking": {"type": "enabled"},
        "temperature": 0.2,
    }
    data = deepseek_request(payload)
    choices = data.get("choices") or []
    ch0 = choices[0] if choices else {}
    msg = (ch0 or {}).get("message") or {}
    c = msg.get("content")
    return str(c or "").strip() or "(摘要为空)"


def _maybe_schedule_summarization(cid: str, messages: List[Dict[str, Any]]) -> None:
    with _SUMMARY_STATE_LOCK:
        alive = SUMMARY_IN_PROGRESS.get(cid)
        if alive is not None and time.time() >= alive:
            SUMMARY_IN_PROGRESS.pop(cid, None)
            alive = None
        if len(messages) <= CONTEXT_HISTORY_MAX_MESSAGES:
            return
        if alive is not None:
            return
        start = CONTEXT_EXCERPT_START_INDEX
        end = len(messages)
        if start >= end:
            return
        s_adj, e_adj = _adjust_excerpt_range_half_open(messages, start, end)
        if s_adj >= e_adj:
            return
        slice_copy = copy.deepcopy(messages[s_adj:e_adj])
        SUMMARY_IN_PROGRESS[cid] = time.time() + SUMMARY_IN_PROGRESS_TTL_SEC

    def _run() -> None:
        try:
            text = _summarize_messages_slice_with_llm(slice_copy, cid)
            ts = int(time.time() * 1000)
            EXCERPTS_DIR.mkdir(parents=True, exist_ok=True)
            path = EXCERPTS_DIR / f"{cid}_{ts}.md"
            meta = {"start_idx": s_adj, "end_idx": e_adj, "conversation_id": cid, "end_exclusive": True}
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


def run_agent_turn(
    conversation_id: str,
    user_text: str,
    client_ip: str = "",
    mode_hint: str = "",
    *,
    resume_after_user_confirm: bool = False,
    run_id: str = "",
):
    """Yields SSE lines (without prefix) as dicts (caller wraps data:)."""
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
        _merge_pending_excerpts_for_conversation(conversation_id, messages)
        rollback_messages = copy.deepcopy(messages)
        messages.append({"role": "user", "content": user_text})
    else:
        _normalize_persisted_conversation(messages)
        rollback_messages = copy.deepcopy(messages)
    user_text_for_preview = user_text
    if resume_after_user_confirm:
        user_text_for_preview = ""
        for _m in reversed(messages):
            if _m.get("role") == "user":
                user_text_for_preview = str(_m.get("content") or "")
                break
    em = effective_model(conversation_id)
    yield {"type": "conversation", "conversation_id": conversation_id, "message_count": len(messages), "mode": mode, "model": em, "reasoning_effort": _get_reasoning_effort(conversation_id)}
    turn_tool_records: List[Dict[str, Any]] = []

    api_messages = _build_api_messages_for_model(messages, conversation_id)
    for _round in range(MAX_TOOL_ROUNDS):
        if _consume_conversation_stop_requested(conversation_id, run_id):
            yield _finish_conversation_stopped(conversation_id, rollback_messages)
            return
        yield {"type": "llm_round", "round": _round + 1}
        reff = _get_reasoning_effort(conversation_id)
        body: Dict[str, Any] = {
            "model": em,
            "messages": api_messages,
            "reasoning_effort": reff,
            "thinking": {"type": "enabled"},
            "temperature": 0.2,
            "tools": otools_sorted,
        }
        yield {"type": "llm_request", "round": _round + 1, "params": {"model": em, "thinking": True, "reasoning_effort": reff, "temperature": 0.2, "messagesCount": len(api_messages), "toolsCount": len(otools_sorted), "hasTools": True}}
        last_choice_message: Optional[Dict[str, Any]] = None
        try:
            usage: Dict[str, Any] = {}
            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            stream_tool_calls: List[dict] = []
            for chunk in deepseek_stream_request(body):
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
                    yield {"type": "assistant_delta", "delta": piece}
                for _rn in _reasoning_delta_field_names():
                    rp = delta.get(_rn)
                    if isinstance(rp, str) and rp:
                        reasoning_parts.append(rp)
                        yield {"type": "reasoning_delta", "round": _round + 1, "delta": rp}
                tdelta = delta.get("tool_calls")
                if isinstance(tdelta, list) and tdelta:
                    stream_tool_calls.extend([x for x in tdelta if isinstance(x, dict)])
            yield {"type": "llm_done"}
        except HTTPException as e:
            yield {"type": "llm_response", "round": _round + 1, "params": {"error": e.detail}}
            yield {"type": "error", "where": "chat_api", "detail": e.detail}
            CONVERSATIONS[conversation_id] = messages
            _save_conversation(conversation_id, messages)
            return

        if usage:
            yield {"type": "usage", "usage": usage}

        tcalls = _merge_stream_tool_calls(stream_tool_calls)
        content = "".join(content_parts)
        reasoning_before_finalize = "".join(reasoning_parts)
        reasoning_content = reasoning_before_finalize
        if last_choice_message is not None:
            reasoning_content = _finalize_stream_reasoning(reasoning_content, last_choice_message)
            content = _finalize_stream_content_text(content, last_choice_message)
        for _rfe in _reasoning_stream_finalize_events(reasoning_before_finalize, reasoning_content, _round + 1):
            yield _rfe
        yield {"type": "llm_response", "round": _round + 1, "params": {"toolCallsCount": len(tcalls), "contentChars": len(content), "reasoningChars": len(reasoning_content), "usage": usage}}
        if tcalls:
            dispatch_title = _extract_dispatch_title(content)
            if dispatch_title:
                yield {"type": "dispatch_title", "title": dispatch_title}
            assistant_msg = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": tcalls,
                "reasoning_content": reasoning_content or "",
            }
            messages.append(assistant_msg)
            direct_preview_content: Optional[str] = None
            for tc in tcalls:
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
                explicit_preview: Optional[bool] = None
                if isinstance(args, dict) and "usePreview" in args:
                    _up = args.get("usePreview")
                    if isinstance(_up, bool):
                        explicit_preview = _up
                script = script_by_api.get(api_name or "")
                if script == "cli_structured_edit.py" and isinstance(args, dict):
                    args = _repair_structured_edit_args(args)
                exec_args = dict(args) if isinstance(args, dict) else {}
                exec_args.pop("usePreview", None)
                if explicit_preview is not None:
                    use_preview = explicit_preview
                else:
                    use_preview = _default_use_preview_link(script, args)
                if script in ("cli_command_exec.py", "cli_python_inline.py"):
                    use_preview = True
                if script and isinstance(args, dict):
                    sl = script.lower()
                    if ("open_meteo" in sl or "ip_geolocate" in sl) and not str(exec_args.get("ip") or "").strip() and client_ip:
                        clean_ip = _normalize_client_ip_for_tools(client_ip)
                        if clean_ip:
                            args["ip"] = clean_ip
                            exec_args["ip"] = clean_ip
                if not script:
                    result = _unknown_tool_result(api_name, script_by_api)
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
                    yield {
                        "type": "tool_start",
                        "api_name": api_name,
                        "script": "(unknown)",
                        "args": args,
                        "tool_call_id": tc.get("id"),
                        "step_title": step_title,
                    }
                    yield {
                        "type": "tool_end",
                        "api_name": api_name,
                        "script": "(unknown)",
                        "tool_call_id": tc.get("id"),
                        "ok": False,
                        "preview": preview_tool_result("(unknown)", result),
                    }
                else:
                    yield {
                        "type": "tool_start",
                        "api_name": api_name,
                        "script": script,
                        "args": args,
                        "tool_call_id": tc.get("id"),
                        "step_title": step_title,
                    }
                    if script == "cli_run_type.py":
                        result = _execute_cli_run_type(conversation_id, exec_args)
                    elif script == "cli_todo_list.py":
                        result = _execute_cli_todo_list(conversation_id, exec_args)
                    else:
                        _WRITE_TOOLS_RUNTYPE = frozenset({"cli_structured_edit.py", "cli_file_ops.py", "cli_patch_apply.py", "cli_command_exec.py", "cli_python_inline.py"})
                        if script in _WRITE_TOOLS_RUNTYPE:
                            current_mode = CONVERSATION_MODES.get(conversation_id, "")
                            if current_mode == "plan":
                                # Plan 模式：cli_structured_edit 交工具自身细粒度判断（extract/delete_segments 无 outFile 放行）
                                if script == "cli_structured_edit.py":
                                    result = execute_tool_script(script, exec_args)
                                else:
                                    # Plan 模式下所有写操作一律拒绝
                                    result = {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，禁止执行写操作。请先切换为 Execute 模式后再执行。"}}
                            elif current_mode == "execute":
                                # Execute 模式下必须有执行清单(Todo-List)
                                _todo = TODO_LISTS.get(conversation_id)
                                _no_todo = _todo is None or not _todo.get("items")
                                if _no_todo:
                                    result = {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Execute 模式，但未找到执行清单(Todo-List)。请先用 cli_todo_list create 创建执行清单后再执行写操作。"}}
                                else:
                                    result = execute_tool_script(script, exec_args)
                            else:
                                # Auto 模式：不拦截
                                result = execute_tool_script(script, exec_args)
                        else:
                            result = execute_tool_script(script, exec_args)
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
                    if script == "cli_user_confirm.py" and _is_user_confirm_required(result) and isinstance(exec_args, dict):
                        _te_tool_end["user_confirm_required"] = True
                        _ucd = result.get("data") or {}
                        _te_tool_end["user_confirm_title"] = str(_ucd.get("title") or "")
                        _cos = _ucd.get("confirms")
                        _te_tool_end["user_confirm_options"] = list(_cos) if isinstance(_cos, list) else []
                        if bool(_ucd.get("multi")):
                            _te_tool_end["user_confirm_multi"] = True
                        _cix = _ucd.get("customOptionIndex")
                        if isinstance(_cix, int):
                            _te_tool_end["user_confirm_custom_index"] = _cix
                        PENDING_USER_CONFIRM[conversation_id] = {
                            "tool_call_id": str(tc.get("id") or ""),
                            "exec_args": dict(exec_args),
                        }
                    if script == "cli_todo_list.py" and isinstance(result, dict) and result.get("ok"):
                        _td = result.get("data")
                        if _td is None:
                            # 无活跃清单 → 发送关闭信号
                            _te_tool_end["todo_list"] = True
                            _te_tool_end["todo_list_data"] = {"close": True}
                            sse_data = {"type": "todo_list", "conversation_id": conversation_id, "close": True}
                            yield sse_data
                        elif isinstance(_td, dict):
                            if _td.get("close") is True:
                                _te_tool_end["todo_list"] = True
                                _te_tool_end["todo_list_data"] = _td
                                sse_data = {"type": "todo_list", "conversation_id": conversation_id, "close": True}
                                yield sse_data
                            elif isinstance(_td.get("items"), list):
                                _te_tool_end["todo_list"] = True
                                _te_tool_end["todo_list_data"] = _td
                                TODO_LISTS[conversation_id] = _td
                                # 发送独立的 todo_list SSE 事件供前端专属区域渲染
                                sse_data = {
                                    "type": "todo_list",
                                    "listId": str(_td.get("listId") or ""),
                                    "items": _td["items"],
                                    "allDone": all(it.get("done") for it in _td["items"]),
                                }
                                sse_data["collapsed"] = bool(_td.get("collapsed"))
                                if _td.get("close"):
                                    sse_data["close"] = True
                                yield sse_data
                    yield _te_tool_end
                    
                    if script == "cli_run_type.py" and isinstance(result, dict) and result.get("ok"):
                        _dc = result.get("data") or {}
                        _rtm = _dc.get("runType")
                        if _rtm in ("auto", "plan", "execute"):
                            yield {"type": "mode_changed", "mode": _rtm}
                    md_chat = _chat_diff_markdown_for_tool(script, result, exec_args)
                    if md_chat:
                        yield {"type": "assistant_markdown", "markdown": md_chat}
                    if use_preview:
                        pa = _pick_preview_args_from_result(result, exec_args)
                        if isinstance(pa, dict):
                            linked_id = f"{tc.get('id')}:linked-preview"
                            yield {
                                "type": "tool_start",
                                "api_name": "cli_preview_render",
                                "script": "cli_preview_render.py",
                                "args": pa,
                                "tool_call_id": linked_id,
                            }
                            pr = execute_tool_script("cli_preview_render.py", pa)
                            _log_agent_console_tool(conversation_id, "cli_preview_render", "cli_preview_render.py", pa, pr)
                            if not (isinstance(pr, dict) and pr.get("ok") is True):
                                _record_tool_debug_failure(
                                    conversation_id=conversation_id,
                                    api_name="cli_preview_render",
                                    script="cli_preview_render.py",
                                    tool_call_id=linked_id,
                                    request=pa,
                                    response=pr,
                                    source="linked_preview_render",
                                )
                            yield {
                                "type": "tool_end",
                                "api_name": "cli_preview_render",
                                "script": "cli_preview_render.py",
                                "tool_call_id": linked_id,
                                "ok": bool(pr.get("ok")),
                                "preview": preview_tool_result("cli_preview_render.py", pr),
                            }
                            turn_tool_records.append({"api_name": "cli_preview_render", "script": "cli_preview_render.py", "ok": bool(pr.get("ok"))})
                            if direct_preview_content is None:
                                direct_preview_content = _build_direct_preview_message("cli_preview_render.py", pr, user_text_for_preview)
                    if direct_preview_content is None:
                        direct_preview_content = _build_direct_preview_message(script, result, user_text_for_preview)
                turn_tool_records.append({"api_name": api_name, "script": script or "(unknown)", "ok": bool(result.get("ok"))})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": _truncate_tool_result(result),
                })
            if conversation_id in PENDING_USER_CONFIRM:
                CONVERSATIONS[conversation_id] = messages
                _save_conversation(conversation_id, messages)
                yield {"type": "paused_for_user_confirm", "conversation_id": conversation_id}
                return
            if isinstance(direct_preview_content, str) and direct_preview_content:
                messages.append({"role": "assistant", "content": direct_preview_content, "reasoning_content": ""})
                if not content_parts:
                    yield {"type": "assistant", "content": direct_preview_content}
                break
            api_messages = _build_api_messages_for_model(messages, conversation_id)
            continue

        assistant_msg = {"role": "assistant", "content": content}
        if reasoning_content:
            assistant_msg["reasoning_content"] = reasoning_content
        messages.append(assistant_msg)
        if content and not content_parts:
            yield {"type": "assistant", "content": content}
        break
    else:
        max_rounds_rollback_messages = copy.deepcopy(rollback_messages)
        messages.append({"role": "user", "content": AGENT_MAX_TOOL_ROUNDS_USER_HINT})
        if _consume_conversation_stop_requested(conversation_id, run_id):
            yield _finish_conversation_stopped(conversation_id, max_rounds_rollback_messages)
            return
        yield {"type": "llm_round", "round": MAX_TOOL_ROUNDS + 1}
        api_messages = _build_api_messages_for_model(messages, conversation_id)
        reff = _get_reasoning_effort(conversation_id)
        wrap_body: Dict[str, Any] = {
            "model": em,
            "messages": api_messages,
            "reasoning_effort": reff,
            "thinking": {"type": "enabled"},
            "temperature": 0.2,
        }
        yield {"type": "llm_request", "round": MAX_TOOL_ROUNDS + 1, "params": {"model": em, "thinking": True, "reasoning_effort": reff, "temperature": 0.2, "messagesCount": len(api_messages), "toolsCount": 0, "hasTools": False}}
        last_choice_message_wrap: Optional[Dict[str, Any]] = None
        try:
            usage: Dict[str, Any] = {}
            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            stream_tool_calls: List[dict] = []
            for chunk in deepseek_stream_request(wrap_body):
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
                    yield {"type": "assistant_delta", "delta": piece}
                for _rn in _reasoning_delta_field_names():
                    rp = delta.get(_rn)
                    if isinstance(rp, str) and rp:
                        reasoning_parts.append(rp)
                        yield {"type": "reasoning_delta", "round": MAX_TOOL_ROUNDS + 1, "delta": rp}
                tdelta = delta.get("tool_calls")
                if isinstance(tdelta, list) and tdelta:
                    stream_tool_calls.extend([x for x in tdelta if isinstance(x, dict)])
            yield {"type": "llm_done"}
        except HTTPException as e:
            yield {"type": "llm_response", "round": MAX_TOOL_ROUNDS + 1, "params": {"error": e.detail}}
            yield {"type": "error", "where": "chat_api", "detail": e.detail}
            CONVERSATIONS[conversation_id] = messages
            _save_conversation(conversation_id, messages)
            return

        if usage:
            yield {"type": "usage", "usage": usage}

        tcalls = _merge_stream_tool_calls(stream_tool_calls)
        content = "".join(content_parts)
        reasoning_before_finalize = "".join(reasoning_parts)
        rc = reasoning_before_finalize
        if last_choice_message_wrap is not None:
            rc = _finalize_stream_reasoning(rc, last_choice_message_wrap)
            content = _finalize_stream_content_text(content, last_choice_message_wrap)
        for _rfe in _reasoning_stream_finalize_events(reasoning_before_finalize, rc, MAX_TOOL_ROUNDS + 1):
            yield _rfe
        content = content.strip()
        reasoning_content = rc.strip()
        yield {"type": "llm_response", "round": MAX_TOOL_ROUNDS + 1, "params": {"toolCallsCount": len(tcalls), "contentChars": len(content), "reasoningChars": len(reasoning_content), "usage": usage}}
        if tcalls:
            content = content or (
                "已达到工具调用次数上限；模型在收尾时仍尝试调用工具。请把任务拆成更小步骤或在本对话中追问。"
            )
        assistant_msg = {"role": "assistant", "content": content, "reasoning_content": reasoning_content or ""}
        messages.append(assistant_msg)
        if content and not content_parts:
            yield {"type": "assistant", "content": content}

    if not resume_after_user_confirm:
        _maybe_schedule_summarization(conversation_id, messages)
    CONVERSATIONS[conversation_id] = messages
    _save_conversation(conversation_id, messages)
    yield {"type": "done"}


# Embedded UI (VS Code-like dark theme, chat + steps). Served from / without external static files.
# ---- UI HTML loaded from external file ----
UI_HTML_FILE = AGENT_ROOT / "res" / "html" / "agent-ui.html"
UI_CSS_FILE = AGENT_ROOT / "res" / "css" / "agent-ui.css"
UI_JS_FILE = AGENT_ROOT / "res" / "js" / "agent-ui.js"

_INLINE_CSS = UI_CSS_FILE.read_text(encoding="utf-8")
_INLINE_JS = UI_JS_FILE.read_text(encoding="utf-8")
_INLINE_HTML_TMPL = UI_HTML_FILE.read_text(encoding="utf-8")
INLINE_UI_HTML = _INLINE_HTML_TMPL.replace("{{CSS}}", _INLINE_CSS).replace("{{JS}}", _INLINE_JS)




def _default_usage_accum_dict() -> Dict[str, int]:
    return {
        "session_token_used": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_cache_hit_tokens": 0,
        "total_cache_miss_tokens": 0,
    }


def _load_usage_accumulator() -> Dict[str, int]:
    base = _default_usage_accum_dict()
    try:
        if not USAGE_ACCUM_FILE.is_file():
            return base
        raw = json.loads(USAGE_ACCUM_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(raw, dict):
        return base
    out: Dict[str, int] = dict(base)
    for k in base:
        if k in raw:
            try:
                out[k] = max(0, int(raw[k]))
            except (TypeError, ValueError):
                pass
    return out


def _save_usage_accumulator(data: Dict[str, Any]) -> None:
    clean = _default_usage_accum_dict()
    for k in clean:
        if k in data:
            try:
                clean[k] = max(0, int(data[k]))
            except (TypeError, ValueError):
                pass
    USAGE_ACCUM_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


from contextlib import asynccontextmanager

@asynccontextmanager
async def _agent_lifespan(app):
    """捕获 uvicorn 关闭时的 CancelledError，避免打印无害的 Traceback"""
    try:
        yield
    except asyncio.CancelledError:
        pass  # uvicorn 关闭时正常行为，不需要 traceback


app = FastAPI(title="Code Web agent", lifespan=_agent_lifespan)



class UsageAccumIn(BaseModel):
    session_token_used: int = Field(default=0, ge=0)
    total_prompt_tokens: int = Field(default=0, ge=0)
    total_completion_tokens: int = Field(default=0, ge=0)
    total_cache_hit_tokens: int = Field(default=0, ge=0)
    total_cache_miss_tokens: int = Field(default=0, ge=0)


class ChatIn(BaseModel):
    message: str = Field(..., description="User message")
    conversation_id: Optional[str] = Field(default=None, description="Thread id; omit to start new")
    mode: Optional[str] = Field(default=None, description="Mode override: auto/plan/execute")
    model: Optional[str] = Field(default=None, description="Session model id (must match ALLOWED_MODELS / CHAT_API_MODELS)")


class ChatUserConfirmIn(BaseModel):
    conversation_id: str = Field(..., description="Thread id (matches session / cid)")
    confirm: str = Field(..., description="User-selected or typed confirmation text")
    mode: Optional[str] = Field(default=None, description="Mode override: auto/plan/execute")
    model: Optional[str] = Field(default=None, description="Session model id (must match ALLOWED_MODELS / CHAT_API_MODELS)")


class ChatStopIn(BaseModel):
    conversation_id: str = Field(..., description="Thread id to stop")
    run_id: Optional[str] = Field(default=None, description="Active run id to stop")


class ChatTitleIn(BaseModel):
    conversation_id: str = Field(..., description="Thread id to title")


class ChatUiStateIn(BaseModel):
    active_conversation_id: Optional[str] = Field(default=None)
    tabs: List[Dict[str, Any]] = Field(default_factory=list)


def _conversation_sse_event(cid: str, ev: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(ev or {})
    out.setdefault("conversation_id", cid)
    return out


def _apply_conversation_request_options(cid: str, mode: str, model: str) -> None:
    mod = str(model or "").strip()
    if mod:
        okm, _m = set_conversation_model(cid, mod)
        if not okm:
            raise HTTPException(400, "invalid model")
    m = str(mode or "").strip().lower()
    if m == "auto":
        CONVERSATION_MODES.pop(cid, None)
    elif m in ("plan", "execute"):
        CONVERSATION_MODES[cid] = m


@app.post("/api/chat/user-confirm/stream")
def chat_user_confirm_stream(inp: ChatUserConfirmIn, request: Request):
    cid = inp.conversation_id.strip()
    conf = inp.confirm.strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    pending = PENDING_USER_CONFIRM.get(cid)
    if not pending:
        raise HTTPException(400, "no pending user confirmation for this conversation")
    tool_call_id = str(pending.get("tool_call_id") or "")
    exec_args0 = pending.get("exec_args")
    if not isinstance(exec_args0, dict):
        PENDING_USER_CONFIRM.pop(cid, None)
        raise HTTPException(500, "invalid pending user_confirm state")
    exec_args1 = _merge_confirm_into_user_confirm_args(exec_args0, conf)
    result = execute_tool_script("cli_user_confirm.py", exec_args1)
    if not (isinstance(result, dict) and result.get("ok") is True):
        _record_tool_debug_failure(
            conversation_id=cid,
            api_name="cli_user_confirm",
            script="cli_user_confirm.py",
            tool_call_id=tool_call_id,
            request=exec_args1,
            response=result,
            source="user_confirm_resume",
        )
    messages = list(CONVERSATIONS.get(cid, []))
    idx: Optional[int] = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "tool" and str(messages[i].get("tool_call_id") or "") == tool_call_id:
            idx = i
            break
    if idx is None:
        PENDING_USER_CONFIRM.pop(cid, None)
        raise HTTPException(400, "tool message not found for pending confirmation")
    messages[idx] = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": _truncate_tool_result(result),
    }
    PENDING_USER_CONFIRM.pop(cid, None)
    CONVERSATIONS[cid] = messages
    _save_conversation(cid, messages)
    mode = str(inp.mode or "").strip().lower()

    if mode not in {"", "auto", "plan", "execute"}:
        raise HTTPException(400, "invalid mode")
    mod = str(inp.model or "").strip()
    if mod:
        okm, _m = set_conversation_model(cid, mod)
        if not okm:
            raise HTTPException(400, "invalid model")
    if mode == "auto":
        CONVERSATION_MODES.pop(cid, None)
    elif mode in ("plan", "execute"):
        CONVERSATION_MODES[cid] = mode
    client_ip = ""
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        client_ip = xff.split(",")[0].strip()
    if not client_ip:
        client_ip = (request.headers.get("x-real-ip") or "").strip()
    if not client_ip and request.client is not None:
        client_ip = str(request.client.host or "").strip()
    client_ip = _normalize_client_ip_for_tools(client_ip)

    def gen():
        _run_id = ""
        try:
            _run_id = _begin_conversation_run(cid) or ""
            if not _run_id:
                _busy_ev = {"type": "error", "conversation_id": cid, "where": "server", "detail": "当前会话仍在执行中，请等待完成或先停止。"}
                yield f"data: {json.dumps(_busy_ev, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'run_started', 'conversation_id': cid, 'run_id': _run_id}, ensure_ascii=False)}\n\n"
            try:
                _tpd = {
                    "type": "tool_preview_update",
                    "conversation_id": cid,
                    "tool_call_id": tool_call_id,
                    "preview": preview_tool_result("cli_user_confirm.py", result),
                }
                yield f"data: {json.dumps(_tpd, ensure_ascii=False)}\n\n"
            finally:
                pass
            _ensure_conversation_loaded(cid)
            _apply_conversation_request_options(cid, mode, mod)
            for ev in run_agent_turn(cid, "", client_ip=client_ip, mode_hint=mode, resume_after_user_confirm=True, run_id=_run_id):
                ev2 = _conversation_sse_event(cid, ev)
                _log_agent_console_sse(cid, ev2)
                yield f"data: {json.dumps(ev2, ensure_ascii=False)}\n\n"
        except Exception as _exc:

            _err_ev = {"type": "error", "conversation_id": cid, "where": "server", "detail": str(_exc)}
            yield f"data: {json.dumps(_err_ev, ensure_ascii=False)}\n\n"
        finally:
            if _run_id:
                _end_conversation_run(cid, _run_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )



@app.get("/")
def index():
    return HTMLResponse(INLINE_UI_HTML, media_type="text/html; charset=utf-8")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/api/model-pricing")
def model_pricing(conversation_id: str = "", model: str = ""):
    return get_model_pricing_snapshot(conversation_id, model)


@app.get("/api/usage-accumulator")
def usage_accumulator_get():
    return _load_usage_accumulator()


@app.put("/api/usage-accumulator")
def usage_accumulator_put(body: UsageAccumIn):
    _save_usage_accumulator(body.model_dump())
    return {"ok": True}


@app.get("/api/chat/history")
def chat_history(conversation_id: str = ""):
    cid = str(conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    _ensure_conversation_loaded(cid)
    messages = list(CONVERSATIONS.get(cid, []))
    return {"ok": True, "conversation_id": cid, "items": _chat_history_from_messages(messages)}


@app.get("/api/chat/sessions")
def chat_sessions():
    state = _load_last_open_session_state()
    title_by_id: Dict[str, str] = {}
    for t in state.get("tabs") or []:
        if isinstance(t, dict):
            cid = str(t.get("id") or "")
            title = str(t.get("title") or "").strip()
            if cid and title:
                title_by_id[cid] = title
    rows: List[Dict[str, Any]] = []
    try:
        files = list(SESSION_DIR.glob("*.json")) + list(SESSION_DIR.glob("*/*.json"))
        files = sorted([p for p in files if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        files = []
    for fp in files[:100]:
        cid = fp.stem
        if not re.match(r"^[A-Za-z0-9._:-]{8,128}$", cid):
            continue
        messages = CONVERSATIONS.get(cid)
        if messages is None:
            messages = _load_conversation(cid) or []
        # 标题优先级：session文件内 > last_open_session_state缓存 > 消息提取
        title = _load_conversation_title(cid) or title_by_id.get(cid) or _fallback_title_from_messages(cid, list(messages))
        try:
            updated_at = int(fp.stat().st_mtime * 1000)
        except Exception:
            updated_at = 0
        rows.append({"id": cid, "title": title[:80], "updated_at": updated_at, "date_group": _session_date_group_from_path(fp)})
    rows.sort(key=lambda r: (r.get("date_group") or "", r.get("updated_at", 0)), reverse=True)
    return {"ok": True, "sessions": rows}


@app.post("/api/chat/title")
def chat_title(body: ChatTitleIn):
    cid = str(body.conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    _ensure_conversation_loaded(cid)
    messages = list(CONVERSATIONS.get(cid, []))
    title = _generate_conversation_title(cid, messages)
    return {"ok": True, "conversation_id": cid, "title": title}


@app.get("/api/chat/ui-state")
def chat_ui_state_get():
    state = _load_last_open_session_state()
    return {"ok": True, "state": state}


@app.put("/api/chat/ui-state")
def chat_ui_state_put(body: ChatUiStateIn):
    tabs: List[Dict[str, str]] = []
    for t in body.tabs or []:
        cid = str(t.get("id") or "").strip()
        if not re.match(r"^[A-Za-z0-9._:-]{8,128}$", cid):
            continue
        title = str(t.get("title") or "").strip()
        tabs.append({"id": cid, "title": title[:80]})
    tabs = tabs[-UI_RESTORE_MAX_TABS:]
    active = str(body.active_conversation_id or "").strip()
    if not re.match(r"^[A-Za-z0-9._:-]{8,128}$", active):
        active = tabs[0]["id"] if tabs else ""
    elif tabs and not any(t["id"] == active for t in tabs):
        tabs = tabs[1:] + [{"id": active, "title": f"会话 {active[:8]}"}] if len(tabs) >= UI_RESTORE_MAX_TABS else tabs + [{"id": active, "title": f"会话 {active[:8]}"}]
    state = {"active_conversation_id": active, "tabs": tabs, "updated_at": int(time.time() * 1000)}
    _save_last_open_session_state(state)
    return {"ok": True}


# ── 知识库 API ──


@app.get("/api/reasoning-effort")
async def reasoning_effort_get(request: Request):
    """查询当前会话或全局的 reasoning_effort 值"""
    cid = str(request.query_params.get("conversation_id") or "").strip()
    return {"ok": True, "reasoning_effort": _get_reasoning_effort(cid), "global_default": _get_reasoning_effort()}


@app.put("/api/reasoning-effort")
async def reasoning_effort_set(request: Request):
    """设置会话级 reasoning_effort"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    cid = str(body.get("cid") or body.get("conversation_id") or "").strip()
    effort = str(body.get("effort") or "").strip().lower()
    if not cid:
        return {"ok": False, "error": "conversation_id required"}
    ok = _set_reasoning_effort(cid, effort)
    return {"ok": ok, "reasoning_effort": _get_reasoning_effort(cid)}


@app.get("/api/kb/files")
def kb_files():
    """列出知识库目录下所有可读文件（目录不存在时自动创建）"""
    if not KB_BASE_DIR:
        return {"ok": True, "enabled": False, "files": []}
    if not KB_BASE_DIR.is_dir():
        try:
            KB_BASE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            return {"ok": True, "enabled": False, "files": []}
    result = []
    for p in sorted(KB_BASE_DIR.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in {".md", ".txt", ".py", ".js", ".json", ".yaml", ".yml",
                        ".css", ".html", ".xml", ".toml", ".ini", ".cfg", ".conf",
                        ".sh", ".bat", ".ps1", ".sql", ".r", ".rs", ".go", ".java",
                        ".ts", ".jsx", ".tsx", ".vue", ".svelte", ".rb", ".php",
                        ".pl", ".lua", ".zig", ".swift", ".kt", ".gradle", ".env",
                        ".gitignore", ".dockerignore", ".editorconfig", ".mdx",
                        ".xlsx", ".xls", ".csv"}:
            continue
        if p.name.startswith(".") or "__pycache__" in p.parts:
            continue
        rel = str(p.relative_to(KB_BASE_DIR)).replace("\\", "/")
        result.append({"path": rel, "name": p.name, "mtime": p.stat().st_mtime})
    return {"ok": True, "enabled": True, "files": result}


@app.get("/api/kb/checked")
def kb_checked_get(conversation_id: str = ""):
    cid = str(conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    with _KB_CHECKED_LOCK:
        state = _KB_CHECKED_STATE.get(cid, set())
    return {"ok": True, "checked": sorted(state)}


class KbCheckedIn(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    checked: List[str] = Field(default_factory=list, description="勾选的文件相对路径列表")


@app.put("/api/kb/checked")
def kb_checked_put(body: KbCheckedIn):
    cid = str(body.conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    paths = set(body.checked or [])
    with _KB_CHECKED_LOCK:
        if paths:
            _KB_CHECKED_STATE[cid] = paths
        else:
            _KB_CHECKED_STATE.pop(cid, None)
        _kb_persist_checked()
    return {"ok": True}


@app.post("/api/chat/stop")
def chat_stop(inp: ChatStopIn):
    cid = str(inp.conversation_id or "").strip()
    if not cid:
        raise HTTPException(400, "empty conversation_id")
    stopped = _request_conversation_stop(cid, str(inp.run_id or "").strip())
    return {"ok": True, "conversation_id": cid, "stopped": stopped}


@app.post("/api/chat/stream")
def chat_stream(inp: ChatIn, request: Request):
    text = inp.message.strip()
    if not text:
        raise HTTPException(400, "empty message")
    if AT_MESSAGE_FILE_PREFETCH:
        # @文件引用解析：自动读取 @路径 引用的文件并注入到消息中
        import re as _re, os as _os
        _at_files = _re.findall(r'@((?:[A-Za-z]:[\\/][^\s]+|"[^"]+"))', text)
        _injected = []
        for _fp in _at_files:
            _fp = _fp.strip('"')
            try:
                if _os.path.isfile(_fp):
                    with open(_fp, 'r', encoding='utf-8', errors='replace') as _f:
                        _fc = _f.read()
                    if len(_fc) > 500_000:
                        _fc = _fc[:500_000] + "\n...(文件过大，已截断)"
                    _injected.append(f"\n\n【引用的文件 @{_fp} 内容如下】\n{_fc}\n【文件结束】")
            except Exception as _e:
                _injected.append(f"\n\n【警告：无法读取 @{_fp}：{_e}】")
        if _injected:
            text = "".join(_injected) + "\n\n---\n用户消息：" + text
    cid = str(inp.conversation_id or "").strip() or str(uuid.uuid4())
    mode = str(inp.mode or "").strip().lower()

    if mode not in {"", "auto", "plan", "execute"}:
        raise HTTPException(400, "invalid mode")

    mod = str(inp.model or "").strip()
    if mod:
        okm, _m = set_conversation_model(cid, mod)
        if not okm:
            raise HTTPException(400, "invalid model")
    if mode == "auto":
        CONVERSATION_MODES.pop(cid, None)
    elif mode in ("plan", "execute"):
        CONVERSATION_MODES[cid] = mode

    # 从本地文件恢复会话
    if cid not in CONVERSATIONS or not CONVERSATIONS.get(cid):
        loaded = _load_conversation(cid)
        if loaded:
            CONVERSATIONS[cid] = loaded

    client_ip = ""
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        client_ip = xff.split(",")[0].strip()
    if not client_ip:
        client_ip = (request.headers.get("x-real-ip") or "").strip()
    if not client_ip and request.client is not None:
        client_ip = str(request.client.host or "").strip()
    client_ip = _normalize_client_ip_for_tools(client_ip)

    def gen():
        _run_id = ""
        try:
            _run_id = _begin_conversation_run(cid) or ""
            if not _run_id:
                _busy_ev = {"type": "error", "conversation_id": cid, "where": "server", "detail": "当前会话仍在执行中，请等待完成或先停止。"}
                yield f"data: {json.dumps(_busy_ev, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'run_started', 'conversation_id': cid, 'run_id': _run_id}, ensure_ascii=False)}\n\n"
            if not _chat_api_key_available():
                _err_ev = {"type": "error", "conversation_id": cid, "where": "config", "detail": "请先在 config.json 中配置 AGENT_MODEL_API_KEY"}
                yield f"data: {json.dumps(_err_ev, ensure_ascii=False)}\n\n"
                return
            _ensure_conversation_loaded(cid)
            _apply_conversation_request_options(cid, mode, mod)
            for ev in run_agent_turn(cid, text, client_ip=client_ip, mode_hint=mode, run_id=_run_id):
                ev2 = _conversation_sse_event(cid, ev)
                _log_agent_console_sse(cid, ev2)
                yield f"data: {json.dumps(ev2, ensure_ascii=False)}\n\n"
        except Exception as _exc:

            _err_ev = {"type": "error", "conversation_id": cid, "where": "server", "detail": str(_exc)}
            yield f"data: {json.dumps(_err_ev, ensure_ascii=False)}\n\n"
        finally:
            if _run_id:
                _end_conversation_run(cid, _run_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/dir-browse")
def dir_browse(path: str = ""):
    """浏览目录结构，供前端 @ 文件选择器使用。
    默认路径：WORKSPACE_DIR → 桌面 → HOME。
    支持 Windows 盘符列表（path=_drives_）。
    """
    import os as _os
    import string as _string

    _workspace = os.environ.get("WORKSPACE_DIR", "").strip()
    if _workspace:
        _default = Path(_workspace).resolve()
    else:
        _default = Path.home() / "Desktop"
    if not _default.is_dir():
        _default = Path.home()

    # ── 盘符列表模式（仅 Windows）──
    if str(path).strip() == "_drives_":
        items = []
        for _letter in _string.ascii_uppercase:
            _dp = f"{_letter}:\\"
            if _os.path.exists(_dp):
                _is_ready = _os.path.isdir(_dp)
                # 读取卷标
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
                items.append({
                    "name": _name,
                    "type": "dir",
                    "ext": "",
                    "path": f"{_letter}:/" if _is_ready else _dp,
                })
        return {"current": "计算机", "parent": "", "items": items}

    if not path or not str(path).strip():
        target = _default
    else:
        raw = Path(str(path).strip()).expanduser()
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
        items.append({
            "name": e.name,
            "type": "dir" if e.is_dir() else "file",
            "ext": ext,
            "path": _os.path.abspath(e.path).replace("\\", "/"),
        })
    parent_p = target.parent.resolve()
    # 盘符根目录（如 C:/）→ 父路径指向盘符列表
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

@app.get("/health")
def health():
    return {"ok": True, "catalog": str(TOOL_LIST_JSON), "model": default_model_from_env(), "allowed_models": list(ALLOWED_MODELS)}


def main():
    # 加载配置（读取 config.json，设置环境变量 PORT）
    from util.config_loader import load_config
    load_config(verbose=True)
    port_str = os.environ.get("PORT")
    if not port_str:
        print("FATAL: PORT 未设置！请在 config.json 中配置 AGENT_SERVER_PORT", flush=True)
        sys.exit(1)
    
    # ── API Key 检查 ──
    if not _chat_api_key_available():
        print("⚠️  WARNING: AGENT_MODEL_API_KEY 未配置或为空！请在 config.json 中设置 AGENT_MODEL_API_KEY", file=sys.stderr, flush=True)
        print("⚠️  或通过环境变量 CHAT_API_KEY 设置", file=sys.stderr, flush=True)

    uvicorn.run(app, host="127.0.0.1", port=int(port_str))


if __name__ == "__main__":
    main()
