# -*- coding: utf-8 -*-
"""v2：配置、数据根路径、KB 勾选、工具路径与调试辅助。"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from util.config_loader import load_config
    AGENT_CONFIG = load_config(verbose=False)
except ImportError:
    AGENT_CONFIG = {}

# PyInstaller 打包后 __file__ 指向 sys._MEIPASS，源码模式正常
if getattr(sys, "frozen", False):
    _base = Path(sys._MEIPASS)
else:
    _base = Path(__file__).resolve().parent.parent  # agent_v2 -> project root
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


def _kb_persist_checked() -> None:
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


def _kb_load_single_cid_checked(cid: str) -> None:
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


def _kb_load_checked() -> None:
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
CONTEXT_FULL_USER_ROUNDS = int(_CONTEXT_CFG["AGENT_CONTEXT_FULL_USER_ROUNDS"])
CONTEXT_PURE_USER_ROUNDS = int(_CONTEXT_CFG["AGENT_CONTEXT_PURE_USER_ROUNDS"])
_SUMMARY_THINK_RAW = str(_CONTEXT_CFG["AGENT_SUMMARY_THINKING"]).strip().lower()
SUMMARY_THINKING_ENABLED = _SUMMARY_THINK_RAW not in ("", "0", "false", "no", "off", "disabled")
SUMMARY_OUTPUT_MAX_CHARS = int(_CONTEXT_CFG["AGENT_SUMMARY_OUTPUT_MAX_CHARS"])
TOKEN_ESTIMATE_EN_PER_CHAR = float(_CONTEXT_CFG["AGENT_TOKEN_ESTIMATE_EN_PER_CHAR"])
TOKEN_ESTIMATE_ZH_PER_CHAR = float(_CONTEXT_CFG["AGENT_TOKEN_ESTIMATE_ZH_PER_CHAR"])
CONTEXT_LAYOUT_BUDGET_TOKENS = int(_CONTEXT_CFG["AGENT_CONTEXT_LAYOUT_BUDGET_TOKENS"])
CONTEXT_SUMMARY_TOKEN_THRESHOLD = int(_CONTEXT_CFG["AGENT_CONTEXT_SUMMARY_TOKEN_THRESHOLD"])
SUMMARY_IN_PROGRESS_TTL_SEC = float(_CONTEXT_CFG["AGENT_SUMMARY_IN_PROGRESS_TTL_SEC"])
MAX_TOOL_ROUNDS = int(_CONTEXT_CFG["AGENT_MAX_TOOL_ROUNDS"])
UI_RESTORE_MAX_TABS = int(_CONTEXT_CFG["AGENT_UI_RESTORE_MAX_TABS"])
UI_RESTORE_MAX_CHAT_ITEMS = int(_CONTEXT_CFG["AGENT_UI_RESTORE_MAX_CHAT_ITEMS"])
_PREVIEW_RAW = _CONTEXT_CFG["AGENT_PREVIEW_INTENT_KEYS"]
PREVIEW_INTENT_KEYS = tuple(_PREVIEW_RAW) if isinstance(_PREVIEW_RAW, (list, tuple)) else tuple(_PREVIEW_RAW)
AT_MESSAGE_FILE_PREFETCH = bool(_CONTEXT_CFG["AGENT_AT_MESSAGE_FILE_PREFETCH"])


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


__all__ = [
    "AGENT_CONFIG",
    "AGENT_ROOT",
    "AT_MESSAGE_FILE_PREFETCH",
    "CONTEXT_FULL_USER_ROUNDS",
    "CONTEXT_LAYOUT_BUDGET_TOKENS",
    "CONTEXT_PURE_USER_ROUNDS",
    "CONTEXT_SUMMARY_TOKEN_THRESHOLD",
    "DATA_ROOT",
    "EXCERPTS_DIR",
    "KB_BASE_DIR",
    "LAST_OPEN_SESSION_STATE_FILE",
    "MAX_TOOL_ROUNDS",
    "PREVIEW_INTENT_KEYS",
    "SESSION_APP_ENTROPY",
    "SESSION_DIR",
    "SESSION_ENCRYPTION_MAGIC",
    "SESSION_KEY_FILE",
    "SUMMARY_IN_PROGRESS_TTL_SEC",
    "SUMMARY_OUTPUT_MAX_CHARS",
    "SUMMARY_THINKING_ENABLED",
    "TOKEN_ESTIMATE_EN_PER_CHAR",
    "TOKEN_ESTIMATE_ZH_PER_CHAR",
    "TOOLS_DIR",
    "TOOL_DEBUG_DIR",
    "TOOL_LIST_JSON",
    "UI_RESTORE_MAX_CHAT_ITEMS",
    "UI_RESTORE_MAX_TABS",
    "USAGE_ACCUM_FILE",
    "_KB_CHECKED_LOCK",
    "_KB_CHECKED_STATE",
    "_ensure_tools_sys_path",
    "_execute_todo_list",
    "_kb_load_single_cid_checked",
    "_kb_persist_checked",
    "_log_agent_console_sse",
    "_log_agent_console_tool",
    "_record_tool_debug_failure",
    "_resolve_tool_script_path",
    "_strip_config_path_value",
    "_todo_list_mod",
]
