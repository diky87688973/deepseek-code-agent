# -*- coding: utf-8 -*-
"""在当前解释器进程内执行一段 Python 源码（不经 shell），用于胶水编排。"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import agent_common as ac
import stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

from tool_help_share import capture_help, HelpfulParser


def build_parser() -> argparse.ArgumentParser:
    p = HelpfulParser(description="执行内联 Python 源码（进程内 exec，不经 shell）")
    p.add_argument("--code", required=True, help="Python 源码字符串")
    p.add_argument("--cwd", help="执行前切换工作目录（默认：工作区根目录）")
    p.add_argument("--timeout_sec", type=int, default=300, help="保留参数；进程内 exec 无法在时限点可靠中断")
    p.add_argument(
        "--restrict_to_workspace",
        action="store_true",
        help="cwd 限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--json_out", action="store_true", help="向 stdout 输出 JSON 信封")
    p.add_argument("--out_file", help="同时将 JSON 结果写入该文件")
    p.add_argument("--run_type", choices=["auto", "plan", "execute"], default="", help="plan 时拒绝执行")
    return p


def _build_error(*, code: str, message: str, exit_code: Optional[int], hint: str, retryable: bool) -> dict:
    return {
        "code": code,
        "type": "PythonInlineError",
        "message": message,
        "exit_code": exit_code,
        "hint": hint,
        "retryable": retryable,
    }


def _emit_envelope_file_and_stdout(args: argparse.Namespace, envelope: dict) -> None:
    if args.out_file:
        out = Path(args.out_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json_out or not args.out_file:
        print(json.dumps(envelope, ensure_ascii=False))


def _sync_inline_progress(
    progress: Optional[Dict[str, Any]],
    *,
    stdout_parts: List[str],
    stderr_parts: List[str],
    start: float,
) -> None:
    if not isinstance(progress, dict):
        return
    from command_safety import (
        sanitize_command_output_for_display,
        STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS,
        STREAM_OUTPUT_TAIL_MAX_CHARS,
    )

    out_s = "".join(stdout_parts)
    err_s = "".join(stderr_parts)
    combined = out_s + (("\n" + err_s) if err_s.strip() else "")
    progress["phase"] = "python_inline"
    progress["stdout_tail"] = sanitize_command_output_for_display(
        combined, max_chars=STREAM_OUTPUT_TAIL_MAX_CHARS
    )
    progress["stderr_tail"] = sanitize_command_output_for_display(
        err_s, max_chars=STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS
    )
    progress["elapsed_sec"] = max(0, int(time.monotonic() - start))
    progress["_seq"] = int(progress.get("_seq") or 0) + 1


class _InlineProgressWriter(io.TextIOBase):
    """将 print / stderr 写入 progress，供宿主推送 tool_progress。"""

    def __init__(
        self,
        parts: List[str],
        progress: Optional[Dict[str, Any]],
        *,
        peer_parts: List[str],
        start: float,
        is_stderr: bool,
    ) -> None:
        self._parts = parts
        self._peer = peer_parts
        self._progress = progress
        self._start = start
        self._is_stderr = is_stderr

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._parts.append(s)
        _sync_inline_progress(
            self._progress,
            stdout_parts=self._peer if self._is_stderr else self._parts,
            stderr_parts=self._parts if self._is_stderr else self._peer,
            start=self._start,
        )
        return len(s)

    def flush(self) -> None:
        _sync_inline_progress(
            self._progress,
            stdout_parts=self._peer if self._is_stderr else self._parts,
            stderr_parts=self._parts if self._is_stderr else self._peer,
            start=self._start,
        )


def _forbid_inline_search(code: str) -> bool:
    """禁止在胶水代码里直接调用搜索工具、base64 编解码或绕过 kling API（搜索走服务端，base64 浪费 token，kling 须走确认）。"""
    s = code or ""
    # 仅拦截实际函数调用，不拦截字符串字面量中的工具名
    if re.search(r"\bfile_search\s*\(", s) or re.search(r"\bgrep_files\s*\(", s):
        return True
    if re.search(r"\bbase64\s*\.\s*(b64decode|b64encode|decode|encode)\s*\(", s):
        return True
    # 禁止绕过 kling_generate 直接调可灵 API
    _kling_pats = ["api-beijing.klingai.com", "klingai.com", "AGENT_KLING_API_KEY", "AGENT_KLING_SECRET_KEY", "KLING_API_KEY", "KLING_SECRET_KEY"]
    for _kp in _kling_pats:
        if _kp in s.lower():
            return True
    return False


def agent_main(
    *,
    code: str,
    cwd: Optional[str] = None,
    timeout_sec: int = 300,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    parser_for_help: Optional[argparse.ArgumentParser] = None,
    _progress_dict: Optional[dict] = None,
) -> dict:
    """返回 {ok, data:{ok, stdout, stderr, exit_code, timeout}, error}。"""
    _ = timeout_sec
    progress = _progress_dict if isinstance(_progress_dict, dict) else None
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
                    "message": "禁止在 python_inline 中使用 file_search/grep_files/base64！搜索类工具请用对应 function call，base64 嵌入图片浪费 token 且导致对话中断。",
                },
            }

        stdout_parts: List[str] = []
        stderr_parts: List[str] = []
        start = time.monotonic()
        if progress is not None:
            progress["phase"] = "python_inline"
            progress["stdout_tail"] = ""
            progress["stderr_tail"] = ""
            progress["elapsed_sec"] = 0
            progress["_seq"] = 0

        prev_cwd: Optional[str] = None

        try:
            if cwd:
                cp = ac.resolve_path(cwd, allow_outside_workspace=not restrict_to_workspace)
                if not cp.is_dir():
                    raise ValueError(f"cwd 不是目录: {cp}")
                prev_cwd = os.getcwd()
                os.chdir(cp)
            else:
                ws = ac.workspace_root()
                prev_cwd = os.getcwd()
                os.chdir(ws)

            out_writer = _InlineProgressWriter(
                stdout_parts, progress, peer_parts=stderr_parts, start=start, is_stderr=False
            )
            err_writer = _InlineProgressWriter(
                stderr_parts, progress, peer_parts=stdout_parts, start=start, is_stderr=True
            )
            with contextlib.redirect_stdout(out_writer), contextlib.redirect_stderr(err_writer):
                exec(code, {"__name__": "__python_inline__"})  # noqa: S102
            sub_ok = True
            exit_code = 0
        except Exception as e:
            stderr_parts.append(f"\n{type(e).__name__}: {e}")
            _sync_inline_progress(progress, stdout_parts=stdout_parts, stderr_parts=stderr_parts, start=start)
            sub_ok = False
            exit_code = 1
        finally:
            if prev_cwd is not None:
                try:
                    os.chdir(prev_cwd)
                except OSError:
                    pass

        _sync_inline_progress(progress, stdout_parts=stdout_parts, stderr_parts=stderr_parts, start=start)
        data = {
            "ok": sub_ok,
            "exit_code": exit_code,
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts),
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
        data = {"ok": False, "exit_code": None, "stdout": "", "stderr": str(e), "timeout": False}
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
    if args.timeout_sec <= 0:
        raise ValueError("timeout_sec 必须 > 0")
    env = agent_main(
        code=args.code,
        cwd=str(args.cwd) if args.cwd else None,
        timeout_sec=int(args.timeout_sec),
        restrict_to_workspace=bool(args.restrict_to_workspace),
        run_type=str(args.run_type or "").strip().lower(),
        parser_for_help=parser,
    )
    _emit_envelope_file_and_stdout(args, env)


if __name__ == "__main__":
    main()
