# -*- coding: utf-8 -*-
"""
以当前解释器执行一段 Python 源码（等价于 ``python -c CODE``），不经 shell。
用于 agent 胶水脚本：避免 Windows 引号与 cli_command_exec.safeMode 对分号的拦截。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import subprocess
import sys

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser


def build_parser():
    p = _HelpFulParser(description="执行内联 Python 源码（python -c）")
    p.add_argument("--code", required=True, help="传给 python -c 的源码字符串")
    p.add_argument("--cwd", help="子进程工作目录")
    p.add_argument("--timeoutSec", type=int, default=300, help="超时秒数，默认 300")
    p.add_argument("--jsonOut", action="store_true", help="向 stdout 输出 JSON")
    p.add_argument("--outFile", help="同时将 JSON 结果写入该文件")
    p.add_argument("--runType", choices=["auto", "plan", "execute"], default="", help="当前运行模式；plan 时操作被拒绝")
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


def _decode_output(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "gb18030", "gbk", "cp936"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _emit_envelope_file_and_stdout(args: argparse.Namespace, envelope: dict) -> None:
    if args.outFile:
        out = Path(args.outFile)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.jsonOut or not args.outFile:
        print(json.dumps(envelope, ensure_ascii=False))


def agent_main(
    *,
    code: str,
    cwd: str | None = None,
    timeout_sec: int = 300,
    run_type: str = "",
    parser_for_help: argparse.ArgumentParser | None = None,
) -> dict:
    """进程内入口；返回与 CLI 打印一致的外层 JSON 结构（字典）。parser_for_help 非空时，E_INVALID 会附加 --help。"""
    try:
        rt = str(run_type or "").strip().lower()
        if rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许执行代码"}}
        # 进程内直接 exec，不启动子进程（避免打包后 sys.executable=EXE 自身导致卡死）
        import io
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        try:
            import contextlib as _ctxlib
            with _ctxlib.redirect_stdout(captured_stdout), _ctxlib.redirect_stderr(captured_stderr):
                exec(code, {"__name__": "__cli_python_inline__"})
            sub_ok = True
            exit_code = 0
        except Exception as e:
            captured_stderr.write(f"\n{type(e).__name__}: {e}")
            sub_ok = False
            exit_code = 1
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
                message=data["stderr"].split("\n")[-1] if data["stderr"] else "exec failed",
                exit_code=exit_code,
                hint="检查 --code 语法与依赖",
                retryable=False,
            )
        return {"ok": sub_ok, "data": data, "error": error}
    except Exception as e:
        data = {"ok": False, "exitCode": None, "stdout": "", "stderr": str(e), "timeout": False}
        error = _build_error(
            code="E_INVALID",
            message=str(e),
            exit_code=None,
            hint="检查 --cwd 与参数",
            retryable=False,
        )
        if parser_for_help is not None:
            full = str(data.get("stderr", "")) + "\n\n--help:\n" + _capture_help(parser_for_help)
            data = {**data, "stderr": full}
            error = {**error, "message": full}
        return {"ok": False, "data": data, "error": error}


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.timeoutSec <= 0:
        raise ValueError("timeoutSec 必须 > 0")
    cwd = None
    if args.cwd:
        cwd = Path(args.cwd)
        if not cwd.exists() or not cwd.is_dir():
            raise ValueError(f"cwd 无效: {cwd}")
        cwd = str(cwd)
    env = agent_main(
        code=args.code,
        cwd=cwd,
        timeout_sec=args.timeoutSec,
        run_type=str(getattr(args, "runType", "") or "").strip().lower(),
        parser_for_help=parser,
    )
    _emit_envelope_file_and_stdout(args, env)


if __name__ == "__main__":
    main()
