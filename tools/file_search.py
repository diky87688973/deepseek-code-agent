# -*- coding: utf-8 -*-
"""在目录或单文件中按字面或正则搜索文件内容；大目录扫描时可配合宿主注入的进度回调。

与 grep_files 的差异：适合大范围扫描，宿主可对 file_search 走独立线程并推送 tool_progress（见 deepseek 黑名单逻辑）。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

import agent_common as ac


def _load_gitignore(root: Path) -> list[re.Pattern]:
    patterns = []
    gitignore_path = root / ".gitignore"
    if not gitignore_path.exists():
        return patterns
    try:
        text = gitignore_path.read_text("utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("/"):
                line = line[1:]
            is_dir = line.endswith("/")
            if is_dir:
                line = line[:-1]
            regex = fnmatch.translate(line)
            if "/" not in line:
                regex = f"(?:.*[/\\\\])?{regex}"
            else:
                regex = f"^{regex}"
            if is_dir:
                regex += "(?:[/\\\\].*)?$"
            else:
                regex += "$"
            try:
                patterns.append(re.compile(regex, re.IGNORECASE))
            except re.error:
                pass
    except OSError:
        pass
    return patterns


def _is_ignored(rel_path: str, ignore_patterns: list[re.Pattern]) -> bool:
    rel = rel_path.replace("\\", "/")
    for p in ignore_patterns:
        if p.search(rel):
            return True
    return False


def _detect_encoding(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            raw = f.read(8192)
        if raw.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if raw.startswith(b"\xff\xfe"):
            return "utf-16-le"
        if raw.startswith(b"\xfe\xff"):
            return "utf-16-be"
        raw.decode("utf-8")
        return "utf-8"
    except (OSError, UnicodeDecodeError):
        pass
    return "gbk"


def _search_file(
    path: Path,
    pattern: re.Pattern,
    context_lines: int,
    limit: Optional[int],
    _progress_dict: Optional[dict] = None,
) -> list[dict]:
    results = []
    try:
        enc = _detect_encoding(path)
        with open(path, "r", encoding=enc, errors="replace") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError) as e:
        return [{"file": str(path), "error": str(e)}]

    total = len(lines)
    starts: list[int] = []
    cur = 0
    for ln in lines:
        starts.append(cur)
        cur += len(ln)
    for lineno, line in enumerate(lines, 1):
        if lineno % 256 == 0 and ac.progress_abort_requested(_progress_dict):
            return [{"_abort": True}]
        if limit is not None and len(results) >= limit:
            break
        m = pattern.search(line)
        if not m:
            continue

        ctx_before = []
        for i in range(max(0, lineno - 1 - context_lines), lineno - 1):
            ctx_before.append({"line": i + 1, "text": lines[i].rstrip("\n").rstrip("\r")})

        ctx_after = []
        for i in range(lineno, min(total, lineno + context_lines)):
            ctx_after.append({"line": i + 1, "text": lines[i].rstrip("\n").rstrip("\r")})

        line_text = line.rstrip("\n").rstrip("\r")
        match_text = m.group()
        region_start = starts[lineno - 1] + m.start()
        region_end = starts[lineno - 1] + m.end()
        results.append(
            {
                "file": str(path),
                "line": lineno,
                "column": m.start() + 1,
                "text": line_text,
                "match": match_text,
                "region_start": region_start,
                "region_end": region_end,
                "context_before": ctx_before,
                "context_after": ctx_after,
            }
        )

    return results


def _search_directory(
    target: Path,
    pattern: re.Pattern,
    glob_pattern: Optional[str],
    recursive: bool,
    context_lines: int,
    limit: Optional[int],
    ignore_gitignore: bool,
    max_scanned: int = 10000,
    _progress_dict: Optional[dict] = None,
) -> dict:
    ignore_patterns = []
    if ignore_gitignore:
        ignore_patterns = _load_gitignore(target)

    results = []
    scanned = 0
    matched_files = 0
    errors = []
    _last_report = time.time()

    _prog_data = {"scanned": 0, "phase": "search"}
    if _progress_dict is not None:
        _progress_dict.update(_prog_data)

    for entry in ac.iter_source_files(target, glob_pattern, recursive=recursive):
        if ac.progress_abort_requested(_progress_dict):
            return {
                "path": str(target),
                "pattern": pattern.pattern,
                "scanned_files": scanned,
                "matched_files": matched_files,
                "total_matches": len(results),
                "matches": results,
                "errors": errors if errors else None,
                "_host_aborted": True,
            }
        if ignore_patterns:
            try:
                rel = entry.relative_to(target)
                if _is_ignored(str(rel), ignore_patterns):
                    continue
            except ValueError:
                pass

        try:
            _cf_disp = str(entry.relative_to(target))
        except (ValueError, AttributeError):
            _cf_disp = entry.name
        _prog_data = {"scanned": scanned, "current_file": _cf_disp, "phase": "search"}
        if _progress_dict is not None:
            _progress_dict.update(_prog_data)

        file_results = _search_file(entry, pattern, context_lines, limit, _progress_dict=_progress_dict)
        if file_results and isinstance(file_results[0], dict) and file_results[0].get("_abort"):
            return {
                "path": str(target),
                "pattern": pattern.pattern,
                "scanned_files": scanned,
                "matched_files": matched_files,
                "total_matches": len(results),
                "matches": results,
                "errors": errors if errors else None,
                "_host_aborted": True,
            }

        scanned += 1
        _now = time.time()
        if scanned == 1 or scanned % 500 == 0 or _now - _last_report >= 2.0:
            try:
                _cf = str(entry.relative_to(target))
            except (ValueError, AttributeError):
                _cf = entry.name
            _prog_data = {"scanned": scanned, "current_file": _cf, "phase": "search"}
            if _progress_dict is not None:
                _progress_dict.update(_prog_data)
            _last_report = _now
        if max_scanned and scanned >= max_scanned:
            break
        if file_results:
            has_error = any("error" in r for r in file_results)
            if has_error:
                for r in file_results:
                    if "error" in r:
                        errors.append(r)
            else:
                matched_files += 1
                results.extend(file_results)

        if limit is not None and len(results) >= limit:
            results = results[:limit]
            break

    return {
        "path": str(target),
        "pattern": pattern.pattern,
        "scanned_files": scanned,
        "matched_files": matched_files,
        "total_matches": len(results),
        "matches": results,
        "errors": errors if errors else None,
    }


def agent_main(
    *,
    path: str,
    pattern: str,
    regex: bool = False,
    glob_pattern: Optional[str] = None,
    recursive: bool = True,
    context_lines: int = 0,
    ignore_case: bool = False,
    no_gitignore: bool = False,
    limit: Optional[int] = None,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    _progress_dict: Optional[dict] = None,
) -> dict:
    _ = run_type
    try:
        target_path = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        if not target_path.exists():
            raise FileNotFoundError(f"目标不存在: {target_path}")

        flags = re.IGNORECASE if ignore_case else 0
        if regex:
            compiled = re.compile(pattern, flags)
        else:
            # 字面模式：| 分隔多个子串，逐个转义后用 OR 连接
            parts = [re.escape(p) for p in pattern.split("|") if p]
            if parts:
                compiled = re.compile("|".join(parts), flags)
            else:
                compiled = re.compile(re.escape(pattern), flags)

        if target_path.is_file():
            if _progress_dict is not None:
                _progress_dict.update(
                    {"scanned": 0, "current_file": target_path.name, "phase": "search"}
                )
            results = _search_file(target_path, compiled, context_lines, limit, _progress_dict=_progress_dict)
            if results and isinstance(results[0], dict) and results[0].get("_abort"):
                return {"ok": False, "data": None, "error": {"type": "Aborted", "message": "用户已停止搜索"}}
            if _progress_dict is not None:
                _progress_dict.update(
                    {"scanned": 1, "current_file": target_path.name, "phase": "search"}
                )
            data = {
                "path": str(target_path),
                "pattern": pattern,
                "scanned_files": 1,
                "matched_files": 1 if results else 0,
                "total_matches": len(results),
                "matches": results,
                "errors": None,
                "hint": "matches 中的 region_start/region_end 可直接传给 replace_in_file；大范围只定位时优先 file_search，轻量搜索优先 grep_files。",
            }
        else:
            data = _search_directory(
                target_path,
                compiled,
                glob_pattern,
                recursive,
                context_lines,
                limit,
                ignore_gitignore=not no_gitignore,
                max_scanned=10000,
                _progress_dict=_progress_dict,
            )
            if isinstance(data, dict) and data.get("_host_aborted"):
                return {"ok": False, "data": None, "error": {"type": "Aborted", "message": "用户已停止搜索"}}

        if isinstance(data, dict):
            data.setdefault(
                "hint",
                "matches 中的 region_start/region_end 可直接传给 replace_in_file；大范围只定位时优先 file_search，轻量搜索优先 grep_files。",
            )
        return {"ok": True, "data": data, "error": None}
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e)}}


def main() -> None:
    p = argparse.ArgumentParser(description="全文搜索：按关键词/正则搜索文件内容")
    p.add_argument("--path", required=True, help="搜索目标目录或文件")
    p.add_argument("--pattern", required=True, help="搜索模式（字面字符串，--regex 时为正则）")
    p.add_argument("--regex", action="store_true")
    p.add_argument(
        "--glob_pattern",
        default=None,
        dest="glob_pattern",
        help="省略=仅常见文本/源码后缀；* 表示全部文件（含各类非文本/二进制）",
    )
    p.add_argument("--recursive", action="store_true", default=True)
    p.add_argument("--no-recursive", action="store_false", dest="recursive")
    p.add_argument("--context_lines", type=int, default=0, dest="context_lines")
    p.add_argument("--ignore_case", action="store_true", dest="ignore_case")
    p.add_argument("--no_gitignore", action="store_true", dest="no_gitignore")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--restrict_to_workspace", action="store_true")
    p.add_argument("--run_type", default="")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(
        path=args.path,
        pattern=args.pattern,
        regex=args.regex,
        glob_pattern=args.glob_pattern,
        recursive=args.recursive,
        context_lines=args.context_lines,
        ignore_case=args.ignore_case,
        no_gitignore=args.no_gitignore,
        limit=args.limit,
        restrict_to_workspace=bool(args.restrict_to_workspace),
        run_type=str(args.run_type or ""),
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    elif r.get("ok") and r.get("data"):
        print(json.dumps(r["data"], ensure_ascii=False, indent=2))
    else:
        print(str((r.get("error") or {}).get("message", "")), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
