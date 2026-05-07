# -*- coding: utf-8 -*-
"""
CLI 统一诊断工具
================

用途
----
在指定根目录下收集 Python 静态问题，输出**统一字段**的 `diagnostics` 列表：
- 对每个匹配的 `.py` 文件做 `ast.parse`（语法类，source=`syntax`）
- 若本机可用 `ruff`，在 `--root` 下执行 `ruff check . --output-format=json` 并合并（source=`ruff`）

顶层输出
--------
- 使用 `--jsonOut` 时：`{ "ok": bool, "data": {...}, "error": null | {...} }`
- `data.diagnostics` 中每一项结构一致，见下方「诊断项」。

诊断项（统一 schema）
---------------------
- file: 绝对路径字符串
- source: `syntax` | `ruff`
- rule: 规则码（语法固定为 `syntax`；ruff 为其 code）
- severity: `error` | `warning`
- line / column / endLine / endColumn: 整数或 null
- message: 说明文本

改顶部 BUILTIN_* 后可在 IDE 中右键运行（仍支持命令行传参覆盖）。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import ast
import json
import shutil
import subprocess
import sys

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser


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


def _collect_py_files(root: Path, glob_pattern: str, limit: int) -> list[Path]:
    out: list[Path] = []
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


def _syntax_diagnostics(root: Path, files: list[Path], encoding: str) -> list[dict]:
    diag: list[dict] = []
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
                    "endLine": None,
                    "endColumn": None,
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
                    "endLine": end_lineno,
                    "endColumn": end_offset,
                    "message": e.msg or str(e),
                }
            )
    return diag


def _run_ruff_json(root: Path, timeout: int) -> tuple[list[dict] | None, str | None]:
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
    out: list[dict] = []
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
                "endLine": int(erow) if isinstance(erow, int) else None,
                "endColumn": int(ecol) if isinstance(ecol, int) else None,
                "message": msg,
            }
        )
    return out, None


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
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


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="统一诊断：语法 + 可选 ruff，JSON diagnostics")
    p.add_argument("--root", required=True, help="项目根目录（在此目录下执行检查）")
    p.add_argument("--glob", default=BUILTIN_GLOB, help=f"匹配 Python 文件，默认 {BUILTIN_GLOB}")
    p.add_argument("--limit", type=int, default=BUILTIN_LIMIT_FILES, help=f"最多扫描文件数，默认 {BUILTIN_LIMIT_FILES}")
    p.add_argument("--encoding", default="utf-8", help="读文件编码，默认 utf-8，可选 auto")
    p.add_argument("--timeoutSec", type=int, default=BUILTIN_TIMEOUT_SEC, help=f"ruff 超时秒数，默认 {BUILTIN_TIMEOUT_SEC}")
    p.add_argument("--noRuff", action="store_true", help="不调用 ruff")
    p.add_argument("--jsonOut", action="store_true", help="输出 {ok,data,error} JSON")
    return p


def _unified_diagnose_envelope(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict:
    if args.limit <= 0:
        raise ValueError("limit 必须 > 0")
    if args.timeoutSec <= 0:
        raise ValueError("timeoutSec 必须 > 0")

    root = _root_resolve(Path(args.root))
    files = _collect_py_files(root, args.glob, args.limit)
    diagnostics = _syntax_diagnostics(root, files, args.encoding)

    ruff_notes: list[str] = []
    ruff_diag: list[dict] | None = None
    try_ruff = BUILTIN_TRY_RUFF and not args.noRuff
    if try_ruff:
        ruff_diag, ruff_err = _run_ruff_json(root, args.timeoutSec)
        if ruff_err:
            ruff_notes.append(ruff_err)
        if ruff_diag is not None:
            diagnostics.extend(ruff_diag)

    diagnostics = _dedupe(diagnostics)
    error_count = sum(1 for d in diagnostics if d.get("severity") == "error")
    warn_count = sum(1 for d in diagnostics if d.get("severity") == "warning")

    data = {
        "root": str(root),
        "glob": args.glob,
        "filesScanned": len(files),
        "diagnostics": diagnostics,
        "summary": {"errors": error_count, "warnings": warn_count, "total": len(diagnostics)},
        "ruffAttempted": try_ruff,
        "ruffIncluded": ruff_diag is not None,
        "notes": ruff_notes,
    }
    ok = error_count == 0
    return {"ok": ok, "data": data, "error": None, "_text_lines": _format_text_report(root, files, diagnostics, error_count, ruff_notes)}


def _format_text_report(root: Path, files: list[Path], diagnostics: list[dict], error_count: int, ruff_notes: list[str]) -> list[str]:
    lines = [f"root={root} files={len(files)} diagnostics={len(diagnostics)} errors={error_count}"]
    for d in diagnostics:
        loc = f"{d.get('line')}:{d.get('column')}" if d.get("line") is not None else "-"
        lines.append(f"[{d.get('severity')}] {d.get('file')} {loc} {d.get('rule')} {d.get('message')}")
    for n in ruff_notes:
        lines.append(f"note: {n}")
    return lines


def agent_main(
    *,
    root: str,
    glob_pattern: str | None = None,
    limit: int | None = None,
    encoding: str = "utf-8",
    timeout_sec: int | None = None,
    no_ruff: bool = False,
) -> dict:
    parser = build_parser()
    args = argparse.Namespace(
        root=root,
        glob=glob_pattern if glob_pattern is not None else BUILTIN_GLOB,
        limit=limit if limit is not None else BUILTIN_LIMIT_FILES,
        encoding=encoding,
        timeoutSec=timeout_sec if timeout_sec is not None else BUILTIN_TIMEOUT_SEC,
        noRuff=no_ruff,
        jsonOut=True,
    )
    try:
        out = _unified_diagnose_envelope(parser, args)
        out.pop("_text_lines", None)
        return {"ok": out["ok"], "data": out["data"], "error": out["error"]}
    except Exception as e:
        msg = str(e) + "\n\n--help:\n" + _capture_help(parser)
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": msg}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        out = _unified_diagnose_envelope(parser, args)
        text_lines = out.pop("_text_lines", None)
        if args.jsonOut:
            print(json.dumps({"ok": out["ok"], "data": out["data"], "error": out["error"]}, ensure_ascii=False))
        else:
            for line in text_lines or []:
                if line.startswith("note:"):
                    print(line, file=sys.stderr)
                else:
                    print(line)
    except Exception as e:
        e.args = (str(e) + "\n\n--help:\n" + _capture_help(parser),)
        if args.jsonOut:
            print(
                json.dumps(
                    {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e)}},
                    ensure_ascii=False,
                )
            )
        else:
            raise


if __name__ == "__main__":
    main()
