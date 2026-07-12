# -*- coding: utf-8 -*-
"""进程内会话表、并发 run、停止标志（agent_v4 运行时状态）。"""
from __future__ import annotations

import contextlib
import json
import queue
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------- in-memory multi-round conversations (stateless API per DeepSeek docs) ----------
CONVERSATIONS: Dict[str, List[Dict[str, Any]]] = {}
PENDING_USER_CONFIRM: Dict[str, Dict[str, Any]] = {}
AGENT_SESSIONS: Dict[str, Dict[str, Any]] = {}
_AGENT_SESSIONS_LOCK = threading.Lock()
AGENT_WAITS: Dict[str, Dict[str, Any]] = {}
_AGENT_WAITS_LOCK = threading.Lock()
SESSION_INBOX: Dict[str, List[Dict[str, Any]]] = {}
_SESSION_INBOX_LOCK = threading.Lock()


def _agent_sessions_file() -> Path:
    from agent_v4.bootstrap import DATA_ROOT

    return DATA_ROOT / "agent_sessions.json"


def _agent_inbox_dir() -> Path:
    from agent_v4.bootstrap import DATA_ROOT

    return DATA_ROOT / "agent_inbox"


def _agent_waits_file() -> Path:
    from agent_v4.bootstrap import DATA_ROOT

    return DATA_ROOT / "agent_waits.json"


def save_agent_sessions() -> None:
    try:
        fp = _agent_sessions_file()
        fp.parent.mkdir(parents=True, exist_ok=True)
        with _AGENT_SESSIONS_LOCK:
            data = {"agents": dict(AGENT_SESSIONS)}
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"WARN: save_agent_sessions failed: {exc}", file=sys.stderr, flush=True)


def load_agent_sessions() -> None:
    try:
        fp = _agent_sessions_file()
        if fp.is_file():
            data = json.loads(fp.read_text(encoding="utf-8"))
            agents = data.get("agents") if isinstance(data, dict) else None
            if isinstance(agents, dict):
                with _AGENT_SESSIONS_LOCK:
                    AGENT_SESSIONS.update({str(k): dict(v) for k, v in agents.items() if isinstance(v, dict)})
        inbox_dir = _agent_inbox_dir()
        if inbox_dir.is_dir():
            with _SESSION_INBOX_LOCK:
                for p in inbox_dir.glob("*.jsonl"):
                    cid = p.stem
                    rows: List[Dict[str, Any]] = []
                    try:
                        for line in p.read_text(encoding="utf-8").splitlines():
                            if not line.strip():
                                continue
                            obj = json.loads(line)
                            if isinstance(obj, dict):
                                rows.append(obj)
                    except Exception:
                        rows = []
                    if rows:
                        SESSION_INBOX[cid] = rows
        waits_fp = _agent_waits_file()
        if waits_fp.is_file():
            data = json.loads(waits_fp.read_text(encoding="utf-8"))
            waits = data.get("waits") if isinstance(data, dict) else None
            if isinstance(waits, dict):
                with _AGENT_WAITS_LOCK:
                    AGENT_WAITS.update({str(k): dict(v) for k, v in waits.items() if isinstance(v, dict)})
    except Exception as exc:
        print(f"WARN: load_agent_sessions failed: {exc}", file=sys.stderr, flush=True)


def upsert_agent_session(cid: str, **fields: Any) -> Dict[str, Any]:
    key = str(cid or "").strip()
    if not key:
        return {}
    now = int(time.time())
    with _AGENT_SESSIONS_LOCK:
        item = dict(AGENT_SESSIONS.get(key) or {})
        item.setdefault("cid", key)
        item.setdefault("created_at", now)
        item["updated_at"] = now
        for k, v in fields.items():
            if v is not None:
                item[k] = v
        AGENT_SESSIONS[key] = item
    save_agent_sessions()
    return item


def get_agent_session(cid: str) -> Dict[str, Any]:
    key = str(cid or "").strip()
    with _AGENT_SESSIONS_LOCK:
        return dict(AGENT_SESSIONS.get(key) or {})


def list_agent_sessions() -> Dict[str, Dict[str, Any]]:
    with _AGENT_SESSIONS_LOCK:
        return {k: dict(v) for k, v in AGENT_SESSIONS.items()}


def _persist_inbox_locked(cid: str) -> None:
    inbox_dir = _agent_inbox_dir()
    inbox_dir.mkdir(parents=True, exist_ok=True)
    fp = inbox_dir / f"{str(cid or '').strip()}.jsonl"
    rows = SESSION_INBOX.get(str(cid or "").strip(), []) or []
    if not rows:
        try:
            if fp.is_file():
                fp.unlink()
        except Exception:
            pass
        return
    fp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")


def _persist_waits() -> None:
    try:
        fp = _agent_waits_file()
        fp.parent.mkdir(parents=True, exist_ok=True)
        with _AGENT_WAITS_LOCK:
            data = {"waits": dict(AGENT_WAITS)}
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"WARN: persist agent waits failed: {exc}", file=sys.stderr, flush=True)


def should_suspend_after_session_wait(tool_result: Any) -> bool:
    """session_wait 返回 pending 时，仅 data.suspend=true 才触发宿主挂起（勿用 should_stop_turn）。"""
    if not isinstance(tool_result, dict) or not tool_result.get("ok"):
        return False
    data = tool_result.get("data")
    if not isinstance(data, dict) or not data.get("pending"):
        return False
    return bool(data.get("suspend"))


def suspend_agent_wait(cid: str, target_ids: List[str], thread_id: str = "") -> Dict[str, Any]:
    key = str(cid or "").strip()
    if not key:
        return {}
    item = {
        "cid": key,
        "target_ids": [str(x).strip() for x in target_ids if str(x).strip()],
        "thread_id": str(thread_id or "").strip(),
        "created_at": int(time.time()),
    }
    with _AGENT_WAITS_LOCK:
        AGENT_WAITS[key] = item
    _persist_waits()
    return dict(item)


def clear_agent_wait(cid: str) -> None:
    key = str(cid or "").strip()
    if not key:
        return
    with _AGENT_WAITS_LOCK:
        AGENT_WAITS.pop(key, None)
    _persist_waits()


def pop_waits_satisfied_by(sender_id: str, target_id: str, thread_id: str = "") -> List[Dict[str, Any]]:
    sender = str(sender_id or "").strip()
    target = str(target_id or "").strip()
    thread = str(thread_id or "").strip()
    if not sender or not target:
        return []
    out: List[Dict[str, Any]] = []
    with _AGENT_WAITS_LOCK:
        for cid, wait in list(AGENT_WAITS.items()):
            if cid != target:
                continue
            targets = [str(x).strip() for x in (wait.get("target_ids") or [])]
            if sender not in targets:
                continue
            wthread = str(wait.get("thread_id") or "").strip()
            if wthread and thread != wthread:
                continue
            out.append(dict(wait))
            AGENT_WAITS.pop(cid, None)
    if out:
        _persist_waits()
    return out


# ── SSE 多播：Worker 执行事件推送给观察者 ──
# session_id → list of observer session_ids
_CONFIRM_IDS: Dict[str, Dict[str, Any]] = {}
_CONFIRM_LOCK = threading.Lock()


def _prune_stale_confirms() -> None:
    """移除超时未消耗的 Kling 确认项，避免内存泄漏。"""
    now = time.time()
    with _CONFIRM_LOCK:
        stale = [
            k
            for k, v in _CONFIRM_IDS.items()
            if now - float(v.get("created_at") or 0) > _CONFIRM_MAX_AGE_SEC
        ]
        for k in stale:
            _CONFIRM_IDS.pop(k, None)


def create_confirm_id(action: str, params: dict) -> str:
    """创建待确认的确认ID，返回 UUID。"""
    import uuid

    _prune_stale_confirms()
    cid = str(uuid.uuid4())
    with _CONFIRM_LOCK:
        _CONFIRM_IDS[cid] = {
            "confirmed": False,
            "action": action,
            "params": dict(params),
            "created_at": time.time(),
        }
    return cid


def mark_confirmed(confirm_id: str) -> bool:
    """将确认ID标记为已确认。"""
    with _CONFIRM_LOCK:
        if confirm_id in _CONFIRM_IDS:
            _CONFIRM_IDS[confirm_id]["confirmed"] = True
            return True
    return False


def consume_confirm_id(confirm_id: str) -> Optional[Dict[str, Any]]:
    """检查并消耗确认ID。返回任务信息表示放行，None 表示无效。"""
    with _CONFIRM_LOCK:
        info = _CONFIRM_IDS.get(confirm_id)
        if info is None:
            return None
        if not info.get("confirmed"):
            return None
        # 已确认，消耗删除
        del _CONFIRM_IDS[confirm_id]
        return dict(info)

CONVERSATION_MODES: Dict[str, str] = {}
CONVERSATION_AUDIT_ONLY: Dict[str, bool] = {}
SUMMARY_IN_PROGRESS: Dict[str, float] = {}
PENDING_EXCERPT_PATHS: Dict[str, List[str]] = {}
_SUMMARY_STATE_LOCK = threading.Lock()
_CONVERSATION_RUN_LOCKS: Dict[str, threading.RLock] = {}
_CONVERSATION_RUN_LOCKS_LOCK = threading.Lock()
_TOOL_EXEC_LOCK = threading.RLock()  # 兼容旧引用；新代码请用 get_tool_exec_lock(cid)
_TOOL_EXEC_LOCKS: Dict[str, threading.RLock] = {}
_TOOL_EXEC_LOCKS_META = threading.Lock()
_FILE_SEARCH_GATE: Dict[str, bool] = {}
_FILE_SEARCH_GATE_LOCK = threading.Lock()
_CONFIRM_MAX_AGE_SEC = 3600
_CONVERSATION_STOP_FLAGS: Dict[str, Set[str]] = {}
_ACTIVE_CONVERSATION_RUNS: Dict[str, str] = {}
_CONVERSATION_STOP_LOCK = threading.Lock()
_SERVER_SHUTTING_DOWN = False

# ── 页面级全局 SSE：同一进程只保留最新连接，所有 cid 事件通过该队列分发 ──
_GLOBAL_SSE_LOCK = threading.Lock()
_GLOBAL_SSE_TOKEN = ""
_GLOBAL_SSE_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=2000)


def server_shutting_down() -> bool:
    """进程正在退出（Ctrl+C / 托盘退出）；用于打断 SSE 与 LLM 流式读。"""
    return _SERVER_SHUTTING_DOWN


_COMMAND_INPUT_TARGETS: Dict[str, Dict[str, Any]] = {}
_COMMAND_INPUT_LOCK = threading.Lock()


def register_command_input_target(key: str, progress: Dict[str, Any]) -> None:
    k = str(key or "").strip()
    if not k or not isinstance(progress, dict):
        return
    with _COMMAND_INPUT_LOCK:
        _COMMAND_INPUT_TARGETS[k] = progress


def unregister_command_input_target(key: str) -> None:
    k = str(key or "").strip()
    if not k:
        return
    with _COMMAND_INPUT_LOCK:
        _COMMAND_INPUT_TARGETS.pop(k, None)


def submit_command_input(key: str, text: str) -> bool:
    """向正在执行的 run_command 写入 stdin（由 progress['_user_input'] 传递）。"""
    k = str(key or "").strip()
    if not k:
        return False
    with _COMMAND_INPUT_LOCK:
        progress = _COMMAND_INPUT_TARGETS.get(k)
    if not isinstance(progress, dict):
        return False
    progress["_user_input"] = str(text or "")
    return True


def abort_all_conversation_runs_on_shutdown() -> None:
    """uvicorn 关闭时标记所有活跃 run 为停止，避免 graceful shutdown 被 SSE 挂死。"""
    global _SERVER_SHUTTING_DOWN
    _SERVER_SHUTTING_DOWN = True
    try:
        from command_safety import force_kill_active_shell_process

        force_kill_active_shell_process()
    except Exception:
        pass
    with _CONVERSATION_STOP_LOCK:
        for cid, run_id in list(_ACTIVE_CONVERSATION_RUNS.items()):
            if run_id:
                _CONVERSATION_STOP_FLAGS.setdefault(cid, set()).add(run_id)
    publish_global_sse_event({"type": "server_shutdown"})


def open_global_sse_channel() -> str:
    """刷新页面/重连时创建最新全局 SSE 通道；旧通道看到 token 变化后自然退出。"""
    global _GLOBAL_SSE_TOKEN, _GLOBAL_SSE_QUEUE
    token = str(uuid.uuid4())
    with _GLOBAL_SSE_LOCK:
        _GLOBAL_SSE_TOKEN = token
        _GLOBAL_SSE_QUEUE = queue.Queue(maxsize=2000)
        try:
            _GLOBAL_SSE_QUEUE.put_nowait({"type": "global_sse_ready"})
        except queue.Full:
            pass
    return token


def is_global_sse_current(token: str) -> bool:
    with _GLOBAL_SSE_LOCK:
        return bool(token) and token == _GLOBAL_SSE_TOKEN


def publish_global_sse_event(event: Dict[str, Any]) -> None:
    """发布事件到当前页面级 SSE；没有前端连接时丢弃，不影响后台 Agent 执行。"""
    if not isinstance(event, dict):
        return
    with _GLOBAL_SSE_LOCK:
        q = _GLOBAL_SSE_QUEUE
    try:
        q.put_nowait(dict(event))
    except queue.Full:
        try:
            q.get_nowait()
        except Exception:
            pass
        try:
            q.put_nowait(dict(event))
        except Exception:
            pass


def next_global_sse_event(token: str, timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    if not is_global_sse_current(token):
        return None
    with _GLOBAL_SSE_LOCK:
        q = _GLOBAL_SSE_QUEUE
    try:
        ev = q.get(timeout=max(0.1, float(timeout)))
    except queue.Empty:
        return {"type": "heartbeat", "ts": int(time.time() * 1000)}
    if not is_global_sse_current(token):
        return None
    return ev


def get_tool_exec_lock(conversation_id: str = "") -> threading.RLock:
    """按会话隔离工具执行锁，避免多 Agent 全局串行。"""
    key = str(conversation_id or "").strip() or "__shared__"
    with _TOOL_EXEC_LOCKS_META:
        lock = _TOOL_EXEC_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _TOOL_EXEC_LOCKS[key] = lock
        return lock


def set_file_search_allowed(conversation_id: str, allowed: bool) -> None:
    key = str(conversation_id or "").strip()
    if not key:
        return
    with _FILE_SEARCH_GATE_LOCK:
        if allowed:
            _FILE_SEARCH_GATE[key] = True
        else:
            _FILE_SEARCH_GATE.pop(key, None)


def is_file_search_allowed(conversation_id: str = "") -> bool:
    key = str(conversation_id or "").strip()
    if not key:
        return False
    with _FILE_SEARCH_GATE_LOCK:
        return bool(_FILE_SEARCH_GATE.get(key))


def enqueue_session_inbox(cid: str, message: Dict[str, Any]) -> int:
    key = str(cid or "").strip()
    if not key:
        return 0
    with _SESSION_INBOX_LOCK:
        q = SESSION_INBOX.setdefault(key, [])
        q.append(dict(message))
        n = len(q)
    _persist_inbox_locked(key)
    return n


def pop_session_inbox(cid: str) -> Optional[Dict[str, Any]]:
    key = str(cid or "").strip()
    if not key:
        return None
    with _SESSION_INBOX_LOCK:
        q = SESSION_INBOX.get(key) or []
        if not q:
            SESSION_INBOX.pop(key, None)
            return None
        msg = q.pop(0)
        if not q:
            SESSION_INBOX.pop(key, None)
        _persist_inbox_locked(key)
        return msg


def session_inbox_size(cid: str) -> int:
    with _SESSION_INBOX_LOCK:
        return len(SESSION_INBOX.get(str(cid or "").strip(), []) or [])


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
    key = str(cid or "")
    with _CONVERSATION_STOP_LOCK:
        active = _ACTIVE_CONVERSATION_RUNS.get(key)
        eff = str(run_id or active or "")
        flags = _CONVERSATION_STOP_FLAGS.get(key)
        if not flags or not eff:
            return False
        return eff in flags


def _get_conversation_run_lock(cid: str) -> threading.RLock:
    key = str(cid or "")
    with _CONVERSATION_RUN_LOCKS_LOCK:
        lock = _CONVERSATION_RUN_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _CONVERSATION_RUN_LOCKS[key] = lock
        return lock


def get_conversation_run_lock(cid: str) -> threading.RLock:
    """按会话 id 返回可重入锁，保护 CONVERSATIONS 读写与跨 Agent 入站 append。"""
    return _get_conversation_run_lock(cid)


@contextlib.contextmanager
def conversation_run_locks(*cids: str):
    """按字典序获取多个会话锁，避免双写交错。"""
    keys = sorted({str(c or "").strip() for c in cids if str(c or "").strip()})
    locks = [get_conversation_run_lock(k) for k in keys]
    for lk in locks:
        lk.acquire()
    try:
        yield
    finally:
        for lk in reversed(locks):
            lk.release()


_PEER_TURN_CHAIN: Dict[str, int] = {}
_PEER_TURN_TS: Dict[str, float] = {}
_PEER_TURN_LOCK = threading.Lock()


def reset_peer_turn_chain(cid: str) -> None:
    """Boss 用户消息发起 turn 时清零连续 peer 自动 turn 计数。"""
    key = str(cid or "").strip()
    if not key:
        return
    with _PEER_TURN_LOCK:
        _PEER_TURN_CHAIN.pop(key, None)
        _PEER_TURN_TS.pop(key, None)


def try_acquire_peer_turn_slot(
    cid: str,
    *,
    max_consecutive: int,
    min_interval_sec: float,
) -> Tuple[bool, str]:
    """peer 触发的自动 turn 限流；返回 (allowed, reason)。"""
    key = str(cid or "").strip()
    if not key:
        return False, "empty conversation_id"
    now = time.monotonic()
    with _PEER_TURN_LOCK:
        last = float(_PEER_TURN_TS.get(key, 0.0) or 0.0)
        if min_interval_sec > 0 and last > 0 and (now - last) < min_interval_sec:
            return False, f"peer turn 间隔须 >= {min_interval_sec}s"
        n = int(_PEER_TURN_CHAIN.get(key, 0) or 0) + 1
        if max_consecutive > 0 and n > max_consecutive:
            return False, f"连续 peer 自动 turn 已达上限 {max_consecutive}"
        _PEER_TURN_CHAIN[key] = n
        _PEER_TURN_TS[key] = now
        return True, ""