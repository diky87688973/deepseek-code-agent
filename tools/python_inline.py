"""在当前解释器进程内执行一段 Python 源码（不经 shell），用于胶水编排。"""
from __future__ import annotations

import contextlib
import io
import os
import re
import time
from typing import Any, Dict, List, Optional

import agent_common as ac
import stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()


def _build_error(*, code: str, message: str, exit_code: Optional[int], hint: str, retryable: bool) -> dict:
    return {
        "code": code,
        "type": "PythonInlineError",
        "message": message,
        "exit_code": exit_code,
        "hint": hint,
        "retryable": retryable,
    }


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
    combined = out_s + ("\n" + err_s if err_s.strip() else "")
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
    """禁止在胶水代码里直接调用搜索工具、写文件操作、base64 编解码或绕过 kling API。"""
    s = code or ""
    if re.search(r"\bfile_search\s*\(", s) or re.search(r"\bgrep_files\s*\(", s):
        return True
    if re.search(r"\bbase64\s*\.\s*(b64decode|b64encode|decode|encode)\s*\(", s):
        return True
    # 禁止写文件操作——代码编辑必须走 write_file / replace_in_file / apply_patch
    if re.search(r"\.write_text\s*\(", s) or re.search(r"\.write_bytes\s*\(", s):
        return True
    if re.search(r"\bopen\s*\([^)]*['\"]w", s):
        return True
    _kling_pats = [
        "api-beijing.klingai.com",
        "klingai.com",
        "AGENT_KLING_API_KEY",
        "AGENT_KLING_SECRET_KEY",
        "KLING_API_KEY",
        "KLING_SECRET_KEY",
    ]
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
    _progress_dict: Optional[dict] = None,
) -> dict:
    """返回 {ok, data:{ok, stdout, stderr, exit_code, timeout}, error}。"""
    _ = timeout_sec
    progress = _progress_dict if isinstance(_progress_dict, dict) else None
    try:
        rt = str(run_type or "").strip().lower()
        if rt == "plan":
            return {
                "ok": False,
                "data": None,
                "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许执行代码"},
            }
        if _forbid_inline_search(code or ""):
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "Forbidden",
                    "message": (
                        "禁止在 python_inline 中调用搜索/写文件/base64 操作！"
                        "搜索类工具请用对应 function call，代码编辑必须走 write_file / replace_in_file / apply_patch。"
                    ),
                },
            }
        stdout_parts = []  # type: List[str]
        stderr_parts = []  # type: List[str]
        start = time.monotonic()
        if progress is not None:
            progress["phase"] = "python_inline"
            progress["stdout_tail"] = ""
            progress["stderr_tail"] = ""
            progress["elapsed_sec"] = 0
            progress["_seq"] = 0
        prev_cwd = None  # type: Optional[str]
        try:
            if cwd:
                cp = ac.resolve_path(cwd, allow_outside_workspace=not restrict_to_workspace)
                if not cp.is_dir():
                    raise ValueError("cwd 不是目录: %s" % cp)
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
                exec(code, {"__name__": "__python_inline__"})
            sub_ok = True
            exit_code = 0
        except Exception as e:
            stderr_parts.append("\n%s: %s" % (type(e).__name__, e))
            _sync_inline_progress(
                progress, stdout_parts=stdout_parts, stderr_parts=stderr_parts, start=start
            )
            sub_ok = False
            exit_code = 1
        finally:
            if prev_cwd is not None:
                try:
                    os.chdir(prev_cwd)
                except OSError:
                    pass
        _sync_inline_progress(
            progress, stdout_parts=stdout_parts, stderr_parts=stderr_parts, start=start
        )
        data = {
            "ok": sub_ok,
            "exit_code": exit_code,
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts),
            "timeout": False,
        }
        error = None
        if not sub_ok:
            err_line = data["stderr"].strip().split("\n")[-1] if data["stderr"] else "exec failed"
            error = _build_error(
                code="E_PY_INLINE_FAILED",
                message=err_line,
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
        return {"ok": False, "data": data, "error": error}
