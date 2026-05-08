#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行模式管理：查询当前模式（无参）或请求切换模式（--runType）。字段名统一用 runType。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from cli_help_share import _capture_help, _HelpFulParser


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="运行模式管理：不传参则查询当前模式；传 --runType 则请求切换（服务端内联执行）")
    p.add_argument(
        "--runType",
        required=False,
        default=None,
        choices=("auto", "plan", "execute"),
        help="目标模式：auto 清除会话模式锁；plan/execute 显式锁定。不传则查询当前模式",
    )
    p.add_argument("--jsonOut", action="store_true", help="输出统一 JSON 行")
    return p


def agent_main(run_type: str = None) -> dict:
    """进程内入口。
    - run_type 为 None：查询当前模式（环境变量 AGENT_RUN_TYPE，默认 auto）
    - run_type 有值：请求切换模式（由服务端内联处理）
    """
    if run_type is not None:
        if run_type not in ("auto", "plan", "execute"):
            return {"ok": False, "error": {"message": f"runType 非法: {run_type}"}}
        return {"ok": True, "data": {"runType": run_type, "action": "switch"}}
    mode = os.environ.get("AGENT_RUN_TYPE", "auto").strip().lower()
    if mode not in ("auto", "plan", "execute"):
        mode = "auto"
    return {"ok": True, "data": {"runType": mode, "action": "query"}}


def main() -> None:
    parser = build_parser()
    try:
        args = parser.parse_args()
        row = agent_main(run_type=args.runType)
        print(json.dumps(row, ensure_ascii=False))
        if not row.get("ok"):
            sys.exit(1)
    except SystemExit:
        raise
    except ValueError as e:
        print(json.dumps({"ok": False, "error": {"message": str(e)}}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        e.args = (str(e) + "\n\n--help:\n" + _capture_help(parser),)
        print(json.dumps({"ok": False, "error": {"message": str(e)}}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
