# -*- coding: utf-8 -*-
"""在单文件或目录下按字面或正则检索文本。

agent_main 仅接受 Python 原生类型；main() 仅供人工调试；build_parser 供失败时输出等效 --help。
"""

from __future__ import annotations

import time

import agent_common as ac


def _first_match_span(line: str, pat, *, is_regex: bool, ignore_case: bool) -> tuple[int, int, str] | None:
    if is_regex:
        m = pat.search(line)
        if not m:
            return None
        a, b = m.span()
        return a, b, line[a:b]

    needle = str(pat)
    haystack = line.lower() if ignore_case else line

    # 多 pattern 支持：字面模式下 | 分隔多个子串，任一匹配即返回最先出现的
    if "|" in needle:
        parts = [p for p in needle.split("|") if p]
        if parts:
            best: tuple[int, int, str] | None = None
            for sub in parts:
                idx = haystack.find(sub)
                if idx >= 0:
                    if best is None or idx < best[0]:
                        end = idx + len(sub)
                        best = (idx, end, line[idx:end])
            if best is not None:
                return best
        return None

    idx = haystack.find(needle)
    if idx < 0:
        return None
    end = idx + len(needle)
    return idx, end, line[idx:end]


def agent_main(
    *,
    path: str,
    pattern: str,
    glob_pattern: str = "",
    regex: bool = False,
    ignore_case: bool = False,
    recursive: bool = True,
    context_lines: int = 0,
    limit: int = 200,
    encoding: str = "utf-8",
    no_gitignore: bool = False,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    _progress_dict: dict | None = None,
) -> dict:
    _ = run_type
    try:
        if limit <= 0:
            raise ValueError("limit 必须 > 0")
        ctx = max(0, int(context_lines))

        root = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        repo_root = root
        while repo_root.parent != repo_root and not (repo_root / ".git").exists():
            repo_root = repo_root.parent
        if not (repo_root / ".git").exists():
            repo_root = None  # type: ignore[assignment]

        pat, is_re = ac.compile_pattern(pattern, regex=regex, ignore_case=ignore_case)

        files: list = []
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = ac.collect_source_files(root, glob_pattern, recursive=recursive)
        else:
            raise FileNotFoundError(f"路径不存在: {root}")

        if (not no_gitignore) and repo_root is not None:
            files = ac.filter_by_gitignore(files, repo_root)

        matches: list[dict] = []
        scanned = 0
        _last_prog = 0.0
        if _progress_dict is not None:
            _progress_dict.update({"scanned": 0, "currentFile": "", "phase": "grep"})
        for fp in files:
            if ac.progress_abort_requested(_progress_dict):
                return {"ok": False, "data": None, "error": {"type": "Aborted", "message": "用户已停止搜索"}}
            if len(matches) >= limit:
                break
            scanned += 1
            if _progress_dict is not None:
                now = time.time()
                if scanned == 1 or scanned % 50 == 0 or now - _last_prog >= 1.0:
                    _progress_dict.update({"scanned": scanned, "currentFile": fp.name, "phase": "grep"})
                    _last_prog = now
            try:
                text = ac.read_file_text(fp, encoding)
            except OSError:
                continue
            lines_keepends = text.splitlines(keepends=True)
            starts, _ = ac.line_meta_keepends(lines_keepends)
            lines = [x.rstrip("\r\n") for x in lines_keepends]
            for i, line in enumerate(lines, start=1):
                if i % 200 == 0 and ac.progress_abort_requested(_progress_dict):
                    return {"ok": False, "data": None, "error": {"type": "Aborted", "message": "用户已停止搜索"}}
                if len(matches) >= limit:
                    break
                span = _first_match_span(line, pat, is_regex=is_re, ignore_case=ignore_case)
                if span is not None:
                    col0, col1, match_text = span
                    region_start = starts[i - 1] + col0
                    region_end = starts[i - 1] + col1
                    entry: dict = {
                        "file": str(fp),
                        "line": i,
                        "column": col0 + 1,
                        "text": line,
                        "match": match_text,
                        "regionStart": region_start,
                        "regionEnd": region_end,
                    }
                    if ctx > 0:
                        a = max(1, i - ctx)
                        b = min(len(lines), i + ctx)
                        entry["context"] = [(j, lines[j - 1]) for j in range(a, b + 1)]
                    matches.append(entry)

        return ac.ok(
            {
                "matchCount": len(matches),
                "truncated": len(matches) >= limit,
                "pattern": pattern,
                "regex": regex,
                "matches": matches,
                "hint": "每条 match 的 regionStart/regionEnd 可直接传给 replace_in_file 做单文件精确替换；多行或复杂正则替换优先用 regex_locate。",
            }
        )
    except Exception as e:
        return ac.err(e)


def build_parser() -> argparse.ArgumentParser:
    import argparse

    p = argparse.ArgumentParser(description="grep_files：人工调试入口 → agent_main")
    p.add_argument("--path", required=True)
    p.add_argument("--pattern", required=True)
    p.add_argument("--glob_pattern", default="", help="省略=仅常见文本/源码后缀；* 表示全部文件（含各类非文本/二进制）")
    p.add_argument("--regex", action="store_true")
    p.add_argument("--ignoreCase", action="store_true")
    p.add_argument("--recursive", action="store_true", default=True)
    p.add_argument("--noRecursive", action="store_false", dest="recursive")
    p.add_argument("--contextLines", type=int, default=0)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--noGitignore", action="store_true", dest="no_gitignore")
    p.add_argument(
        "--restrictToWorkspace",
        action="store_true",
        help="将 path 限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--runType", default="", help="占位，只读不拦截")
    p.add_argument("--jsonOut", action="store_true")
    return p


def main() -> None:
    import json

    args = build_parser().parse_args()
    r = agent_main(
        path=args.path,
        pattern=args.pattern,
        glob_pattern=args.glob_pattern,
        regex=args.regex,
        ignore_case=args.ignoreCase,
        recursive=args.recursive,
        context_lines=args.contextLines,
        limit=args.limit,
        encoding=args.encoding,
        no_gitignore=args.no_gitignore,
        restrict_to_workspace=bool(getattr(args, "restrictToWorkspace", False)),
        run_type=str(args.runType or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
