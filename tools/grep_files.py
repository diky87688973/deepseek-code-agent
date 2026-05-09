# -*- coding: utf-8 -*-
"""在单文件或目录下按字面或正则检索文本。

agent_main 仅接受 Python 原生类型；main() 为 CLI 防腐层；build_parser 供失败时输出等效 --help。
"""

from __future__ import annotations

import time

import agent_common as ac


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
    allow_outside_workspace: bool = False,
    run_type: str = "",
    _progress_dict: dict | None = None,
) -> dict:
    _ = run_type
    try:
        if limit <= 0:
            raise ValueError("limit 必须 > 0")
        ctx = max(0, int(context_lines))

        root = ac.resolve_path(path, allow_outside_workspace=allow_outside_workspace)
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
            lines = text.splitlines()
            for i, line in enumerate(lines, start=1):
                if i % 200 == 0 and ac.progress_abort_requested(_progress_dict):
                    return {"ok": False, "data": None, "error": {"type": "Aborted", "message": "用户已停止搜索"}}
                if len(matches) >= limit:
                    break
                if ac.line_matches(line, pat, is_re, ignore_case):
                    entry: dict = {"file": str(fp), "line": i, "text": line}
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
            }
        )
    except Exception as e:
        return ac.err(e)


def build_parser() -> argparse.ArgumentParser:
    import argparse

    p = argparse.ArgumentParser(description="grep_files：CLI 防腐层 → agent_main")
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
    p.add_argument("--allowOutsideWorkspace", action="store_true")
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
        allow_outside_workspace=args.allowOutsideWorkspace,
        run_type=str(args.runType or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
