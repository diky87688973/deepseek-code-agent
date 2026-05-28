# -*- coding: utf-8 -*-
"""在 shell 中执行命令（结构化 stdout/stderr）。支持宿主 _progress_dict 实时推送输出。"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import agent_common as ac

from command_safety import (
    _check_command_blacklist,
    _decode_output,
    _resolve_shell_executable,
    _validate_safe_command,
    kill_shell_process_tree,
    register_shell_process,
    sanitize_command_output_for_display,
    unregister_shell_process,
    STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS,
    STREAM_OUTPUT_TAIL_MAX_CHARS,
)

_INTERACTIVE_PROMPT_RE = re.compile(
    r"(?:\[Y\]\s*是\s*\[N\]\s*否|是否同意所有源协议|\(Y/N\)|Continue\?\s*\[y/n\]|需要确认)",
    re.I,
)
_INPUT_WAIT_SEC = 180.0
def _detect_awaiting_input(text_tail: str) -> Optional[Dict[str, Any]]:
    tail = (text_tail or "")[-1500:]
    if not _INTERACTIVE_PROMPT_RE.search(tail):
        return None
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()][-10:]
    title = "\n".join(lines) if lines else "命令正在等待确认"
    return {"title": title[:800], "options": ["Y", "N"]}


def _run_shell_streaming(
    *,
    command: str,
    shell_executable: Optional[str],
    cwd_resolved: str,
    timeout_sec: int,
    selected_shell: str,
    progress: Optional[Dict[str, Any]],
) -> dict:
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    proc = subprocess.Popen(
        command,
        shell=True,
        executable=shell_executable,
        cwd=cwd_resolved,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    shell_scope = str((progress or {}).get("_shell_scope") or "").strip()
    from command_safety import _DEFAULT_SHELL_SCOPE

    shell_scope = shell_scope or _DEFAULT_SHELL_SCOPE
    register_shell_process(proc.pid, shell_scope)
    stdout_parts: List[bytes] = []
    stderr_parts: List[bytes] = []
    buf_lock = threading.Lock()
    start = time.monotonic()

    def _sync_progress() -> None:
        if not isinstance(progress, dict):
            return
        with buf_lock:
            out_s = _decode_output(b"".join(stdout_parts))
            err_s = _decode_output(b"".join(stderr_parts))
        combined = out_s + (("\n" + err_s) if err_s.strip() else "")
        progress["phase"] = "run_command"
        progress["stdout_tail"] = sanitize_command_output_for_display(
            combined, max_chars=STREAM_OUTPUT_TAIL_MAX_CHARS
        )
        progress["stderr_tail"] = sanitize_command_output_for_display(
            err_s, max_chars=STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS
        )
        progress["elapsed_sec"] = max(0, int(time.monotonic() - start))
        progress["_seq"] = int(progress.get("_seq") or 0) + 1
        if not progress.get("awaiting_input"):
            hit = _detect_awaiting_input(combined)
            if hit:
                progress["awaiting_input"] = hit
                progress["_awaiting_since"] = time.monotonic()

    def _reader(pipe: Any, *, is_err: bool) -> None:
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                with buf_lock:
                    if is_err:
                        stderr_parts.append(chunk)
                    else:
                        stdout_parts.append(chunk)
                _sync_progress()
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    t_out = threading.Thread(target=_reader, args=(proc.stdout,), kwargs={"is_err": False}, daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr,), kwargs={"is_err": True}, daemon=True)
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        while proc.poll() is None:
            if ac.progress_abort_requested(progress):
                kill_shell_process_tree(proc.pid)
                try:
                    proc.kill()
                except OSError:
                    pass
                break
            elapsed = time.monotonic() - start
            if elapsed > float(timeout_sec):
                timed_out = True
                kill_shell_process_tree(proc.pid)
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    kill_shell_process_tree(proc.pid)
                break
            if isinstance(progress, dict) and progress.get("_user_input") is not None:
                raw_in = str(progress.pop("_user_input", "") or "").strip()
                progress.pop("awaiting_input", None)
                progress.pop("_awaiting_since", None)
                if raw_in and proc.stdin:
                    try:
                        proc.stdin.write((raw_in + "\n").encode("utf-8", errors="replace"))
                        proc.stdin.flush()
                    except OSError:
                        pass
                _sync_progress()
            elif isinstance(progress, dict) and progress.get("awaiting_input"):
                since = float(progress.get("_awaiting_since") or start)
                if time.monotonic() - since > _INPUT_WAIT_SEC:
                    progress.pop("awaiting_input", None)
                    progress.pop("_awaiting_since", None)
            time.sleep(0.12)

        t_out.join(timeout=3)
        t_err.join(timeout=3)
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kill_shell_process_tree(proc.pid)

        with buf_lock:
            out_s = _decode_output(b"".join(stdout_parts))
            err_s = _decode_output(b"".join(stderr_parts))

        if isinstance(progress, dict):
            progress.pop("awaiting_input", None)
            _sync_progress()

        if timed_out:
            return {
                "ok": False,
                "data": {
                    "exit_code": -1,
                    "stdout": out_s,
                    "stderr": err_s,
                    "timeout": True,
                    "selected_shell": selected_shell,
                },
                "error": {
                    "type": "TimeoutError",
                    "message": f"命令超时（{timeout_sec}s），已尝试结束进程树",
                },
            }

        ok_sub = proc.returncode == 0
        data = {
            "exit_code": proc.returncode,
            "stdout": out_s,
            "stderr": err_s,
            "timeout": False,
            "selected_shell": selected_shell,
        }
        err = None
        if not ok_sub:
            msg = "命令非零退出"
            if "0x8a150042" in out_s + err_s or "读取输入时出错" in out_s + err_s:
                msg = (
                    "命令需要交互确认但当前无控制台；请改用非交互参数"
                    "（如 winget 加 --accept-package-agreements --accept-source-agreements），"
                    "或在步骤卡片上点击 Y/N 后再执行"
                )
            err = {"type": "CommandFailed", "message": msg}
        return {"ok": ok_sub, "data": data, "error": err}
    finally:
        unregister_shell_process(shell_scope)


def agent_main(
    *,
    command: str,
    cwd: Optional[str] = None,
    timeout_sec: int = 300,
    shell: str = "auto",
    safe_mode: bool = True,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    _progress_dict: Optional[dict] = None,
) -> dict:
    progress = _progress_dict if isinstance(_progress_dict, dict) else None
    try:
        rt = str(run_type or "").strip().lower()
        if rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许执行命令"}}

        block_reason = _check_command_blacklist(command)
        if block_reason is not None:
            return {"ok": False, "data": None, "error": {"type": "CommandBlacklisted", "message": block_reason}}

        import re as _re

        _py_files_in_cmd = _re.findall(r'[\'"]?([a-zA-Z]:[^\'"\s]+\.py)[\'"]?', command)
        if not _py_files_in_cmd:
            _py_files_in_cmd = _re.findall(r'([^\'"\s]+\.py)', command)
        for _pyf in _py_files_in_cmd:
            _pyf = _pyf.strip('\'" ')
            try:
                _py_content = Path(_pyf).read_text("utf-8", errors="replace")
                _file_block = _check_command_blacklist(_py_content)
                if _file_block is not None:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "type": "CommandBlacklisted",
                            "message": f"脚本文件 {_pyf} 包含被拦截内容：{_file_block}",
                        },
                    }
            except Exception:
                pass

        if safe_mode:
            _validate_safe_command(command)

        cwd_resolved: Optional[str] = None
        if cwd:
            cp = ac.resolve_path(cwd, allow_outside_workspace=not restrict_to_workspace)
            if not cp.is_dir():
                raise ValueError(f"cwd 不是目录: {cp}")
            cwd_resolved = str(cp)
        else:
            cwd_resolved = str(ac.workspace_root())

        try:
            timeout_sec = int(timeout_sec)
        except (TypeError, ValueError):
            timeout_sec = 300
        timeout_sec = max(5, min(timeout_sec, 3600))

        if progress is not None:
            progress["phase"] = "run_command"
            progress["stdout_tail"] = ""
            progress["stderr_tail"] = ""
            progress["elapsed_sec"] = 0
            progress["_seq"] = 0

        selected_shell, shell_executable = _resolve_shell_executable(shell, command)
        return _run_shell_streaming(
            command=command,
            shell_executable=shell_executable,
            cwd_resolved=cwd_resolved,
            timeout_sec=timeout_sec,
            selected_shell=selected_shell,
            progress=progress,
        )
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e)}}


def main() -> None:
    import argparse
    import json

    p = argparse.ArgumentParser(description="run_command")
    p.add_argument("--command", required=True)
    p.add_argument("--cwd", default=None, help="执行目录（默认：工作区根目录）")
    p.set_defaults(safe_mode=True)
    p.add_argument("--timeout_sec", type=int, default=300)
    p.add_argument("--shell", choices=["auto", "cmd", "powershell"], default="auto")
    p.add_argument("--safe_mode", dest="safe_mode", action="store_true")
    p.add_argument("--no_safe_mode", dest="safe_mode", action="store_false")
    p.add_argument(
        "--restrict_to_workspace",
        action="store_true",
        help="cwd 限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--run_type", default="")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(
        command=args.command,
        cwd=args.cwd,
        timeout_sec=args.timeout_sec,
        shell=args.shell,
        safe_mode=bool(args.safe_mode),
        restrict_to_workspace=bool(args.restrict_to_workspace),
        run_type=str(args.run_type or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
