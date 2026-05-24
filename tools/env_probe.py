# -*- coding: utf-8 -*-
"""探测本机可用命令行环境（shell/python 可执行文件），并给出使用建议。"""

from __future__ import annotations
from typing import Dict, List

import json
import shutil
import sys
from pathlib import Path

import agent_common as ac
from tool_help_share import HelpfulParser


def build_parser() -> argparse.ArgumentParser:
    p = HelpfulParser(description="探测本机可用命令行模式（cmd/powershell/bash/python 等）")
    p.add_argument("--json_out", action="store_true", help="向 stdout 输出统一 JSON {ok,data,error}")
    return p


def agent_main(*, run_type: str = "") -> dict:
    """扁平参数；无业务必填项。run_type 仅占位与清单一致。"""
    _ = run_type
    candidates = [
        ("cmd", "cmd.exe"),
        ("powershell", "powershell.exe"),
        ("pwsh", "pwsh.exe"),
        ("bash", "bash.exe"),
        ("python", "python.exe"),
        ("py", "py.exe"),
    ]
    available: List[Dict[str, str]] = []
    for key, exe in candidates:
        path = shutil.which(exe) or ""
        if path:
            available.append({"mode": key, "executable": exe, "path": path})

    recommendations = [
        "文件读写优先使用工作区内工具：read_file、write_file、grep_files 等（进程内调用）。",
        "复杂引号/多行内容优先 write_file 落盘或使用 run_command（低优先级，注意安全策略）。",
    ]
    if any(x["mode"] == "cmd" for x in available):
        recommendations.append("Windows 下简单命令拼接可优先 cmd。")
    if any(x["mode"] == "powershell" for x in available):
        recommendations.append("PowerShell 可用；深层嵌套转义成本较高，复杂场景优先专用工具。")

    data = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "cwd": str(ac.workspace_root()),
        "available": available,
        "pycharm_terminal_note": "IDE 终端仍调用系统 shell（如 powershell/cmd），常规 pip/命令均可使用。",
        "recommendations": recommendations,
    }
    return ac.ok(data)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    env = agent_main()
    if args.json_out:
        print(json.dumps(env, ensure_ascii=False))
    else:
        if env.get("ok") and isinstance(env.get("data"), dict):
            d = env["data"]
            print(json.dumps(d, ensure_ascii=False, indent=2))
        else:
            print((env.get("error") or {}).get("message", ""), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
