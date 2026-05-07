#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可选：用户明确要求由模型代为切换 Auto/Plan/Execute 时调用；由 code_web_agent 内联处理并更新会话。"""
from __future__ import annotations

import argparse
import json
import sys
from cli_help_share import _capture_help, _HelpFulParser



def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="请求切换会话运行模式（仅作契约；服务端内联执行）")
    p.add_argument(
        "--runType",
        required=True,
        choices=("auto", "plan", "execute"),
        help="目标模式：auto 清除会话模式锁；plan/execute 显式锁定",
    )
    p.add_argument("--jsonOut", action="store_true", help="输出统一 JSON 行")
    return p


def agent_main(*, run_type: str) -> dict:
    """进程内入口；返回与 CLI 成功/失败打印一致的字典。"""
    if run_type not in ("auto", "plan", "execute"):
        return {"ok": False, "error": {"message": f"runType 非法: {run_type}"}}
    return {"ok": True, "data": {"runType": run_type}}


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