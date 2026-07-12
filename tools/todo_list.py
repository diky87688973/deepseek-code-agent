# -*- coding: utf-8 -*-
"""会话 Todo-List：仅由 Agent 宿主按 conversation_id 调用 `execute`（扁平 Python 类型）。

`agent_main` 仅作占位；真实逻辑在 `execute`。失败帮助由宿主 catalog 提供。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import agent_common as ac

session_lists: Dict[str, Dict[str, Any]] = {}
_storage_dir: Optional[Path] = None


def configure_storage(directory: Path) -> None:
    """由宿主在启动时调用一次，指定会话清单落盘目录。"""
    global _storage_dir
    _storage_dir = directory
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _persist(cid: str) -> None:
    if not _storage_dir or not cid:
        return
    try:
        lst = session_lists.get(cid)
        fp = _storage_dir / f"{cid}.json"
        if lst is None:
            if fp.exists():
                fp.unlink()
            return
        fp.write_text(json.dumps(lst, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load(cid: str) -> None:
    if not cid or cid in session_lists or not _storage_dir:
        return
    try:
        fp = _storage_dir / f"{cid}.json"
        if fp.exists():
            raw = fp.read_text(encoding="utf-8")
            lst = json.loads(raw)
            if isinstance(lst, dict) and "items" in lst and ("list_id" in lst or "listId" in lst):
                if "listId" in lst and "list_id" not in lst:
                    lst["list_id"] = lst.pop("listId")
                session_lists[cid] = lst
    except Exception:
        pass


def _normalize_create_items(parsed: List[Any]) -> List[Dict[str, Any]]:
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("items 必须为非空数组")
    items: List[Dict[str, Any]] = []
    for i, it in enumerate(parsed):
        if isinstance(it, dict) and "text" in it:
            items.append({"text": str(it["text"]).strip(), "done": bool(it.get("done", False))})
        elif isinstance(it, str) and it.strip():
            items.append({"text": it.strip(), "done": False})
        else:
            raise ValueError(f"items[{i}] 须为非空字符串或 {{text, done}} 对象")
    return items


def _as_int_list(v: Any, label: str) -> List[int]:
    if v is None:
        raise ValueError(f"缺少 {label}")
    if isinstance(v, int):
        return [v]
    if isinstance(v, list):
        if not v:
            raise ValueError(f"{label} 须为非空整数数组")
        return [int(x) for x in v]
    raise ValueError(f"{label} 须为 int 或非空 int 数组")


def execute(conversation_id: str, exec_args: Dict[str, Any]) -> dict:
    """宿主唯一入口：exec_args 使用扁平字段（与 OpenAPI 一致，蛇形命名）。"""
    cid = str(conversation_id or "").strip()
    action = str(exec_args.get("action") or "").strip().lower()
    if not action:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "缺少 action"}}

    _load(cid)

    if action == "create":
        raw_items = exec_args.get("items")
        if not isinstance(raw_items, list):
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "create 需要 items（字符串数组或对象数组）"}}
        try:
            items = _normalize_create_items(raw_items)
        except ValueError as e:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": str(e)}}
        lid = uuid.uuid4().hex[:12]
        session_lists[cid] = {"list_id": lid, "items": items, "collapsed": False}
        _persist(cid)
        return {"ok": True, "data": {"list_id": lid, "items": items, "collapsed": False}, "error": None}

    lst = session_lists.get(cid)
    if lst is None:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "当前对话无活跃清单，请先用 create"}}

    if action == "check":
        try:
            indices = _as_int_list(exec_args.get("indices"), "indices")
        except ValueError as e:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": str(e)}}
        for idx in indices:
            if idx < 0 or idx >= len(lst["items"]):
                return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"indices 含越界下标 {idx}，共 {len(lst['items'])} 项"}}
            lst["items"][idx]["done"] = True
        _persist(cid)
        return {"ok": True, "data": {"list_id": lst["list_id"], "items": lst["items"], "checked": indices}, "error": None}

    if action == "uncheck":
        try:
            indices = _as_int_list(exec_args.get("indices"), "indices")
        except ValueError as e:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": str(e)}}
        for idx in indices:
            if idx < 0 or idx >= len(lst["items"]):
                return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"indices 含越界下标 {idx}，共 {len(lst['items'])} 项"}}
            lst["items"][idx]["done"] = False
        _persist(cid)
        return {"ok": True, "data": {"list_id": lst["list_id"], "items": lst["items"], "unchecked": indices}, "error": None}

    if action == "add_item":
        text = str(exec_args.get("text") or "").strip()
        if not text:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "add_item 需要 text"}}
        ins = exec_args.get("item_index")
        item = {"text": text, "done": False}
        if ins is not None and isinstance(ins, int) and 0 <= ins <= len(lst["items"]):
            lst["items"].insert(ins, item)
        else:
            lst["items"].append(item)
        _persist(cid)
        return {"ok": True, "data": {"list_id": lst["list_id"], "items": lst["items"]}, "error": None}

    if action == "remove_item":
        raw_ix = exec_args.get("item_index")
        if raw_ix is None:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "remove_item 需要 item_index（int）"}}
        try:
            ix = int(raw_ix)
        except (TypeError, ValueError):
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "remove_item 需要 item_index（int）"}}
        if ix < 0 or ix >= len(lst["items"]):
            return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"item_index {ix} 越界，共 {len(lst['items'])} 项"}}
        lst["items"].pop(ix)
        _persist(cid)
        return {"ok": True, "data": {"list_id": lst["list_id"], "items": lst["items"]}, "error": None}

    if action == "replace_item":
        raw_ix = exec_args.get("item_index")
        if raw_ix is None:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "replace_item 需要 item_index（int）"}}
        try:
            ix = int(raw_ix)
        except (TypeError, ValueError):
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "replace_item 需要 item_index（int）"}}
        text = str(exec_args.get("text") or "").strip()
        if ix < 0 or ix >= len(lst["items"]):
            return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"item_index {ix} 越界，共 {len(lst['items'])} 项"}}
        if not text:
            return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "replace_item 需要 text"}}
        lst["items"][ix]["text"] = text
        _persist(cid)
        return {"ok": True, "data": {"list_id": lst["list_id"], "items": lst["items"]}, "error": None}

    if action == "collapse":
        lst["collapsed"] = not lst.get("collapsed", False)
        _persist(cid)
        return {"ok": True, "data": {"list_id": lst["list_id"], "items": lst["items"], "collapsed": lst["collapsed"]}, "error": None}

    if action == "close":
        session_lists.pop(cid, None)
        _persist(cid)
        return {"ok": True, "data": {"close": True}, "error": None}

    if action == "query":
        _load(cid)
        cur = session_lists.get(cid)
        if cur is None:
            return {"ok": True, "data": None, "error": None}
        return {"ok": True, "data": list(cur["items"]), "error": None}

    return {"ok": False, "data": None, "error": {"type": "ValueError", "message": f"未知 action: {action}"}}


def agent_main(
    *,
    action: str = "",
    items: Any = None,
    indices: Any = None,
    item_index: Any = None,
    text: str = "",
    conversation_id: str = "",
    json_out: bool = False,
    **_kwargs: Any,
) -> dict:
    """与 catalog / OpenAPI 一致的扁平参数；宿主经 execute(conversation_id, exec_args) 调用。"""
    _ = json_out
    cid = str(conversation_id or _kwargs.get("conversation_id") or "").strip()
    if not cid:
        return {
            "ok": False,
            "data": None,
            "error": {
                "type": "RuntimeError",
                "message": "todo_list 须由宿主传入 conversation_id",
            },
        }
    exec_args: Dict[str, Any] = {}
    if action:
        exec_args["action"] = action
    if items is not None:
        exec_args["items"] = items
    if indices is not None:
        exec_args["indices"] = indices
    if item_index is not None:
        exec_args["item_index"] = item_index
    if text:
        exec_args["text"] = text
    for k, v in _kwargs.items():
        if k in ("conversation_id", "run_type", "step_title") or v is None:
            continue
        if k not in exec_args:
            exec_args[k] = v
    return execute(cid, exec_args)






