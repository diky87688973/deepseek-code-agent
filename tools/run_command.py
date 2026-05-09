# -*- coding: utf-8 -*-
"""在 shell 中执行命令（结构化 stdout/stderr）。新链路的命令入口。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import agent_common as ac

from command_safety import (
    _check_command_blacklist,
    _decode_output,
    _resolve_shell_executable,
    _validate_safe_command,
)


def agent_main(
    *,
    command: str,
    cwd: str | None = None,
    timeout_sec: int = 300,
    shell: str = "auto",
    safe_mode: bool = True,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    try:
        rt = str(run_type or "").strip().lower()
        if rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许执行命令"}}

        block_reason = _check_command_blacklist(command)
        if block_reason is not None:
            return {"ok": False, "data": None, "error": {"type": "CommandBlacklisted", "message": block_reason}}

        if safe_mode:
            _validate_safe_command(command)

        cwd_resolved: str | None = None
        if cwd:
            cp = ac.resolve_path(cwd, allow_outside_workspace=not restrict_to_workspace)
            if not cp.is_dir():
                raise ValueError(f"cwd 不是目录: {cp}")
            cwd_resolved = str(cp)

        selected_shell, shell_executable = _resolve_shell_executable(shell, command)
        cp = subprocess.run(
            command,
            shell=True,
            executable=shell_executable,
            cwd=cwd_resolved,
            text=False,
            capture_output=True,
            timeout=timeout_sec,
        )
        ok_sub = cp.returncode == 0
        data = {
            "exitCode": cp.returncode,
            "stdout": _decode_output(cp.stdout),
            "stderr": _decode_output(cp.stderr),
            "timeout": False,
            "selectedShell": selected_shell,
        }
        return {"ok": ok_sub, "data": data, "error": None if ok_sub else {"type": "CommandFailed", "message": "命令非零退出"}}
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "data": {
                "exitCode": -1,
                "stdout": _decode_output(e.stdout),
                "stderr": _decode_output(e.stderr),
                "timeout": True,
            },
            "error": {"type": "TimeoutError", "message": f"命令超时（{timeout_sec}s）"},
        }
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e)}}


def main() -> None:
    import argparse
    import json

    p = argparse.ArgumentParser(description="run_command")
    p.add_argument("--command", required=True)
    p.add_argument("--cwd", default=None)
    p.add_argument("--timeoutSec", type=int, default=300)
    p.add_argument("--shell", choices=["auto", "cmd", "powershell"], default="auto")
    p.add_argument("--safeMode", action="store_true", default=True)
    p.add_argument("--no-safeMode", dest="safeMode", action="store_false")
    p.add_argument(
        "--restrictToWorkspace",
        action="store_true",
        help="cwd 限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--runType", default="")
    p.add_argument("--jsonOut", action="store_true")
    args = p.parse_args()
    r = agent_main(
        command=args.command,
        cwd=args.cwd,
        timeout_sec=args.timeoutSec,
        shell=args.shell,
        safe_mode=args.safeMode,
        restrict_to_workspace=bool(getattr(args, "restrictToWorkspace", False)),
        run_type=str(args.runType or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
