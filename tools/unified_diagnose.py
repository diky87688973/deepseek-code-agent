# -*- coding: utf-8 -*-
"""Python 统一诊断：AST 语法检查 + 可选 ruff check（需本机 PATH 中有 ruff）。"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

BUILTIN_GLOB = "**/*.py"
BUILTIN_LIMIT_FILES = 500
BUILTIN_TIMEOUT_SEC = 120
BUILTIN_TRY_RUFF = True


def _read_text(path: Path, encoding: str) -> str:
    if encoding != "auto":
        return path.read_text(encoding=encoding, errors="replace")
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _root_resolve(root: Path) -> Path:
    r = root.resolve()
    if not r.is_dir():
        raise NotADirectoryError(f"root 不是目录: {r}")
    return r


def _collect_py_files(root: Path, glob_pattern: str, limit: int) -> List[Path]:
    out: List[Path] = []
    for p in sorted(root.glob(glob_pattern)):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".py":
            continue
        rp = p.resolve()
        try:
            rp.relative_to(root)
        except ValueError:
            continue
        out.append(rp)
        if len(out) >= limit:
            break
    return out


def _severity_for_ruff(code: str, message: str) -> str:
    if code.startswith("E9"):
        return "error"
    if code.startswith("F82"):
        return "error"
    if "invalid syntax" in message.lower():
        return "error"
    return "warning"


def _syntax_diagnostics(root: Path, files: List[Path], encoding: str) -> List[dict]:
    diag: List[dict] = []
    for path in files:
        try:
            src = _read_text(path, encoding)
        except OSError as e:
            diag.append(
                {
                    "file": str(path),
                    "source": "syntax",
                    "rule": "io_error",
                    "severity": "error",
                    "line": None,
                    "column": None,
                    "end_line": None,
                    "end_column": None,
                    "message": str(e),
                }
            )
            continue
        try:
            ast.parse(src, filename=str(path))
        except SyntaxError as e:
            lineno = e.lineno or 1
            off = e.offset or None
            col = off if off is not None else None
            end_lineno = getattr(e, "end_lineno", None) or lineno
            end_offset = getattr(e, "end_offset", None)
            diag.append(
                {
                    "file": str(path),
                    "source": "syntax",
                    "rule": "syntax",
                    "severity": "error",
                    "line": lineno,
                    "column": col,
                    "end_line": end_lineno,
                    "end_column": end_offset,
                    "message": e.msg or str(e),
                }
            )
    return diag


def _run_ruff_json(root: Path, timeout: int) -> Tuple[Optional[List[dict]], Optional[str]]:
    exe = shutil.which("ruff")
    if not exe:
        return None, "ruff 不在 PATH 中，已跳过"
    cp = subprocess.run(
        [exe, "check", ".", "--output-format=json"],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if cp.returncode not in (0, 1):
        err = (cp.stderr or cp.stdout or "").strip() or f"exit {cp.returncode}"
        return None, err
    raw = (cp.stdout or "").strip()
    if not raw:
        return [], None
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"ruff JSON 解析失败: {e}"
    if not isinstance(arr, list):
        return None, "ruff 输出不是 JSON 数组"
    out: List[dict] = []
    root_s = str(root.resolve())
    for item in arr:
        if not isinstance(item, dict):
            continue
        fn = item.get("filename")
        if not isinstance(fn, str):
            continue
        try:
            Path(fn).resolve().relative_to(Path(root_s))
        except ValueError:
            continue
        loc = item.get("location") or {}
        end_loc = item.get("end_location") or {}
        code = str(item.get("code") or "unknown")
        msg = str(item.get("message") or "")
        row = loc.get("row")
        col = loc.get("column")
        erow = end_loc.get("row")
        ecol = end_loc.get("column")
        out.append(
            {
                "file": str(Path(fn).resolve()),
                "source": "ruff",
                "rule": code,
                "severity": _severity_for_ruff(code, msg),
                "line": int(row) if isinstance(row, int) else None,
                "column": int(col) if isinstance(col, int) else None,
                "end_line": int(erow) if isinstance(erow, int) else None,
                "end_column": int(ecol) if isinstance(ecol, int) else None,
                "message": msg,
            }
        )
    return out, None


def _dedupe(items: List[dict]) -> List[dict]:
    seen: Set[tuple] = set()
    out: List[dict] = []
    for d in sorted(
        items,
        key=lambda x: (
            x.get("file") or "",
            x.get("line") or 0,
            x.get("column") or 0,
            x.get("rule") or "",
        ),
    ):
        key = (
            d.get("file"),
            d.get("line"),
            d.get("column"),
            d.get("rule"),
            d.get("source"),
            d.get("message"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _run_diagnose(
    *,
    root: Path,
    glob_pattern: str,
    limit: int,
    encoding: str,
    timeout_sec: int,
    no_ruff: bool,
) -> dict:
    if limit <= 0:
        raise ValueError("limit 必须 > 0")
    if timeout_sec <= 0:
        raise ValueError("timeout_sec 必须 > 0")

    root_r = _root_resolve(root)
    files = _collect_py_files(root_r, glob_pattern, limit)
    diagnostics = _syntax_diagnostics(root_r, files, encoding)

    ruff_notes: List[str] = []
    ruff_diag: Optional[List[dict]] = None
    try_ruff = BUILTIN_TRY_RUFF and not no_ruff
    if try_ruff:
        ruff_diag, ruff_err = _run_ruff_json(root_r, timeout_sec)
        if ruff_err:
            ruff_notes.append(ruff_err)
        if ruff_diag is not None:
            diagnostics.extend(ruff_diag)

    diagnostics = _dedupe(diagnostics)
    error_count = sum(1 for d in diagnostics if d.get("severity") == "error")
    warn_count = sum(1 for d in diagnostics if d.get("severity") == "warning")

    data = {
        "path": str(root_r),
        "glob_pattern": glob_pattern,
        "files_scanned": len(files),
        "diagnostics": diagnostics,
        "summary": {"errors": error_count, "warnings": warn_count, "total": len(diagnostics)},
        "ruff_attempted": try_ruff,
        "ruff_included": ruff_diag is not None,
        "notes": ruff_notes,
    }
    ok = error_count == 0
    return {"ok": ok, "data": data, "error": None}


def agent_main(
    *,
    path: str,
    glob_pattern: str = BUILTIN_GLOB,
    limit: int = BUILTIN_LIMIT_FILES,
    encoding: str = "utf-8",
    timeout_sec: int = BUILTIN_TIMEOUT_SEC,
    no_ruff: bool = False,
) -> dict:
    try:
        return _run_diagnose(
            root=Path(str(path)),
            glob_pattern=str(glob_pattern or BUILTIN_GLOB),
            limit=int(limit),
            encoding=str(encoding or "utf-8"),
            timeout_sec=int(timeout_sec),
            no_ruff=bool(no_ruff),
        )
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e)}}


def main() -> None:
    p = argparse.ArgumentParser(description="统一诊断：语法 + 可选 ruff")
    p.add_argument("--path", required=True)
    p.add_argument("--glob_pattern", default=BUILTIN_GLOB, dest="glob_pattern")
    p.add_argument("--limit", type=int, default=BUILTIN_LIMIT_FILES)
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--timeout_sec", type=int, default=BUILTIN_TIMEOUT_SEC, dest="timeout_sec")
    p.add_argument("--no_ruff", action="store_true", dest="no_ruff")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(
        path=args.path,
        glob_pattern=args.glob_pattern,
        limit=args.limit,
        encoding=args.encoding,
        timeout_sec=args.timeout_sec,
        no_ruff=args.no_ruff,
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    elif r.get("ok") and r.get("data"):
        d = r["data"]
        s = d.get("summary", {})
        print(f"path={d.get('path')} files={d.get('files_scanned')} diagnostics={s.get('total')} errors={s.get('errors')}")
        for diag in d.get("diagnostics", []):
            loc = f"{diag.get('line')}:{diag.get('column')}" if diag.get("line") is not None else "-"
            print(f"[{diag.get('severity')}] {diag.get('file')} {loc} {diag.get('rule')} {diag.get('message')}")
        for n in d.get("notes", []):
            print(f"note: {n}", file=sys.stderr)
    else:
        err = r.get("error") or {}
        print(str(err.get("message", "")), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
