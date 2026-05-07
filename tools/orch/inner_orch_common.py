# -*- coding: utf-8 -*-
"""供同目录「编排_*.py」调用：优先进程内调用 CLI 工具，必要时回退子进程并解析 JSON。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

_INLINE_TOOL_LOCK = threading.RLock()


def tools_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def cli_structured() -> Path:
    return tools_dir() / "cli_structured_edit.py"


def cli_regex() -> Path:
    return tools_dir() / "cli_regex_locate.py"


_AGENT_MAIN_ARG_ALIASES = {
    "glob": "glob_pattern",
    "type": "type_filter",
}


def _camel_to_snake(name: str) -> str:
    name = str(name or "").strip().lstrip("-").replace("-", "_")
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _agent_main_param_name(raw_key: str) -> str:
    snake = _camel_to_snake(raw_key)
    return _AGENT_MAIN_ARG_ALIASES.get(snake, snake)


def _argv_to_args(argv: list[str]) -> dict:
    args = {}
    i = 0
    while i < len(argv):
        cur = str(argv[i])
        if not cur.startswith("--"):
            i += 1
            continue
        key = cur
        if i + 1 < len(argv) and not str(argv[i + 1]).startswith("--"):
            args[key] = argv[i + 1]
            i += 2
        else:
            args[key] = True
            i += 1
    return args


def _try_agent_main(mod, argv: list[str]):
    import inspect

    fn = getattr(mod, "agent_main", None)
    if not callable(fn):
        return None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    params = sig.parameters
    kwargs = {}
    for k, v in _argv_to_args(argv).items():
        pn = _agent_main_param_name(k)
        if pn == "json_out":
            continue
        if pn not in params:
            return None
        kwargs[pn] = v
    if "parser_for_help" in params and "parser_for_help" not in kwargs:
        bp = getattr(mod, "build_parser", None)
        if callable(bp):
            try:
                kwargs["parser_for_help"] = bp()
            except Exception:
                pass
    try:
        out = fn(**kwargs)
    except Exception:
        return None
    if isinstance(out, dict):
        return {k: v for k, v in out.items() if not str(k).startswith("_")}
    return None


def _run_tool_inline(cmd: list[str]) -> dict:
    """进程内直接调用工具脚本；优先 agent_main，必要时再模拟 main()。"""
    import importlib, io, json as _json
    if len(cmd) < 2:
        return _run_cli_json_subprocess(cmd)
    script_path = Path(cmd[1])
    tools_dir_path = tools_dir()
    try:
        rel = script_path.resolve().relative_to(tools_dir_path.resolve())
    except ValueError:
        return _run_cli_json_subprocess(cmd)
    if rel.suffix != ".py":
        return _run_cli_json_subprocess(cmd)
    mod_name = str(rel.with_suffix("")).replace(os.sep, ".")
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        return _run_cli_json_subprocess(cmd)
    argv = [str(script_path)] + cmd[2:]
    agent_out = _try_agent_main(mod, argv[1:])
    if agent_out is not None:
        return agent_out
    with _INLINE_TOOL_LOCK:
        old_argv = sys.argv
        old_stdout = sys.stdout
        captured = io.StringIO()
        try:
            sys.argv = argv
            sys.stdout = captured
            mod.main()
        except SystemExit:
            pass
        except Exception as e:
            return _run_cli_json_subprocess(cmd)
        finally:
            sys.stdout = old_stdout
            sys.argv = old_argv
    output = captured.getvalue().strip()
    if not output:
        raise RuntimeError(f"内联调用无 stdout: {script_path.name}")
    try:
        return _json.loads(output.splitlines()[-1])
    except _json.JSONDecodeError as e:
        raise RuntimeError(f"内联调用输出非 JSON: {output[:500]!r}") from e


def _run_cli_json_subprocess(cmd: list[str]) -> dict:
    """子进程方式调用（备用回退，非 .py 或无法 import 时使用）"""
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    raw = (cp.stdout or "").strip()
    if not raw:
        raise RuntimeError(f"CLI 无 stdout: stderr={cp.stderr!r}")
    line = raw.splitlines()[-1]
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"CLI 输出非 JSON: {line[:500]!r}") from e


def _run_cli_json(cmd: list[str]) -> dict:
    """优先尝试进程内调用，失败时回退到 subprocess"""
    return _run_tool_inline(cmd)


def run_structured_json(
    *,
    payload: dict,
    file: str | Path | None = None,
    text: str | None = None,
    text_stdin: bool = False,
    encoding: str = "utf-8",
) -> dict:
    fd, tmp = tempfile.mkstemp(suffix=".json", text=True)
    os.close(fd)
    try:
        Path(tmp).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cmd: list[str] = [
            sys.executable,
            str(cli_structured()),
            "--encoding",
            encoding,
            "--jsonOut",
            "--payloadFile",
            tmp,
        ]
        if file is not None:
            cmd += ["--file", str(file)]
        if text is not None:
            cmd += ["--text", text]
        if text_stdin:
            cmd.append("--textStdin")
        return _run_cli_json(cmd)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def run_structured_with_payload_file(
    *,
    payload_file: str | Path,
    file: str | Path | None = None,
    encoding: str = "utf-8",
) -> dict:
    """已有 payload JSON 文件时直接传 --payloadFile（不经由临时文件再写一份）。"""
    cmd: list[str] = [
        sys.executable,
        str(cli_structured()),
        "--encoding",
        str(encoding),
        "--jsonOut",
        "--payloadFile",
        str(payload_file),
    ]
    if file is not None:
        cmd += ["--file", str(file)]
    return _run_cli_json(cmd)


def run_regex_json(
    *,
    target: str | Path,
    pattern: str,
    encoding: str = "utf-8",
    ignore_case: bool = False,
    multiline: bool = False,
    limit: int = 500,
) -> dict:
    cmd: list[str] = [
        sys.executable,
        str(cli_regex()),
        "--target",
        str(target),
        "--pattern",
        pattern,
        "--encoding",
        encoding,
        "--limit",
        str(limit),
        "--jsonOut",
    ]
    if ignore_case:
        cmd.append("--ignoreCase")
    if multiline:
        cmd.append("--multiline")
    return _run_cli_json(cmd)


def run_regex_export_masks(
    *,
    target: str | Path,
    pattern: str,
    masks_out: str | Path,
    encoding: str = "utf-8",
    ignore_case: bool = False,
    multiline: bool = False,
    limit: int = 500,
) -> dict:
    cmd: list[str] = [
        sys.executable,
        str(cli_regex()),
        "--target",
        str(target),
        "--pattern",
        pattern,
        "--encoding",
        encoding,
        "--limit",
        str(limit),
        "--rangesOut",
        str(masks_out),
        "--jsonOut",
    ]
    if ignore_case:
        cmd.append("--ignoreCase")
    if multiline:
        cmd.append("--multiline")
    return _run_cli_json(cmd)


def merge_intervals_half_open(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并半开区间 [s,e)，忽略 s>=e 的项。"""
    cleaned = [(int(s), int(e)) for s, e in intervals if int(e) > int(s) >= 0]
    if not cleaned:
        return []
    cleaned.sort(key=lambda x: (x[0], x[1]))
    out = [cleaned[0]]
    for s, e in cleaned[1:]:
        ls, le = out[-1]
        if s <= le:
            out[-1] = (ls, max(le, e))
        else:
            out.append((s, e))
    return out


def emit(ok: bool, data: dict | None, error: dict | None, *, json_out: bool) -> None:
    if json_out:
        print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))
    elif ok and data:
        print(json.dumps(data, ensure_ascii=False))
    elif not ok and error:
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)


def run_slice_between_regex(
    *,
    source_file: str | Path,
    out_file: str | Path,
    left_pattern: str | None,
    right_pattern: str | None,
    encoding: str = "utf-8",
    ignore_case: bool = False,
    multiline: bool = False,
    allow_no_left: bool = False,
    allow_no_right: bool = False,
    left_use_end_of_match: bool = False,
    right_bound_is_match_end: bool = False,
) -> dict:
    """
    在 source_file 全文上：先正则找左锚、再找右锚，再用 cli_structured_edit extract offsets 写出 out_file。
    默认：切片起点为左锚首次命中的 start；切片终点（开区间）为右锚首次命中且 start>=切片起点的 start。
    left_use_end_of_match=True 时起点改为左锚首次命中的 end。
    right_bound_is_match_end=True 时终点改为上述右锚命中的 end（extract 仍为 [start,end)）。
    """
    path = Path(source_file)
    text = path.read_text(encoding=encoding, errors="replace")
    n = len(text)

    if left_pattern:
        lj = run_regex_json(
            target=path,
            pattern=left_pattern,
            encoding=encoding,
            ignore_case=ignore_case,
            multiline=multiline,
        )
        if not lj.get("ok"):
            return lj
        items = (lj.get("data") or {}).get("items") or []
        if not items:
            if not allow_no_left:
                return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "左锚未命中"}}
            sl = 0
        else:
            it0 = items[0]
            sl = int(it0["end"]) if left_use_end_of_match else int(it0["start"])
    else:
        sl = 0

    if right_pattern:
        rj = run_regex_json(
            target=path,
            pattern=right_pattern,
            encoding=encoding,
            ignore_case=ignore_case,
            multiline=multiline,
        )
        if not rj.get("ok"):
            return rj
        items_r = (rj.get("data") or {}).get("items") or []
        er: int | None = None
        for it in items_r:
            rs = int(it["start"])
            if rs < sl:
                continue
            er = int(it["end"]) if right_bound_is_match_end else rs
            break
        if er is None:
            if not allow_no_right:
                return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "右锚未命中"}}
            er = n
    else:
        er = n

    if sl < 0 or er < 0 or sl > er or er > n:
        return {
            "ok": False,
            "data": None,
            "error": {"type": "ValueError", "message": f"区间非法: start={sl} end={er} len={n}"},
        }

    pl = {"type": "extract", "mode": "offsets", "start": sl, "end": er, "outFile": str(out_file)}
    return run_structured_json(payload=pl, file=str(path), encoding=encoding)
