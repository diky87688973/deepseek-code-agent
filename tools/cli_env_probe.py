# -*- coding: utf-8 -*-
"""
命令环境探测：识别本机可用命令行模式并给出推荐。
"""

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import shutil
import sys

from pathlib import Path
from cli_help_share import _HelpFulParser


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="探测本机可用命令行模式")
    p.add_argument("--jsonOut", action="store_true", help="输出 JSON")
    return p


def _which(name: str) -> str:
    p = shutil.which(name)
    return p or ""


def _emit(ok: bool, data: dict, error: dict | None) -> None:
    print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))


def agent_main() -> dict:
    """进程内入口；返回统一 JSON 信封（字典），不打印。"""
    candidates = [
        ("cmd", "cmd.exe"),
        ("powershell", "powershell.exe"),
        ("pwsh", "pwsh.exe"),
        ("bash", "bash.exe"),
        ("python", "python.exe"),
        ("py", "py.exe"),
    ]
    available = []
    for key, exe in candidates:
        path = _which(exe)
        if path:
            available.append({"mode": key, "executable": exe, "path": path})

    recommendations = [
        "文本工具优先直接调用 cli_*.py（最稳定）",
        "复杂引号/多行 JSON 场景优先 python/py + stdin 传参",
    ]
    if any(x["mode"] == "cmd" for x in available):
        recommendations.append("Windows 下命令拼接兼容性优先 cmd")
    if any(x["mode"] == "powershell" for x in available):
        recommendations.append("PowerShell 可用，但复杂嵌套转义成本更高")

    data = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "cwd": str(Path.cwd()),
        "available": available,
        "pycharmTerminalNote": "PyCharm 终端本质仍调用系统 shell（如 powershell/cmd），可满足 pip 与常规命令执行。",
        "recommendations": recommendations,
    }
    return {"ok": True, "data": data, "error": None}


def main() -> None:
    _ = build_parser().parse_args()
    env = agent_main()
    _emit(env["ok"], env["data"], env["error"])


if __name__ == "__main__":
    main()
