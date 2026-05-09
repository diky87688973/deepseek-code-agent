# -*- coding: utf-8 -*-
"""在当前解释器进程内执行一段 Python 源码（不经 shell），用于胶水编排。"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path

import agent_common as ac
import stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

from tool_help_share import capture_help, HelpfulParser


def build_parser() -> argparse.ArgumentParser:
    p = HelpfulParser(description="执行内联 Python 源码（进程内 exec，不经 shell）")
    p.add_argument("--code", required=True, help="Python 源码字符串")
    p.add_argument("--cwd", help="执行前切换工作目录")
    p.add_argument("--timeoutSec", type=int, default=300, help="保留参数；进程内 exec 无法在时限点可靠中断")
    p.add_argument(
        "--restrictToWorkspace",
        action="store_true",
        help="cwd 限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--jsonOut", action="store_true", help="向 stdout 输出 JSON 信封")
    p.add_argument("--outFile", help="同时将 JSON 结果写入该文件")
    p.add_argument("--runType", choices=["auto", "plan", "execute"], default="", help="plan 时拒绝执行")
    return p


def _build_error(*, code: str, message: str, exit_code: int | None, hint: str, retryable: bool) -> dict:
    return {
        "code": code,
        "type": "PythonInlineError",
        "message": message,
        "exitCode": exit_code,
        "hint": hint,
        "retryable": retryable,
    }


def _emit_envelope_file_and_stdout(args: argparse.Namespace, envelope: dict) -> None:
    if args.outFile:
        out = Path(args.outFile)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.jsonOut or not args.outFile:
        print(json.dumps(envelope, ensure_ascii=False))


def _forbid_inline_search(code: str) -> bool:
    """禁止在胶水代码里绕过宿主调度搜索类工具（须走服务端线程+进度）。"""
    s = code or ""
    return "file_search" in s or "grep_files" in s


def agent_main(
    *,
    code: str,
    cwd: str | None = None,
    timeout_sec: int = 300,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    parser_for_help: argparse.ArgumentParser | None = None,
) -> dict:
    """返回 {ok, data:{ok, stdout, stderr, exitCode, timeout}, error}。"""
    _ = timeout_sec
    try:
        rt = str(run_type or "").strip().lower()
        if rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许执行代码"}}

        if _forbid_inline_search(code or ""):
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "Forbidden",
                    "message": "禁止在 python_inline 中调度 file_search/grep_files！请直接使用对应工具的 function call（走服务端线程+进度）。",
                },
            }

        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        prev_cwd: str | None = None

        try:
            if cwd:
                cp = ac.resolve_path(cwd, allow_outside_workspace=not restrict_to_workspace)
                if not cp.is_dir():
                    raise ValueError(f"cwd 不是目录: {cp}")
                prev_cwd = os.getcwd()
                os.chdir(cp)

            with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
                exec(code, {"__name__": "__python_inline__"})  # noqa: S102
            sub_ok = True
            exit_code = 0
        except Exception as e:
            captured_stderr.write(f"\n{type(e).__name__}: {e}")
            sub_ok = False
            exit_code = 1
        finally:
            if prev_cwd is not None:
                try:
                    os.chdir(prev_cwd)
                except OSError:
                    pass

        data = {
            "ok": sub_ok,
            "exitCode": exit_code,
            "stdout": captured_stdout.getvalue(),
            "stderr": captured_stderr.getvalue(),
            "timeout": False,
        }
        error = None
        if not sub_ok:
            error = _build_error(
                code="E_PY_INLINE_FAILED",
                message=data["stderr"].strip().split("\n")[-1] if data["stderr"] else "exec failed",
                exit_code=exit_code,
                hint="检查 code 语法与依赖",
                retryable=False,
            )
        return {"ok": sub_ok, "data": data, "error": error}
    except Exception as e:
        data = {"ok": False, "exitCode": None, "stdout": "", "stderr": str(e), "timeout": False}
        error = _build_error(
            code="E_INVALID",
            message=str(e),
            exit_code=None,
            hint="检查 cwd 与参数",
            retryable=False,
        )
        if parser_for_help is not None:
            full = str(data.get("stderr", "")) + "\n\n--help:\n" + capture_help(parser_for_help)
            data = {**data, "stderr": full}
            error = {**error, "message": full}
        return {"ok": False, "data": data, "error": error}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.timeoutSec <= 0:
        raise ValueError("timeoutSec 必须 > 0")
    env = agent_main(
        code=args.code,
        cwd=str(args.cwd) if args.cwd else None,
        timeout_sec=int(args.timeoutSec),
        restrict_to_workspace=bool(getattr(args, "restrictToWorkspace", False)),
        run_type=str(getattr(args, "runType", "") or "").strip().lower(),
        parser_for_help=parser,
    )
    _emit_envelope_file_and_stdout(args, env)


if __name__ == "__main__":
    main()
