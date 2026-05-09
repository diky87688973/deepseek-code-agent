# -*- coding: utf-8 -*-
"""会话运行模式：查询/切换 Auto、Plan、Execute。

实际会话状态由 **宿主进程** 内联维护（见 deepseek `_execute_run_type`）。
本模块提供与 `tool_list_agent.json` 一致的 `agent_main` 签名供进程内校验；CLI 仅作调试。"""

from __future__ import annotations

import argparse
import json
import sys

import agent_common as ac


def agent_main(
    *,
    run_type: str | None = None,
) -> dict:
    """不传 `run_type` 表示查询意图；传 `auto|plan|execute` 表示切换请求（由宿主落盘）。"""
    if run_type is None or str(run_type).strip() == "":
        return ac.ok({"action": "query", "hint": "由宿主返回当前 CONVERSATION_MODES"})
    rt = str(run_type).strip().lower()
    if rt not in ("auto", "plan", "execute"):
        return ac.err(ValueError(f"run_type 须为 auto/plan/execute，收到: {run_type!r}"))
    return ac.ok({"runType": rt, "action": "switch"})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="run_type：CLI 防腐层；宿主内联执行时走 _execute_run_type")
    p.add_argument("--runType", default=None)
    p.add_argument("--jsonOut", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    r = agent_main(run_type=args.runType)
    if args.jsonOut:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok"):
            print(json.dumps(r.get("data"), ensure_ascii=False))
        else:
            print((r.get("error") or {}).get("message", ""), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
