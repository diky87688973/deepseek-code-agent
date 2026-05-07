# -*- coding: utf-8 -*-
"""
CLI Todo-List 工具
==================

用途
----
创建/勾选/取消勾选执行清单（Todo List）。清单状态在 CLI 进程内维护，
每个 conversation 独立。action=create 创建新清单并返回全部状态；
check/uncheck 更新单项状态。

action
------
- create：创建新清单，必须提供 --items（JSON 数组字符串）
- check：勾选一项，必须提供 --listId 和 --itemIndex
- uncheck：取消勾选一项，必须提供 --listId 和 --itemIndex
- collapse：折叠/展开清单，须提供 --listId（仅发送信号，不修改清单数据）
- close：关闭清单，须提供 --listId（发送关闭信号，前端隐藏清单区域）
- query：查询状态；无活跃清单时 data 为 null，有则 data 为 items 数组。勿用 close 探测

输出
----
统一 JSON {ok, data, error}。create 等返回含 listId 的对象；action=query 时无活跃清单为 data:null，有则为 items 数组 [{text, done}, ...]。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import sys
import uuid

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser


# 全局状态（进程内，每个 conversation 可建多个 listId）
_TODO_LISTS: dict[str, dict] = {}


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(
        description="执行清单管理：create / check / uncheck / query",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--action",
        required=True,
        choices=["create", "check", "uncheck", "collapse", "close", "query"],
        help="create / check / uncheck / collapse / close / query（查询当前清单 items，无则 data 为 null）",
    )
    p.add_argument(
        "--items",
        type=str,
        default="",
        help='create 时必填：JSON 字符串数组，如 ["步骤1","步骤2"]',
    )
    p.add_argument(
        "--itemIndex",
        type=int,
        default=-1,
        help="check/uncheck 时必填：项目索引（0-based）",
    )
    p.add_argument(
        "--listId",
        type=str,
        default="",
        help="清单 ID；create 时自动生成；check/uncheck/collapse/close 必填；query 在仅有一个清单时可省略（仅 CLI 多清单时需指定）",
    )
    p.add_argument("--jsonOut", action="store_true", help="输出统一 JSON")
    return p


def _gen_list_id() -> str:
    return uuid.uuid4().hex[:12]


def _do_create(items_raw: str) -> dict:
    if not items_raw or not items_raw.strip():
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "create 需要 --items (JSON 数组)"}}
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
    lid = _gen_list_id()
    _TODO_LISTS[lid] = {"listId": lid, "items": items, "collapsed": False}
    return {"ok": True, "data": {"listId": lid, "items": items, "collapsed": False}, "error": None}


def _do_check(list_id: str, item_index: int) -> dict:
    lst = _TODO_LISTS.get(list_id)
    if lst is None:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": f"listId 不存在: {list_id}"}}
    if item_index < 0 or item_index >= len(lst["items"]):
        return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"itemIndex {item_index} 越界，共 {len(lst['items'])} 项"}}
    lst["items"][item_index]["done"] = True
    return {"ok": True, "data": {"listId": list_id, "items": lst["items"]}, "error": None}


def _do_uncheck(list_id: str, item_index: int) -> dict:
    lst = _TODO_LISTS.get(list_id)
    if lst is None:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": f"listId 不存在: {list_id}"}}
    if item_index < 0 or item_index >= len(lst["items"]):
        return {"ok": False, "data": None, "error": {"type": "IndexError", "message": f"itemIndex {item_index} 越界，共 {len(lst['items'])} 项"}}
    lst["items"][item_index]["done"] = False
    return {"ok": True, "data": {"listId": list_id, "items": lst["items"]}, "error": None}


def _do_collapse(list_id: str) -> dict:
    lst = _TODO_LISTS.get(list_id)
    if lst is None:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": f"listId 不存在: {list_id}"}}
    # 切换折叠状态
    lst["collapsed"] = not lst.get("collapsed", False)
    return {"ok": True, "data": {"listId": list_id, "items": lst["items"], "collapsed": lst["collapsed"]}, "error": None}


def _do_close(list_id: str) -> dict:
    lst = _TODO_LISTS.get(list_id)
    if lst is None:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": f"listId 不存在: {list_id}"}}
    return {"ok": True, "data": {"listId": list_id, "items": lst["items"], "close": True}, "error": None}


def _do_query(list_id: str) -> dict:
    """无活跃清单：ok、data null；有则返回当前 items 数组（Code Agent 会按会话覆盖此逻辑）。"""
    lid = str(list_id or "").strip()
    if not _TODO_LISTS:
        return {"ok": True, "data": None, "error": None}
    if lid:
        lst = _TODO_LISTS.get(lid)
        if lst is None:
            return {"ok": True, "data": None, "error": None}
        return {"ok": True, "data": list(lst["items"]), "error": None}
    if len(_TODO_LISTS) == 1:
        lst = next(iter(_TODO_LISTS.values()))
        return {"ok": True, "data": list(lst["items"]), "error": None}
    return {
        "ok": False,
        "data": None,
        "error": {"type": "ValueError", "message": "CLI 进程内存在多个清单，请指定 --listId 再查询"},
    }


def agent_main(
    *,
    action: str,
    items: str = "",
    item_index: int = -1,
    list_id: str = "",
) -> dict:
    """进程内入口；返回 {ok,data,error} 字典。"""
    if action == "create":
        return _do_create(items)
    elif action == "check":
        return _do_check(list_id, item_index)
    elif action == "uncheck":
        return _do_uncheck(list_id, item_index)
    elif action == "collapse":
        return _do_collapse(list_id)
    elif action == "close":
        return _do_close(list_id)
    elif action == "query":
        return _do_query(list_id)
    else:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": f"未知 action: {action}"}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    res = agent_main(
        action=args.action,
        items=args.items,
        item_index=args.itemIndex,
        list_id=args.listId,
    )
    if args.jsonOut:
        print(json.dumps(res, ensure_ascii=False))
    else:
        if res.get("ok"):
            print("ok")
            print(json.dumps(res["data"], ensure_ascii=False))
        else:
            err = res.get("error") or {}
            print(str(err.get("message", "")), file=sys.stderr)


if __name__ == "__main__":
    main()
