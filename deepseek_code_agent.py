#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek Code Agent: OpenAI-compatible Chat Completions + 工具库（tool_list_agent.json）。

Configure via config.ini [model] api_key / api_base_url, or env CHAT_API_KEY / CHAT_API_BASE_URL.
Typical body: messages, tools, stream with SSE data: lines — see provider docs for JSON mode, tools, and errors.
- 本仓库工具库与 Agent 落盘文本（JSON 等）默认 UTF-8。
- 工具调用失败时（ok 非 true）可向 DATA_ROOT 下 debug 目录写入 JSON 记录；AGENT_TOOL_DEBUG=0 关闭。
- 工具返回 ok=false 时 error **必定**含 tool_help：合并 argparse 等效 `--help` 与 tool_list_agent.json 摘要（未知工具亦有兜底说明），供模型纠正调用；**仅**通过各脚本 `agent_main` 进程内执行（原生 Python 类型），不向 `main()`/模拟 argv 降级。
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

# file_search 黑名单：禁止脱离服务端直接调用，必须走线程+SSE 进度推送路径
_FILE_SEARCH_ALLOWED: bool = False
_RESTRICTED_TOOLS: frozenset = frozenset({"file_search.py"})
# 走线程 + _progress_dict，宿主轮询并推送 tool_progress（与 file_search 一致）
_TOOL_PROGRESS_SCRIPTS: frozenset = frozenset({"file_search.py", "grep_files.py", "regex_locate.py"})
# 写盘/写类工具：宿主在 dry_run 预览成功时统一注入 host_dry_run_notice（Plan 模式跳过）
WRITE_TOOL_SCRIPTS: frozenset = frozenset(
    {
        "file_ops.py",
        "python_inline.py",
        "write_file.py",
        "read_write.py",
        "replace_in_file.py",
        "apply_patch.py",
        "run_command.py",
        "delete_file.py",
    }
)
_HOST_DRY_RUN_NOTICE_ZH = (
    "【宿主提示】本次为预览（dry_run=true），磁盘未被修改。"
    "确认写入请传 dry_run: false（命令行对应 --commit），并满足当前会话模式（如 Execute）与执行清单等要求。"
)
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
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

# PyInstaller 打包后 __file__ 指向 sys._MEIPASS，源码模式正常
if getattr(sys, 'frozen', False):
    _base = Path(sys._MEIPASS)
else:
    _base = Path(__file__).resolve().parent
AGENT_ROOT = _base


def _strip_config_path_value(value: object) -> str:
    """Trim and strip a single pair of surrounding quotes from config/env path strings."""
    raw = str(value or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        raw = raw[1:-1].strip()
    return raw


# 可写运行时数据目录，从配置读取
_dr = _strip_config_path_value(AGENT_CONFIG.get("AGENT_DATA_ROOT_DIR"))
if not _dr:
    print("FATAL: AGENT_DATA_ROOT_DIR 未设置！请在 config.ini 的 [workspace] 节配置 data_root", flush=True)
    sys.exit(1)
DATA_ROOT = Path(_dr).expanduser().resolve()

# ── 知识库配置 ──
_KB_DIR_STR = _strip_config_path_value(AGENT_CONFIG.get("AGENT_KNOWLEDGE_BASE_DIR"))
KB_BASE_DIR: Optional[Path] = Path(_KB_DIR_STR).expanduser().resolve() if _KB_DIR_STR else None
_KB_CHECKED_STATE: Dict[str, Set[str]] = {}
_KB_CHECKED_LOCK = threading.Lock()


def _kb_checked_state_file() -> Optional[Path]:
    """知识库勾选状态：放在知识库根目录的上一级，避免与资料文件同目录。"""
    if not KB_BASE_DIR:
        return None
    return KB_BASE_DIR.resolve().parent / ".checked_state.json"


def _kb_persist_checked():
    if not KB_BASE_DIR:
        return
    state_file = _kb_checked_state_file()
    if not state_file:
        return
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        ser = {k: sorted(v) for k, v in _KB_CHECKED_STATE.items()}
        state_file.write_text(json.dumps(ser, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _kb_load_single_cid_checked(cid: str):
    """从磁盘文件恢复指定会话的勾选状态"""
    if not KB_BASE_DIR:
        return
    state_file = _kb_checked_state_file()
    if not state_file or not state_file.is_file():
        return
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if cid in data:
            _KB_CHECKED_STATE[cid] = set(data[cid])
    except Exception:
        pass


def _kb_load_checked():
    if not KB_BASE_DIR:
        return
    state_file = _kb_checked_state_file()
    if not state_file or not state_file.is_file():
        return
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        for cid, paths in data.items():
            if isinstance(paths, list):
                _KB_CHECKED_STATE[cid] = set(paths)
    except Exception:
        pass

# 启动时恢复勾选状态
_kb_load_checked()

TOOLS_DIR = AGENT_ROOT / "tools"
TOOL_LIST_JSON = TOOLS_DIR / "tool_list_agent.json"


def _resolve_tool_script_path(script_name: str) -> Optional[Path]:
    """工具脚本位于 tools/。"""
    if not script_name:
        return None
    p = TOOLS_DIR / script_name
    if p.is_file():
        return p
    return None


def _ensure_tools_sys_path() -> None:
    """将 tools/ 置于 sys.path 以便 importlib 加载各工具模块。"""
    sd = str(TOOLS_DIR)
    try:
        if TOOLS_DIR.is_dir() and sd not in sys.path:
            sys.path.insert(0, sd)
    except Exception:
        pass


_ensure_tools_sys_path()
import delete_file as _delete_file_mod
import todo_list as _todo_list_mod

_todo_list_mod.configure_storage(DATA_ROOT / "cache" / "todo_lists")
_delete_file_mod.configure_trash_root(DATA_ROOT / "safe_delete")


def _execute_todo_list(conversation_id: str, exec_args: Dict[str, Any]) -> dict:
    return _todo_list_mod.execute(conversation_id, exec_args)


USAGE_ACCUM_FILE = DATA_ROOT / "model_usage_accumulator.json"
SESSION_DIR = DATA_ROOT / "cache" / "sessions"
EXCERPTS_DIR = DATA_ROOT / "cache" / "excerpts"
TOOL_DEBUG_DIR = DATA_ROOT / "debug"
LAST_OPEN_SESSION_STATE_FILE = DATA_ROOT / "cache" / "last_open_session_state.json"
_skf = str(AGENT_CONFIG.get("AGENT_SESSION_KEY_FILE") or "").strip()
SESSION_KEY_FILE = Path(_skf).expanduser().resolve() if _skf else (DATA_ROOT / "cache" / "session_encryption.key")
SESSION_ENCRYPTION_MAGIC = "__code_web_agent_session_encrypted__"
SESSION_APP_ENTROPY = hashlib.sha256((str(AGENT_ROOT) + "|code-web-agent-session-v1").encode("utf-8")).digest()


# ── Agent 运行参数：从 AGENT_CONFIG 读取（无默认值，缺失报错）──
_CONTEXT_CFG = AGENT_CONFIG
# full：保留末尾 full_user_rounds 个 user 回合为近期完整对话（含工具）；pure：紧挨其前 pure_user_rounds 个 user 回合为远期纯对话（折叠）。
CONTEXT_FULL_USER_ROUNDS = int(_CONTEXT_CFG["AGENT_CONTEXT_FULL_USER_ROUNDS"])
CONTEXT_PURE_USER_ROUNDS = int(_CONTEXT_CFG["AGENT_CONTEXT_PURE_USER_ROUNDS"])
_SUMMARY_THINK_RAW = str(_CONTEXT_CFG["AGENT_SUMMARY_THINKING"]).strip().lower()
SUMMARY_THINKING_ENABLED = _SUMMARY_THINK_RAW not in ("", "0", "false", "no", "off", "disabled")
# AGENT_CONTEXT_TOKEN_METHOD：config.ini 预留，当前未参与分支（仅 estimate）
TOKEN_ESTIMATE_EN_PER_CHAR = float(_CONTEXT_CFG["AGENT_TOKEN_ESTIMATE_EN_PER_CHAR"])
TOKEN_ESTIMATE_ZH_PER_CHAR = float(_CONTEXT_CFG["AGENT_TOKEN_ESTIMATE_ZH_PER_CHAR"])
# 上下文比例条：与「已用 token 估算」对比的总预算（用于末尾剩余容量条）
CONTEXT_LAYOUT_BUDGET_TOKENS = int(_CONTEXT_CFG["AGENT_CONTEXT_LAYOUT_BUDGET_TOKENS"])
CONTEXT_SUMMARY_TOKEN_THRESHOLD = int(_CONTEXT_CFG["AGENT_CONTEXT_SUMMARY_TOKEN_THRESHOLD"])
SUMMARY_IN_PROGRESS_TTL_SEC = float(_CONTEXT_CFG["AGENT_SUMMARY_IN_PROGRESS_TTL_SEC"])
MAX_TOOL_ROUNDS = int(_CONTEXT_CFG["AGENT_MAX_TOOL_ROUNDS"])
UI_RESTORE_MAX_TABS = int(_CONTEXT_CFG["AGENT_UI_RESTORE_MAX_TABS"])
UI_RESTORE_MAX_CHAT_ITEMS = int(_CONTEXT_CFG["AGENT_UI_RESTORE_MAX_CHAT_ITEMS"])
_PREVIEW_RAW = _CONTEXT_CFG["AGENT_PREVIEW_INTENT_KEYS"]
PREVIEW_INTENT_KEYS = tuple(_PREVIEW_RAW) if isinstance(_PREVIEW_RAW, (list, tuple)) else tuple(_PREVIEW_RAW)

# @路径：是否在进模型前由服务端预读并注入全文。False=由模型按需用工具读取；True=恢复预注入。
AT_MESSAGE_FILE_PREFETCH = bool(_CONTEXT_CFG["AGENT_AT_MESSAGE_FILE_PREFETCH"])

# ---------- 控制台调试：stderr 输出 JSON 行（SSE 事件、工具完整入参/出参）。AGENT_CONSOLE_LOG=0 关闭 ----------
def _agent_console_log_enabled() -> bool:
    v = str(AGENT_CONFIG.get("AGENT_CONSOLE_LOG") or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


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
    v = str(AGENT_CONFIG.get("AGENT_TOOL_DEBUG") or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


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


def _peek_conversation_stop_requested(cid: str, run_id: str = "") -> bool:
    """是否已有停止请求（不消费标志，供工具执行循环内轮询）。"""
    key = str(cid or "")
    with _CONVERSATION_STOP_LOCK:
        active = _ACTIVE_CONVERSATION_RUNS.get(key)
        eff = str(run_id or active or "")
        flags = _CONVERSATION_STOP_FLAGS.get(key)
        if not flags or not eff:
            return False
        return eff in flags


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
    script_path = _resolve_tool_script_path(script_name)
    if not script_path or not script_path.is_file():
        return ""
    _ensure_tools_sys_path()
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


def _intent_tool_hints(key_lower: str, names: list[str]) -> list[str]:
    """未知工具名时按常见臆造后缀给出可读推荐，避免 closest-match 跑偏到无关工具。"""
    hit: list[str] = []
    if any(
        s in key_lower
        for s in (
            "directory",
            "dir_list",
            "list_dir",
            "folder",
            "cli_directory",
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
            "cli_find",
        )
    ):
        for c in ("replace_in_file", "read_write", "apply_patch", "write_file"):
            if c in names:
                hit.append(c)
    seen: set[str] = set()
    out: list[str] = []
    for x in hit:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[:6]


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
            h = _subprocess_cli_help(scr) if scr else ""
            if h:
                lines.extend(["", f"与 {key!r} 最接近的已注册名称：{c!r}（脚本 {scr}），该脚本 --help：", h])
                break
            if scr:
                lines.extend(["", f"与 {key!r} 最接近的已注册名称：{c!r}（脚本 {scr}）；未能捕获 --help 文本"])
                break
    msg = "\n".join(lines)
    return {"ok": False, "data": None, "error": {"type": "UnknownTool", "message": msg}}


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



def _safe_json_loads(s: str) -> Optional[dict]:
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except Exception:
        return None


def _reasoning_delta_field_names() -> List[str]:
    raw = str(AGENT_CONFIG.get("AGENT_REASONING_DELTA_FIELDS") or "").strip()
    names = [x.strip() for x in raw.replace(",", " ").split() if x.strip()]
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


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(data, out[:len(data)]))


def _encrypt_session_payload(plain: bytes) -> Dict[str, Any]:
    mode = str(AGENT_CONFIG["AGENT_SESSION_ENCRYPTION"]).strip().lower()
    if mode not in {"auto", "dpapi", "local", "none"}:
        mode = "none"
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
    if alg == "local-hmac-sha256-stream-v1":
        key = _session_fallback_key()
        nonce = base64.b64decode(str(raw.get("nonce") or "").encode("ascii"))
        cipher = base64.b64decode(str(raw.get("data") or "").encode("ascii"))
        tag = base64.b64decode(str(raw.get("tag") or "").encode("ascii"))
        expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("session encryption tag mismatch")
        return _xor_stream(cipher, key, nonce)
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


def _save_title_file(cid: str, title: str) -> None:
    """将标题写入独立 .title 文件，不加密"""
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
        tfile.write_text(title.strip()[:80], encoding="utf-8")
    except Exception as e:
        print(f"WARN: failed to save title file for {cid}: {e}", file=sys.stderr, flush=True)


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
            return raw
        return None
    except Exception:
        return None


# 磁盘上已有会话文件但无法解析/解密时，禁止继续对话（避免用空 messages 覆盖写盘导致历史丢失）
SESSION_PERSIST_UNREADABLE_SSE_DETAIL = "当前会话已停止，请重新发起会话。"


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
    """从独立 .title 文件读取标题，没有则返回空"""
    try:
        tfile = _find_title_file(cid)
        if tfile is not None and tfile.is_file():
            title = tfile.read_text(encoding="utf-8").strip()[:80]
            if title:
                return title
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


_CATALOG_TOOL_DESCRIPTION_MAX_CHARS = 24000


def _format_catalog_tool_examples(examples: Any) -> str:
    """将 tool_list_agent.json 中的 examples 并入工具 description / tool_help，便于模型对齐用法。"""
    if not isinstance(examples, list) or not examples:
        return ""
    blocks: List[str] = []
    for i, ex in enumerate(examples, 1):
        if isinstance(ex, str) and str(ex).strip():
            blocks.append(f"示例{i}：\n{str(ex).strip()}")
            continue
        if not isinstance(ex, dict):
            continue
        title = str(ex.get("title") or ex.get("name") or f"示例{i}").strip()
        note = ex.get("note") or ex.get("description")
        note_s = str(note).strip() if note is not None else ""
        args = ex.get("args")
        lines = [f"示例{i}：{title}"]
        if note_s:
            lines.append(note_s)
        if isinstance(args, dict) and args:
            try:
                dumped = json.dumps(args, ensure_ascii=False, indent=2)
            except TypeError:
                dumped = str(args)
            lines.append("建议 arguments（键名与 function 参数一致，布尔用小写 true/false）：")
            lines.append(dumped)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _catalog_tool_full_description(entry: dict, script_fn: str) -> str:
    base = str(entry.get("purpose") or script_fn).strip()
    ext = entry.get("extended_description")
    if isinstance(ext, str) and ext.strip():
        base = f"{base}\n\n{ext.strip()}"
    ex_text = _format_catalog_tool_examples(entry.get("examples"))
    if ex_text:
        base = f"{base}\n\n—— 调用示例 ——\n{ex_text}"
    if len(base) > _CATALOG_TOOL_DESCRIPTION_MAX_CHARS:
        base = base[: _CATALOG_TOOL_DESCRIPTION_MAX_CHARS - 2] + "\n…"
    return base


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
    """Return OpenAI-format tools + mapping api_name -> script filename (e.g. read_file.py)."""
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
            elif typ == "array":
                arr_items = arg.get("array_items", {"type": "string"})
                sch = {"type": "array", "description": desc, "items": arr_items}
            elif typ in ("object", "json-object", "json_object"):
                sch = {"type": "object", "description": desc}
            else:
                sch = {"type": "string", "description": desc}
            props[pname] = sch
            if arg.get("required"):
                required.append(pname)
        if "step_title" not in props:
            props["step_title"] = {
                "type": "string",
                "description": "可选。一句简短中文说明本次调用用途，将用作侧栏步骤主标题（建议≤40字）；省略则使用系统默认动作标题。",
            }
        tools.append({
            "type": "function",
            "function": {
                "name": api,
                "description": _catalog_tool_full_description(t, fn),
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return tools, name_map




def _openai_tools_sort_key(t: dict) -> Tuple[int, str]:
    """OpenAI tools list order: shell 类工具排后（隐式降低被选概率）。"""
    name = str((t.get("function") or {}).get("name") or "")
    deprioritize = 1 if name in ("run_command", "python_inline") else 0
    return (deprioritize, name)


def normalize_cli_args(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        key = str(k)
        if not key.startswith("--"):
            key = "--" + key
        out[key] = v
    return out



def _camel_to_snake(name: str) -> str:
    name = str(name or "").strip().lstrip("-").replace("-", "_")
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _agent_main_param_name(raw_key: str) -> str:
    key = str(raw_key or "").strip().lstrip("-")
    return _camel_to_snake(key)


def _strip_internal_tool_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if not str(k).startswith("_")}


def _execute_tool_agent_main(script_name: str, mod: Any, args: Dict[str, Any]) -> dict:
    """仅调用模块的 agent_main（不向 main()/CLI _stdout 降级）。"""
    import inspect

    fn = getattr(mod, "agent_main", None)
    if not callable(fn):
        return {
            "ok": False,
            "data": None,
            "error": {"type": "ToolError", "message": f"{script_name} 未定义可调用的 agent_main。"},
        }

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as e:
        return {
            "ok": False,
            "data": None,
            "error": {"type": "ToolError", "message": f"{script_name} agent_main 签名无效: {e}"},
        }

    params = sig.parameters
    kw_target_kinds = frozenset(
        {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    )
    accepted = {n for n, p in params.items() if p.kind in kw_target_kinds}
    varkw = next((n for n, p in params.items() if p.kind == inspect.Parameter.VAR_KEYWORD), None)

    arg_copy = dict(args)
    kwargs: Dict[str, Any] = {}
    unknown: list[str] = []

    for k, v in arg_copy.items():
        if v is None:
            continue
        pn = _agent_main_param_name(k)
        if pn == "json_out":
            continue
        # 宿主注入的 _progress_dict 必须传入 agent_main，否则 file_search/grep_files 等无法上报进度
        if pn.startswith("_"):
            if pn in accepted:
                kwargs[pn] = v
            continue
        if pn in accepted:
            kwargs[pn] = v
            continue

        if varkw is not None:
            kwargs[pn] = v
            continue

        unknown.append(f"{str(k)!r}(→{pn})")

    if unknown:
        allow_list = sorted(x for x in accepted if x != "parser_for_help")
        return {
            "ok": False,
            "data": None,
            "error": {
                "type": "BadToolArguments",
                "message": (
                    f"{script_name} agent_main 不识别下列参数（请对照 function schema / tools/tool_list_agent.json）："
                    f"{', '.join(sorted(set(unknown)))}。"
                    f"允许的形参名：{', '.join(allow_list)}。"
                ),
            },
        }

    if "parser_for_help" in accepted and "parser_for_help" not in kwargs:
        build_parser = getattr(mod, "build_parser", None)
        if callable(build_parser):
            try:
                kwargs["parser_for_help"] = build_parser()
            except Exception:
                pass

    allow_list_hint = sorted(x for x in accepted if x != "parser_for_help")
    try:
        result = fn(**kwargs)
    except TypeError as e:
        return {
            "ok": False,
            "data": None,
            "error": {
                "type": "TypeError",
                "message": (
                    f"{script_name} agent_main 参数不匹配或缺失必填项：{e}；"
                    f"本轮已解析关键字={sorted(kwargs.keys())}；"
                    f"形参清单={allow_list_hint}"
                ),
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "data": None,
            "error": {"type": "ToolError", "message": f"{script_name} agent_main 执行异常: {e}"},
        }

    if isinstance(result, dict):
        return _strip_internal_tool_result(result)
    return {
        "ok": False,
        "data": None,
        "error": {"type": "ToolError", "message": f"{script_name} agent_main 须返回 dict，实际：{type(result).__name__}"},
    }


_TOOL_HELP_MAX_CHARS = 20000

# 模型偶发把数组/对象序列化成字符串传入；在调用 agent_main 前解析为 Python 类型（agent_main 禁止依赖 JSON 字符串参数）。
_COERCE_JSON_CONTAINER_KEYS = frozenset({"rules", "items", "confirms", "indices"})


def _catalog_public_arg_names(script_name: str) -> set[str]:
    out: set[str] = {"step_title"}
    try:
        cat = load_catalog()
        for t in cat.get("tools", []):
            if str(t.get("name", "")).strip() != script_name:
                continue
            for a in t.get("args", []) or []:
                flag = str(a.get("flag", "")).strip()
                if not flag:
                    continue
                out.add(flag[2:] if flag.startswith("--") else flag)
            break
    except Exception:
        pass
    return out


def _validate_public_tool_args(script_name: str, args: Dict[str, Any]) -> Optional[dict]:
    public = _catalog_public_arg_names(script_name)
    if not public:
        return None
    bad: list[str] = []
    for k in args.keys():
        key = str(k).strip()
        if key.startswith("_"):
            continue
        bare = key[2:] if key.startswith("--") else key
        if bare not in public:
            bad.append(bare)
    if not bad:
        rules = args.get("rules") or args.get("--rules")
        if script_name == "replace_in_file.py" and isinstance(rules, list):
            nested_bad: list[str] = []
            for item in rules:
                if not isinstance(item, dict):
                    continue
                for nk in item.keys():
                    if str(nk) not in {"old_text", "new_text"}:
                        nested_bad.append(str(nk))
            if not nested_bad:
                return None
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "BadToolArguments",
                    "message": (
                        "replace_in_file.py 的 rules 只接受 old_text/new_text；"
                        f"不接受：{', '.join(sorted(set(nested_bad)))}。"
                    ),
                },
            }
        return None
    return {
        "ok": False,
        "data": None,
        "error": {
            "type": "BadToolArguments",
            "message": (
                f"{script_name} 不接受未公开参数：{', '.join(sorted(set(bad)))}。"
                f"请只使用 function schema / tools/tool_list_agent.json 中列出的参数。"
            ),
        },
    }


def _normalize_nested_tool_arg_keys(out: Dict[str, Any]) -> None:
    """规范嵌套对象参数：将 rules 数组内键名转为 snake_case（与 agent_main 一致）。"""
    rules = out.get("rules")
    if isinstance(rules, list):
        norm_rules: list[Any] = []
        for item in rules:
            if not isinstance(item, dict):
                norm_rules.append(item)
                continue
            norm_rules.append(
                {
                    _agent_main_param_name(k): v
                    for k, v in item.items()
                }
            )
        out["rules"] = norm_rules


def _coerce_tool_arguments_for_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    """将误传的 JSON 字符串解析为 list/dict，保证进程内 agent_main 收到 Python 原生类型。"""
    if not isinstance(args, dict):
        return {}
    out = dict(args)
    for k in _COERCE_JSON_CONTAINER_KEYS:
        v = out.get(k)
        if not isinstance(v, str):
            continue
        s = v.strip()
        if len(s) < 2 or s[0] not in "[{":
            continue
        try:
            parsed = json.loads(s)
        except Exception:
            continue
        out[k] = parsed
    _normalize_nested_tool_arg_keys(out)
    return out


def _capture_tool_help_from_module(mod: Any) -> Optional[str]:
    bp = getattr(mod, "build_parser", None)
    if not callable(bp):
        return None
    try:
        import tool_help_share as _ths

        return _ths.capture_help(bp())
    except Exception:
        return None


def _capture_tool_help_from_catalog(script_name: str) -> Optional[str]:
    try:
        cat = load_catalog()
        for t in cat.get("tools", []):
            if str(t.get("name", "")).strip() == script_name:
                lines = [str(t.get("purpose", "")), "", "参数摘要:"]
                for a in t.get("args", []) or []:
                    lines.append(f'  {a.get("flag", "")} — {a.get("description", "")}')
                ex_text = _format_catalog_tool_examples(t.get("examples"))
                if ex_text:
                    lines.extend(["", "调用示例:", ex_text])
                return "\n".join(lines)
    except Exception:
        pass
    return None


def attach_tool_help_on_failure(script_name: str, mod: Optional[Any], result: dict) -> dict:
    """工具返回 ok=false 时**必须**附带 tool_help：合并工具自身说明、argparse --help、tool_list_agent.json（与命令行 --help 等效）。"""
    if not isinstance(result, dict) or result.get("ok"):
        return result
    err = result.get("error")
    if not isinstance(err, dict):
        return result
    blocks: List[str] = []
    prior = err.get("tool_help")
    if isinstance(prior, str) and prior.strip():
        blocks.append("【工具返回的说明】\n" + prior.strip())
    h_cli = _capture_tool_help_from_module(mod) if mod is not None else None
    if h_cli:
        blocks.append("【--help（argparse 完整用法）】\n" + h_cli.strip())
    h_cat = _capture_tool_help_from_catalog(script_name)
    if h_cat:
        blocks.append("【工具清单（tool_list_agent.json）】\n" + h_cat.strip())
    if not h_cli and not h_cat:
        blocks.append(
            f"【--help】\n未找到 {script_name} 的 argparse 帮助与目录条目；请核对脚本名、WORKSPACE_DIR 与 tools/tool_list_agent.json 是否一致。"
        )
    merged = "\n\n".join(blocks)
    if len(merged) > _TOOL_HELP_MAX_CHARS:
        merged = merged[:_TOOL_HELP_MAX_CHARS] + "\n…"
    return {**result, "error": {**err, "tool_help": merged}}


def maybe_attach_write_tool_host_dry_run_notice(
    script_name: str,
    result: Any,
    conversation_mode: str,
) -> Any:
    """写类工具成功返回且 data.dry_run 为预览时，向 data 写入 host_dry_run_notice；Plan 模式不加。"""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    data = result.get("data")
    if not isinstance(data, dict):
        return result
    if str(conversation_mode or "").strip().lower() == "plan":
        return result
    sn = script_name or ""
    if sn not in WRITE_TOOL_SCRIPTS:
        return result
    dr = data.get("dry_run")
    if dr is not True and dr != 1:
        return result
    if data.get("host_dry_run_notice"):
        return result
    out = dict(result)
    out_data = dict(data)
    out_data["host_dry_run_notice"] = _HOST_DRY_RUN_NOTICE_ZH
    out["data"] = out_data
    return out


def execute_tool_script(script_name: str, args: Dict[str, Any]) -> dict:
    """统一进程内执行工具（源码运行 / PyInstaller 打包后均走此路）"""
    # 黑名单工具拒绝脱离服务端直接调用
    if script_name in _RESTRICTED_TOOLS and not _FILE_SEARCH_ALLOWED:
        return attach_tool_help_on_failure(
            script_name,
            None,
            {"ok": False, "data": None, "error": {"type": "Restricted", "message": "file_search 禁止直接调用，请通过对话界面使用（支持实时进度展示）"}},
        )
    with _TOOL_EXEC_LOCK:
        return _execute_tool_script_locked(script_name, args)


# ── kling_generate 生成类 action 列表（用于确认 ID 拦截） ──
_KLING_GENERATE_ACTIONS = {
    "text2video", "image2video", "multimodal2video", "multi_image2video",
    "motion_control", "video_extend", "lip_sync", "avatar",
    "text2image", "image2image", "multi_image2image", "omni_image",
    "virtual_try_on", "text2audio",
}

# 从 agent_v2.live_state 导入确认 ID 系统
try:
    from agent_v2.live_state import kling_create_confirm_id, kling_consume_confirm_id
except ImportError:
    kling_create_confirm_id = None
    kling_consume_confirm_id = None


def _kling_estimate_cost(action: str, args: dict) -> str:
    duration = str(args.get("duration", "5"))
    mode = str(args.get("mode", "std"))
    num_images = int(args.get("num_images", 1))
    if action in ("text2image", "image2image", "omni_image"):
        return "约 " + str(0.1 * num_images) + " 元"
    if action == "text2audio":
        return "按音频时长计费"
    if action in ("virtual_try_on",):
        return "按次计费"
    try:
        d = int(duration or 5)
    except ValueError:
        d = 5
    rate = {"std": 0.6, "pro": 0.8, "4k": 3.0}.get(mode, 0.6)
    return "约 " + str(rate * d) + " 元"


def _execute_tool_script_locked(script_name: str, args: Dict[str, Any]) -> dict:
    """execute_tool_script 的加锁实现；仅调用 agent_main，不劫持 sys.argv/stdout。"""
    import importlib

    script_path = _resolve_tool_script_path(script_name)
    if script_path is None:
        hint = [str(TOOLS_DIR / script_name)]
        try:
            c1 = [p.name for p in TOOLS_DIR.glob("*.py")]
            cands = sorted(set(c1))[:50]
            hint.append("\n\n工具脚本（节选）：" + ", ".join(cands))
        except Exception:
            pass
        return attach_tool_help_on_failure(
            script_name,
            None,
            {"ok": False, "data": None, "error": {"type": "ToolNotFound", "message": "\n".join(hint)}},
        )

    _ensure_tools_sys_path()

    # ── kling_generate 确认 ID 拦截（放在参数校验之前，confirm_id 不在公开参数列表中） ──
    if script_name == "kling_generate.py":
        action = str(args.get("action", "") or "")
        if action in _KLING_GENERATE_ACTIONS:
            raw_cid = args.get("confirm_id")
            has_cid = raw_cid is not None and str(raw_cid).strip() != ""
            if has_cid:
                confirm_id = str(raw_cid).strip()
                info = kling_consume_confirm_id(confirm_id)
                if info is None:
                    try:
                        from agent_v2.live_state import kling_mark_confirmed
                        kling_mark_confirmed(confirm_id)
                    except Exception:
                        pass
                    info = kling_consume_confirm_id(confirm_id)
                if info and info.get("action") == action:
                    args.pop("confirm_id", None)  # 消耗后移除，避免传给 agent_main
                    pass  # 拦截消耗通过，继续到正常流程
                else:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "type": "E_INVALID_CONFIRM_ID",
                            "code": "E_INVALID_CONFIRM_ID",
                            "message": "确认ID无效或与请求的操作不匹配。请先通过 kling_generate 获取确认ID并完成确认。"
                        },
                    }
            else:
                import json as _json
                new_id = kling_create_confirm_id(action, dict(args))
                cost_info = _kling_estimate_cost(action, args)
                action_cn = {"text2video":"文生视频","image2video":"图生视频","text2image":"文生图","image2image":"图生图"}.get(action, action)
                return {
                    "ok": False,
                    "data": {
                        "title": "确认使用可灵" + action_cn + "（" + cost_info + "）",
                        "confirms": ["确认生成", "取消"],
                        "confirm_id": new_id,
                        "preview": {"action": action, "estimated_cost": cost_info},
                    },
                    "error": {
                        "code": "E_USER_CONFIRM_REQUIRED",
                        "type": "UserConfirmRequired",
                        "message": (
                            "预览 - " + action + "\n"
                            "费用预估: " + cost_info + "\n\n"
                            "参数:\n" + _json.dumps(args, ensure_ascii=False, indent=2) + "\n\n"
                            "确认ID: " + new_id + "\n\n"
                            "此操作需要用户确认。"
                        ),
                        "hint": "前端弹窗显示 title/confirms；用户确认后传入 confirm_id 重新调用 kling_generate",
                        "retryable": False,
                    },
                }

    public_arg_error = _validate_public_tool_args(script_name, args)
    if public_arg_error is not None:
        return attach_tool_help_on_failure(script_name, None, public_arg_error)
    args = _coerce_tool_arguments_for_agent(args)
    mod_name = script_name.replace('.py', '')

    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:
        return attach_tool_help_on_failure(
            script_name,
            None,
            {"ok": False, "data": None, "error": {"type": "ImportError", "message": f"进程内加载 {script_name} 失败: {e}"}},
        )

    agent_result = _execute_tool_agent_main(script_name, mod, args)
    return attach_tool_help_on_failure(script_name, mod, agent_result)


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
    if isinstance(result, dict) and result.get("ok") and isinstance(result.get("data"), dict):
        d = result["data"]
        if "read_file" in sn and isinstance(d.get("content"), str):
            text = d["content"]
            snippet = text if len(text) <= text_limit else text[:text_limit] + "\n…"
            slim = {
                "ok": True,
                "data": {
                    "path": d.get("path"),
                    "content": snippet,
                    "truncated": bool(d.get("truncated")),
                    "total_chars_returned": d.get("total_chars_returned"),
                },
            }
            return json.dumps(slim, ensure_ascii=False)
        if "grep_files" in sn and isinstance(d.get("matches"), list):
            m = d["matches"][:80]
            slim = {
                "ok": True,
                "data": {
                    "match_count": d.get("match_count"),
                    "truncated": d.get("truncated"),
                    "matches": m,
                },
            }
            return json.dumps(slim, ensure_ascii=False)
        if ("run_command" in sn or "python_inline" in sn) and isinstance(d.get("stdout"), str):
            out = d["stdout"]
            snippet = out if len(out) <= text_limit else out[:text_limit] + "\n…"
            slim = {"ok": True, "data": {"stdout": snippet, "stdout_len": len(out)}}
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
                    "%s [%s,%s) %s:%d:%d %s"
                    % (
                        fp.split("/")[-1] if "/" in fp else fp.split("\\")[-1] if "\\" in fp else fp,
                        item.get("region_start", ""),
                        item.get("region_end", ""),
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
        if "text_diff" in sn and isinstance(d.get("summary"), dict):
            dm = d.get("diff_markdown")
            sm = d.get("summary")
            slim_dm = dm
            if isinstance(dm, str) and len(dm) > text_limit:
                slim_dm = dm[:text_limit] + "\n…"
            slim = {"ok": True, "data": {"summary": sm, "diff_markdown": slim_dm}}
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
        dm = data.get("diff_markdown")
        return dm if isinstance(dm, str) and dm.strip() else None
    if "replace_in_file" in sn or "write_file" in sn or "apply_patch" in sn:
        dt = data.get("diff_text")
        if isinstance(dt, str) and dt.strip():
            return "```diff\n" + dt + "\n```" if not dt.strip().startswith("```") else dt
        return None
    return None


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
            "result_len": len(raw),
        },
        ensure_ascii=False,
    )


def _is_user_confirm_required(result: dict) -> bool:
    if not isinstance(result, dict) or result.get("ok"):
        return False
    err = result.get("error") or {}
    return err.get("code") == "E_USER_CONFIRM_REQUIRED"


def _merge_confirm_into_user_confirm_args(exec_args: Dict[str, Any], confirm: str) -> Dict[str, Any]:
    """回填用户确认：仅扁平 confirm。"""
    out = dict(exec_args)
    out["confirm"] = confirm
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
            if level == 0 and k == "diff_markdown":
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
    if ("replace_in_file" in sn or "write_file" in sn or "apply_patch" in sn) and isinstance(data.get("diff_text"), str):
        return data["diff_text"]
    return None


_REASONING_EFFORTS: Dict[str, str] = {}



def _chat_api_key_available() -> bool:
    """检查 API Key 是否已配置（从 AGENT_CONFIG 读取，无内置默认值）"""
    key = AGENT_CONFIG["AGENT_MODEL_API_KEY"]
    return bool(key and key.strip())


def _get_reasoning_effort(cid: str = "") -> str:
    """从会话级或配置获取 reasoning_effort（high/max），无默认值，缺失则返回 high。"""
    cid_key = str(cid or "").strip()
    if cid_key and cid_key in _REASONING_EFFORTS:
        return _REASONING_EFFORTS[cid_key]
    raw = str(AGENT_CONFIG.get("AGENT_REASONING_EFFORT") or "").strip().lower()
    if raw in ("high", "max"):
        return raw
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
_KB_MAX_FILE_SIZE = int(AGENT_CONFIG["AGENT_KB_MAX_FILE_SIZE"])


def _kb_safe_resolve_rel(rel: str) -> Optional[Path]:
    """将相对路径解析为 KB 根下的真实文件路径；禁止 .. 与越界。"""
    if not KB_BASE_DIR:
        return None
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return None
    try:
        root = KB_BASE_DIR.resolve()
        candidate = (KB_BASE_DIR.joinpath(*parts)).resolve()
        candidate.relative_to(root)
    except (ValueError, OSError):
        return None
    if not candidate.is_file():
        return None
    return candidate


def _kb_file_allowed_when_checked(fpath: Path) -> bool:
    """勾选时校验：存在、大小上限；表格类需已安装 openpyxl。"""
    try:
        if fpath.stat().st_size > _KB_MAX_FILE_SIZE:
            return False
    except OSError:
        return False
    ext = fpath.suffix.lower()
    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            return False
    return True


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
        fpath = _kb_safe_resolve_rel(rel)
        if not fpath or not _kb_file_allowed_when_checked(fpath):
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


def _kb_attached_file_count(cid: str) -> int:
    """与 _build_kb_prompt 中实际参与拼接的文件数量一致（已勾选、存在、未超大小）。"""
    if not KB_BASE_DIR or not KB_BASE_DIR.is_dir():
        return 0
    with _KB_CHECKED_LOCK:
        checked = _KB_CHECKED_STATE.get(cid, set())
        if not checked:
            _kb_load_single_cid_checked(cid)
            checked = _KB_CHECKED_STATE.get(cid, set())
    if not checked:
        return 0
    n = 0
    for rel in sorted(checked):
        fpath = _kb_safe_resolve_rel(rel)
        if not fpath or not _kb_file_allowed_when_checked(fpath):
            continue
        n += 1
    return n


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


PURE_WINDOW_NO_FINAL_ASSISTANT = "（本轮含工具调用，完整细节见近期完整对话。）"

# 压缩后远期锚定缓存：conversation_id → pure_count（首次压缩后固定，新对话全入近期）
_PURE_ANCHOR_CACHE: Dict[str, int] = {}


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


def _count_user_turns_in_messages(msgs: List[Dict[str, Any]]) -> int:
    """统计消息列表中 role=user 条数（近期/远期「个」与配置回合对齐，不含 assistant/tool）。"""
    return sum(1 for m in msgs if m.get("role") == "user")


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
        txt = str(last_asst.get("content") or "").strip()
        tc = bool(last_asst.get("tool_calls"))
        if txt:
            out.append({"role": "assistant", "content": txt})
        elif has_tool or tc:
            out.append({"role": "assistant", "content": PURE_WINDOW_NO_FINAL_ASSISTANT})
        else:
            out.append({"role": "assistant", "content": ""})
    return out


def _strip_tool_trace_for_summary(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """摘要模型输入：去掉 tool 与 assistant 的 tool_calls / reasoning_content。"""
    out: List[Dict[str, Any]] = []
    for m in msgs:
        r = m.get("role")
        if r == "tool":
            continue
        if r == "user":
            out.append({"role": "user", "content": str(m.get("content") or "")[:8000]})
        elif r == "assistant":
            out.append({"role": "assistant", "content": str(m.get("content") or "")[:6000]})
        elif r == "system":
            out.append({"role": "system", "content": str(m.get("content") or "")[:4000]})
        else:
            out.append({"role": str(r or "user"), "content": str(m.get("content") or "")[:4000]})
    return out


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


def _approx_tokens_text(s: str) -> int:
    """上下文视图条：字符比例估算（系数见 AGENT_TOKEN_ESTIMATE_*）；AGENT_CONTEXT_TOKEN_METHOD 预留。"""
    if not s:
        return 0
    return _estimate_tokens_text_ratio(s)


def _approx_tokens_message(m: Dict[str, Any]) -> int:
    n = _approx_tokens_text(str(m.get("content") or ""))
    rc = m.get("reasoning_content")
    if isinstance(rc, str) and rc.strip():
        n += _approx_tokens_text(rc)
    tc = m.get("tool_calls")
    if isinstance(tc, list) and tc:
        try:
            n += _approx_tokens_text(json.dumps(tc, ensure_ascii=False))
        except (TypeError, ValueError):
            n += 16
    return n


def _build_context_segments(
    persisted: List[Dict[str, Any]], conversation_id: str
) -> Tuple[str, str, List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], int, Dict[str, Any]]:
    """与 _build_api_messages_for_model 相同的语义拆分（未 sanitize），供布局统计。
    近期为 full_pre_stripped + full_suf_stripped，中间由 pure_folded 插入，见 _split_pure_and_full_dialogue。
    返回中 int 为远期带内 user 条数（与 pure_user_rounds 对齐，按 pure_raw 统计）。
    """
    mode = _stored_mode_for_tail(conversation_id)
    fu = _find_first_user_index(persisted)
    kb_part = _build_kb_prompt(conversation_id)
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
    full_pre_stripped = [_strip_internal_message_for_api(m) for m in full_pre_raw]
    full_suf_stripped = [_strip_internal_message_for_api(m) for m in full_suf_raw]
    mode_tail = _ephemeral_mode_system_tail(mode)
    return (
        TOOL_AGENT_SYSTEM_PROMPT,
        kb_part or "",
        summaries,
        pure_folded,
        full_pre_stripped,
        full_suf_stripped,
        pure_user_turns,
        mode_tail,
    )


def _compute_context_layout_payload(conversation_id: str, persisted: List[Dict[str, Any]]) -> Dict[str, Any]:
    sys_base, kb_part, summaries, pure_folded, full_pre, full_suf, pure_user_turns, mode_tail = _build_context_segments(
        persisted, conversation_id
    )
    t_system = _approx_tokens_text(sys_base)
    t_kb = _approx_tokens_text(kb_part)
    t_summary = sum(_approx_tokens_message(m) for m in summaries)
    t_pure = sum(_approx_tokens_message(m) for m in pure_folded)
    t_full = sum(_approx_tokens_message(m) for m in full_pre) + sum(_approx_tokens_message(m) for m in full_suf)
    t_mode = _approx_tokens_message(mode_tail)
    # label 为前端括号标题（与业务含义映射一致）
    labels = {
        "system": "系统占用",
        "knowledge": "知识库",
        "summary": "记忆文件",
        "pure": "远期记忆",
        "full_recent": "近期记忆",
        "mode": "模式",
    }
    keys_tokens = [
        ("system", t_system),
        ("knowledge", t_kb),
        ("summary", t_summary),
        ("pure", t_pure),
        ("full_recent", t_full),
        ("mode", t_mode),
    ]
    counts_map: Dict[str, Optional[int]] = {
        "system": None,
        "knowledge": _kb_attached_file_count(conversation_id),
        "summary": len(summaries),
        "pure": pure_user_turns,
        "full_recent": _count_user_turns_in_messages(full_pre) + _count_user_turns_in_messages(full_suf),
    }
    total_used = sum(t for _, t in keys_tokens)
    budget = max(CONTEXT_LAYOUT_BUDGET_TOKENS, int(total_used), 1)
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
    return {"segments": segments, "total_tokens": int(total_used), "budget_tokens": int(budget)}


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


def _dialogue_summary_excerpt_half_open(messages: List[Dict[str, Any]]) -> Optional[Tuple[int, int]]:
    """摘要截取区间 [start,end)：仅覆盖「首条 user」至「保留尾」起点之前，保留末尾 full+pure 个 user 回合不动。

    与上下文窗口一致：full_user_rounds + pure_user_rounds 个 user 从末尾起不得被本段摘要删除。
    """
    fu = _find_first_user_index(messages)
    if fu is None:
        return None
    user_idxs = [i for i, m in enumerate(messages) if m.get("role") == "user" and i >= fu]
    k = len(user_idxs)
    fn = max(1, int(CONTEXT_FULL_USER_ROUNDS))
    pn = max(0, int(CONTEXT_PURE_USER_ROUNDS))
    reserve = fn + pn
    if reserve <= 0:
        return None
    if k <= reserve:
        # 轮次在保留窗口内，但 token 量可能远超阈值（如单轮大文件读取）。
        # 动态缩小保留窗口至 fn 轮近期，把更早轮次（远期区域）纳入摘取范围。
        if k <= fn:
            return None  # 全在近期，无旧轮可摘
        end_cap = user_idxs[k - fn]  # 保留最后 fn 轮，摘取更早的 k-fn 轮
    else:
        end_cap = user_idxs[k - reserve]
    start0 = fu
    if start0 >= end_cap:
        return None
    s_adj, e_adj = _adjust_excerpt_range_half_open(messages, start0, end_cap)
    e_adj = min(e_adj, end_cap)
    if s_adj >= e_adj:
        return None
    return s_adj, e_adj


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
    sys_base, kb_part, summaries, pure_folded, full_pre, full_suf, _pure_user_turns, mode_tail = _build_context_segments(
        persisted, conversation_id
    )
    sys_content = sys_base
    if kb_part:
        sys_content += "\n\n" + kb_part
    prefix = [{"role": "system", "content": sys_content}]
    tail = list(full_pre) + list(pure_folded) + list(full_suf)
    built = prefix + summaries + tail + [mode_tail]
    return _sanitize_tool_pairing_for_api(built)


_AGENT_SUMMARY_BRIDGE_TAIL = "\n\n【以上为历史摘要，下面继续当前对话】"


def _is_degenerate_summary_body(body: str) -> bool:
    """无实质摘要：空串，或极短且仅含「摘要为空」类占位（避免误伤正文中提及该短语的长摘要）。"""
    b = str(body or "").strip()
    if not b:
        return True
    if len(b) < 10 and "摘要为空" in b:
        return True
    return False


def _summarize_messages_slice_with_llm(slice_msgs: List[Dict[str, Any]], cid: str = "") -> str:
    sys_h = (
        "你是对话整理助手，任务是对下列「历史聊天记录」做摘要提取，不是续写对话、不是执行工具、不要输出工具调用。\n"
        "1) 识别对话场景（如开发、排障、写文档、数据分析等），按场景保留高价值信息。\n"
        "2) 降噪：去掉寒暄与无信息套话；多处矛盾时以用户最终意图与最后澄清为准；重复尝试可合并为一句。\n"
        "3) 事实粒度：保留可执行信息——路径、命令、版本号、明确数字、用户硬性约束（必须/禁止等）。\n"
        "4) 未完成：单独列出仍待处理或待用户确认的事项；已放弃的方案一句话带过即可。\n"
        "5) 输出：纯文本中文；建议分节（背景 / 关键结论 / 约束与约定 / 未完成与待确认 / 风险与注意点）；"
        "总篇幅控制在约 800～1200 字以内，避免过长反噬后续上下文。\n"
        "6) 脉络连贯：关注「用户要什么 → 做了什么 → 得到什么结论」的因果链，不要只罗列事实；对每个关键结论尽量保留。\n"
        "7) 禁止编造：不得引入记录中未出现的文件名、结论或数字；不确定处请写「未在记录中明确」。\n"
        "8) 若剔除噪声后确实无可保留的实质信息：请仅输出「摘要为空」五个字（不要加标点或换行），"
        "使全文总字符数少于 10；不要输出其它占位或解释。"
    )
    compact: List[Dict[str, Any]] = []
    for m in slice_msgs:
        r = m.get("role")
        if r == "tool":
            continue
        if r == "user":
            compact.append({"role": "user", "content": str(m.get("content") or "")[:8000]})
        elif r == "assistant":
            compact.append({"role": "assistant", "content": str(m.get("content") or "")[:6000]})
        elif r == "system":
            compact.append({"role": "system", "content": str(m.get("content") or "")[:4000]})
    reff = _get_reasoning_effort(cid)
    th_type = "enabled" if SUMMARY_THINKING_ENABLED else "disabled"
    payload = {
        "model": default_model_from_env(),
        "messages": [
            {"role": "system", "content": sys_h},
            {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
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


def _maybe_schedule_summarization(cid: str, messages: List[Dict[str, Any]]) -> None:
    with _SUMMARY_STATE_LOCK:
        alive = SUMMARY_IN_PROGRESS.get(cid)
        if alive is not None and time.time() >= alive:
            SUMMARY_IN_PROGRESS.pop(cid, None)
            alive = None
        total_tokens = sum(_approx_tokens_message(m) for m in messages)
        if total_tokens <= CONTEXT_SUMMARY_TOKEN_THRESHOLD:
            return
        if alive is not None:
            return
        rng = _dialogue_summary_excerpt_half_open(messages)
        if rng is None:
            return
        s_adj, e_adj = rng[0], rng[1]
        if s_adj >= e_adj:
            return
        slice_copy = copy.deepcopy(messages[s_adj:e_adj])
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
            direct_preview_content: List[str] = []
            turn_stop_after_this_batch = False
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
                if not script:
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
                    if script == "run_type.py":
                        result = _execute_run_type(conversation_id, exec_args)
                        if isinstance(result, dict) and not result.get("ok"):
                            import importlib as _il_rt

                            try:
                                _rtm = _il_rt.import_module("run_type")
                            except Exception:
                                _rtm = None
                            result = attach_tool_help_on_failure(script, _rtm, result)
                    elif script == "todo_list.py":
                        result = _execute_todo_list(conversation_id, exec_args)
                        result = attach_tool_help_on_failure(script, _todo_list_mod, result)
                    else:
                        if script in WRITE_TOOL_SCRIPTS:
                            current_mode = CONVERSATION_MODES.get(conversation_id, "")
                            if current_mode == "plan":
                                # Plan 模式：replace_in_file 仅允许 dry_run 预览；其余写类工具一律拒绝
                                if script == "replace_in_file.py":
                                    _dr = exec_args.get("dry_run", True)
                                    if _dr is False or _dr == 0:
                                        result = {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，禁止执行写操作。请先切换为 Execute 模式后再执行。"}}
                                        result = attach_tool_help_on_failure(script, None, result)
                                    else:
                                        result = execute_tool_script(script, exec_args)
                                else:
                                    # Plan 模式下所有写操作一律拒绝
                                    result = {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，禁止执行写操作。请先切换为 Execute 模式后再执行。"}}
                                    result = attach_tool_help_on_failure(script, None, result)
                            elif current_mode == "execute":
                                # Execute 模式下必须有执行清单(Todo-List)
                                _todo = _todo_list_mod.session_lists.get(conversation_id)
                                _no_todo = _todo is None or not _todo.get("items")
                                if _no_todo:
                                    result = {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Execute 模式，但未找到执行清单(Todo-List)。请先用 todo_list（action=create）创建执行清单后再执行写操作。"}}
                                    result = attach_tool_help_on_failure(script, None, result)
                                else:
                                    result = execute_tool_script(script, exec_args)
                            else:
                                # Auto 模式：不拦截
                                result = execute_tool_script(script, exec_args)
                        else:
                            # file_search / grep_files / regex_locate：线程执行 + 注入 _progress_dict，宿主轮询推送 tool_progress
                            if script in _TOOL_PROGRESS_SCRIPTS:
                                _search_progress: Dict[str, Any] = {}
                                _exec_args_with_progress = dict(exec_args)
                                _exec_args_with_progress["_progress_dict"] = _search_progress
                                _ts_result_holder: Dict[str, Any] = {}
                                _tool_aborted_by_user = False
                                global _FILE_SEARCH_ALLOWED
                                if script == "file_search.py":
                                    _FILE_SEARCH_ALLOWED = True

                                def _run_tool_with_progress() -> None:
                                    try:
                                        _ts_result_holder["r"] = execute_tool_script(script, _exec_args_with_progress)
                                    finally:
                                        pass

                                import threading as _thr

                                _t = _thr.Thread(target=_run_tool_with_progress, daemon=True)
                                _t.start()
                                try:

                                    def _yield_tool_progress_ev() -> Dict[str, Any]:
                                        _sp = _search_progress.get("scanned")
                                        if _sp is None:
                                            return {}
                                        _cf = _search_progress.get("current_file", "")
                                        if not isinstance(_cf, str):
                                            _cf = str(_cf) if _cf is not None else ""
                                        return {
                                            "type": "tool_progress",
                                            "conversation_id": conversation_id,
                                            "tool_call_id": tc.get("id"),
                                            "scanned": _sp,
                                            "current_file": _cf,
                                        }

                                    while _t.is_alive():
                                        if _peek_conversation_stop_requested(conversation_id, run_id):
                                            _search_progress["_abort"] = True
                                            _tool_aborted_by_user = True
                                            for _join_i in range(40):
                                                if not _t.is_alive():
                                                    break
                                                _t.join(timeout=0.25)
                                            break
                                        _tp_ev = _yield_tool_progress_ev()
                                        if _tp_ev:
                                            yield _tp_ev
                                        _t.join(timeout=0.5)
                                    _tp_final = _yield_tool_progress_ev()
                                    if _tp_final:
                                        yield _tp_final
                                    if _tool_aborted_by_user:
                                        if _consume_conversation_stop_requested(conversation_id, run_id):
                                            pass
                                        turn_stop_after_this_batch = True
                                        result = {
                                            "ok": False,
                                            "data": None,
                                            "error": {"type": "Aborted", "message": "用户已停止任务"},
                                        }
                                    else:
                                        result = _ts_result_holder.get("r", {})
                                finally:
                                    if script == "file_search.py":
                                        _FILE_SEARCH_ALLOWED = False
                            else:
                                result = execute_tool_script(script, exec_args)
                    result = maybe_attach_write_tool_host_dry_run_notice(
                        script,
                        result,
                        CONVERSATION_MODES.get(conversation_id, ""),
                    )
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
                    if script in ("user_confirm.py", "kling_generate.py") and _is_user_confirm_required(result) and isinstance(exec_args, dict):
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
                        if script == "kling_generate.py":
                            _cid = _ucd.get("confirm_id")
                            if _cid:
                                _pending_args["confirm_id"] = str(_cid)
                        PENDING_USER_CONFIRM[conversation_id] = {
                            "tool_call_id": str(tc.get("id") or ""),
                            "exec_args": _pending_args,
                            "script": script,
                            "confirms": list(_cos) if isinstance(_cos, list) else [],
                        }
                    if script == "todo_list.py" and isinstance(result, dict) and result.get("ok"):
                        _td = result.get("data")
                        if _td is None:
                            # 无活跃清单 → 发送关闭信号
                            sse_data = {"type": "todo_list", "conversation_id": conversation_id, "close": True}
                            yield sse_data
                        elif isinstance(_td, dict):
                            if _td.get("close") is True:
                                sse_data = {"type": "todo_list", "conversation_id": conversation_id, "close": True}
                                yield sse_data
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
                                yield sse_data
                    yield _te_tool_end
                    
                    if script == "run_type.py" and isinstance(result, dict) and result.get("ok"):
                        _dc = result.get("data") or {}
                        _rtm = _dc.get("run_type")
                        if _rtm in ("auto", "plan", "execute"):
                            yield {"type": "mode_changed", "mode": _rtm}
                    md_chat = _chat_diff_markdown_for_tool(script, result, exec_args)
                    if md_chat:
                        yield {"type": "assistant_markdown", "markdown": md_chat}
                    _preview = _build_direct_preview_message(script, result, user_text_for_preview)
                    if _preview:
                        direct_preview_content.append(_preview)
                turn_tool_records.append({"api_name": api_name, "script": script or "(unknown)", "ok": bool(result.get("ok"))})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": _truncate_tool_result(result),
                })
                yield _context_layout_event(conversation_id, messages)
                if turn_stop_after_this_batch:
                    yield _finish_conversation_stopped(conversation_id, rollback_messages)
                    return
            if conversation_id in PENDING_USER_CONFIRM:
                CONVERSATIONS[conversation_id] = messages
                _save_conversation(conversation_id, messages)
                yield {"type": "paused_for_user_confirm", "conversation_id": conversation_id}
                return
            if direct_preview_content:
                combined = "\n\n".join(direct_preview_content)
                messages.append({"role": "assistant", "content": combined, "reasoning_content": ""})
                if not content_parts:
                    yield {"type": "assistant", "content": combined}
                yield _context_layout_event(conversation_id, messages)
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
    yield _context_layout_event(conversation_id, messages)
    yield {"type": "done"}


# Embedded UI (VS Code-like dark theme, chat + steps). Served from / without external static files.
# ---- UI HTML loaded from external file ----
UI_HTML_FILE = AGENT_ROOT / "res" / "html" / "agent-ui.html"
RESET_CSS_FILE = AGENT_ROOT / "res" / "css" / "reset.css"
UI_CSS_FILE = AGENT_ROOT / "res" / "css" / "agent-ui.css"
UI_JS_FILE = AGENT_ROOT / "res" / "js" / "agent-ui.js"

_INLINE_CSS = (
    RESET_CSS_FILE.read_text(encoding="utf-8").rstrip()
    + "\n\n"
    + UI_CSS_FILE.read_text(encoding="utf-8")
)
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

# 沉浸模式 v1.1：模块化 UI 静态资源 + 独立入口（经典 `/` 仍为内联 HTML，不变）
_IMMERSIVE_HTML = AGENT_ROOT / "res" / "html" / "agent-immersive.html"
app.mount("/assets", StaticFiles(directory=str(AGENT_ROOT / "res")), name="assets")


@app.get("/immersive", include_in_schema=False)
def immersive_index():
    if not _IMMERSIVE_HTML.is_file():
        raise HTTPException(404, "immersive UI not found")
    return FileResponse(str(_IMMERSIVE_HTML), media_type="text/html; charset=utf-8")


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
    # 必须用当前 HTTP 流绑定的 cid 覆盖：内层 yield 若带空/None 的 conversation_id，
    # setdefault 不会写入，前端 normalizeConversationId 会丢弃整包 SSE。
    _cid = str(cid or "").strip()
    if _cid:
        out["conversation_id"] = _cid
    else:
        out.setdefault("conversation_id", "")
    return out


def _context_layout_event(conversation_id: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """SSE：当前 messages 下的上下文视图（供底部比例条 / 悬浮层）。"""
    return _conversation_sse_event(
        conversation_id,
        {"type": "context_layout", **_compute_context_layout_payload(conversation_id, messages)},
    )


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
    script_name = str(pending.get("script", "user_confirm.py"))
    if script_name == "kling_generate.py":
        # 仅第一选项（确认生成）放行，其他全部拦截
        _first_opt = str(pending.get("confirms", [None])[0]) if isinstance(pending.get("confirms"), list) and len(pending.get("confirms")) > 0 else "确认生成"
        if conf == _first_opt:
            result = execute_tool_script(script_name, exec_args0)
            exec_args1 = exec_args0
        else:
            try:
                from agent_v2.live_state import kling_mark_confirmed as _kmc, kling_consume_confirm_id as _kcc
                _cid = str(exec_args0.get("confirm_id") or "")
                if _cid:
                    _kmc(_cid)
                    _kcc(_cid)
            except Exception:
                pass
            result = {"ok": True, "data": {"confirm": str(conf) if conf else "取消", "cancelled": True, "message": "用户已取消操作，请立即停止当前任务，不要再调用任何生成工具！"}}
            exec_args1 = exec_args0
    else:
        exec_args1 = _merge_confirm_into_user_confirm_args(exec_args0, conf)
        result = execute_tool_script(script_name, exec_args1)
    if not (isinstance(result, dict) and result.get("ok") is True):
        _record_tool_debug_failure(
            conversation_id=cid,
            api_name=script_name.replace('.py', ''),
            script=script_name,
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
                    "preview": preview_tool_result(script_name, result),
                }
                yield f"data: {json.dumps(_tpd, ensure_ascii=False)}\n\n"
            finally:
                pass
            _ensure_conversation_loaded(cid)
            if _persisted_session_unreadable_after_load(cid):
                _se = {
                    "type": "error",
                    "conversation_id": cid,
                    "where": "session_persist",
                    "detail": SESSION_PERSIST_UNREADABLE_SSE_DETAIL,
                }
                yield f"data: {json.dumps(_se, ensure_ascii=False)}\n\n"
                return
            _apply_conversation_request_options(cid, mode, mod)
            _msgs_ctx = list(CONVERSATIONS.get(cid, []))
            yield f"data: {json.dumps(_context_layout_event(cid, _msgs_ctx), ensure_ascii=False)}\n\n"
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
    messages = CONVERSATIONS.get(cid)
    if not messages:
        messages = []
    # 与发 LLM 前一致：先合并 pending 摘要，再算上下文视图（与 agent_v2 /api/chat/history 对齐）
    _merge_pending_excerpts_for_conversation(cid, messages)
    context_layout = _context_layout_event(cid, messages)
    # 附带当前待办清单，供前端刷新页面后恢复 Todo 显示
    todo_list = None
    try:
        todo_r = _todo_list_mod.execute(cid, {"action": "query"})
        if todo_r.get("ok") and todo_r.get("data") is not None:
            todo_list = todo_r["data"]
    except Exception:
        pass
    return {
        "ok": True,
        "conversation_id": cid,
        "items": _chat_history_from_messages(messages),
        "todo_list": todo_list,
        "context_layout": context_layout,
    }


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
    seen_ids: set[str] = set()
    try:
        # 优先从 .title 文件收集会话（标题来源）
        title_files = list(SESSION_DIR.glob("*.title")) + list(SESSION_DIR.glob("*/*.title"))
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
            rows.append({"id": cid, "title": title or title_by_id.get(cid) or f"会话 {cid[:8]}", "updated_at": updated_at, "date_group": _session_date_group_from_path(tf)})
        # 补充没有 .title 文件的 session（新会话等），仍显示
        json_files = list(SESSION_DIR.glob("*.json")) + list(SESSION_DIR.glob("*/*.json"))
        for fp in json_files:
            cid = fp.stem
            if not re.match(r"^[A-Za-z0-9._:-]{8,128}$", cid) or cid in seen_ids:
                continue
            seen_ids.add(cid)
            messages = CONVERSATIONS.get(cid)
            if messages is None:
                messages = _load_conversation(cid) or []
            title = _load_conversation_title(cid) or title_by_id.get(cid) or _fallback_title_from_messages(cid, list(messages))
            try:
                updated_at = int(fp.stat().st_mtime * 1000)
            except Exception:
                updated_at = 0
            rows.append({"id": cid, "title": title[:80], "updated_at": updated_at, "date_group": _session_date_group_from_path(fp)})
    except Exception:
        pass
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
    # 生成标题后写入独立 .title 文件
    _save_title_file(cid, title)
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
    """列出知识库目录下文件（不按后缀过滤；勾选时再校验是否可用）。"""
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
        if "__pycache__" in p.parts:
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
    raw_paths = set(body.checked or [])
    accepted: Set[str] = set()
    for rel in raw_paths:
        fp = _kb_safe_resolve_rel(rel)
        if fp and _kb_file_allowed_when_checked(fp):
            try:
                norm = str(fp.relative_to(KB_BASE_DIR.resolve())).replace("\\", "/")
            except ValueError:
                continue
            accepted.add(norm)
    with _KB_CHECKED_LOCK:
        if accepted:
            _KB_CHECKED_STATE[cid] = accepted
        else:
            _KB_CHECKED_STATE.pop(cid, None)
        _kb_persist_checked()
    return {"ok": True, "checked": sorted(accepted)}


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
        # @路径预读：仅对「普通文件」注入全文；目录不预读（模型可据路径用工具浏览）
        import re as _re
        _at_files = _re.findall(
            r'@((?:"[^"]+")|(?:[A-Za-z]:[\\/][^\s]+)|(?:~[^\s]+)|(?:/[^\s]+))',
            text,
        )
        _injected = []
        for _fp in _at_files:
            _fp = _strip_config_path_value(_fp.strip('"').strip("'"))
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
                _err_ev = {"type": "error", "conversation_id": cid, "where": "config", "detail": "请先在 config.ini 的 [model] 节配置 api_key（或设置环境变量 CHAT_API_KEY）"}
                yield f"data: {json.dumps(_err_ev, ensure_ascii=False)}\n\n"
                return
            _ensure_conversation_loaded(cid)
            if _persisted_session_unreadable_after_load(cid):
                _se = {
                    "type": "error",
                    "conversation_id": cid,
                    "where": "session_persist",
                    "detail": SESSION_PERSIST_UNREADABLE_SSE_DETAIL,
                }
                yield f"data: {json.dumps(_se, ensure_ascii=False)}\n\n"
                return
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

    _workspace = _strip_config_path_value(str(AGENT_CONFIG.get("AGENT_WORKSPACE_DIR") or ""))
    if _workspace:
        _default = Path(_workspace).expanduser().resolve()
    else:
        _default = Path.home()
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
        raw_s = _strip_config_path_value(str(path).strip())
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
    # 加载配置（读取 config.ini，设置环境变量等）
    from util.config_loader import load_config
    load_config(verbose=True)
    port_str = str(AGENT_CONFIG["AGENT_SERVER_PORT"]).strip()
    if not port_str:
        print("FATAL: AGENT_SERVER_PORT 未设置！请在 config.ini 的 [server] 节配置 port", flush=True)
        sys.exit(1)
    
    # ── API Key 检查 ──
    if not _chat_api_key_available():
        print("⚠️  WARNING: API Key 未配置或为空！请在 config.ini 的 [model] 节设置 api_key 或环境变量 CHAT_API_KEY", file=sys.stderr, flush=True)
        print("⚠️  或通过环境变量 CHAT_API_KEY 设置", file=sys.stderr, flush=True)

    uvicorn.run(app, host=AGENT_CONFIG["AGENT_SERVER_HOST"], port=int(port_str))


if __name__ == "__main__":
    main()
