# -*- coding: utf-8 -*-
"""
CLI Git 工作区工具
==================

用途
----
在 Git 仓库根目录下提供多种只读查询，结构化 JSON 输出，减少 agent 手写 git 与解析成本。

模式 --mode
-----------
- worktree（默认）：`git status --porcelain=v1 -b` + 工作区/暂存区 diff（diff 可截断）
- log：最近若干条提交（hash / 主题 / 作者 / 日期）
- blame：单文件逐行归属（`git blame --line-porcelain`）
- show：指定提交（默认 HEAD）的元信息 + 变更文件列表 + 可选补丁摘要（可截断）

说明
----
- 依赖本机 `git` 且在 PATH 中。
- diff / show 补丁由 BUILTIN_MAX_DIFF_CHARS 控制最大字符数。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import re
import subprocess
import sys

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser


BUILTIN_MAX_DIFF_CHARS = 200_000
BUILTIN_LOG_MAX = 20
BUILTIN_BLAME_MAX_BYTES = 2_000_000


def _run_git(cwd: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _truncate(s: str, max_chars: int) -> tuple[str, bool]:
    if len(s) <= max_chars:
        return s, False
    return s[:max_chars], True


def _error_payload(
    *,
    code: str,
    message: str,
    hint: str,
    exit_code: int | None = None,
    retryable: bool = False,
) -> dict:
    return {
        "code": code,
        "type": "GitWorkspaceError",
        "message": message,
        "exitCode": exit_code,
        "hint": hint,
        "retryable": retryable,
    }


def _ensure_repo(root: Path) -> None:
    if not root.is_dir():
        raise NotADirectoryError(f"root 不是目录: {root}")
    if not (root / ".git").exists():
        raise ValueError(f"不是 Git 仓库根目录（缺少 .git）: {root}")


def _resolve_under_root(root: Path, rel: str) -> Path:
    root_r = root.resolve()
    p = (root_r / rel).resolve()
    try:
        p.relative_to(root_r)
    except ValueError:
        raise ValueError(f"path 越出 root: {rel}") from None
    return p


def _mode_worktree(root: Path, max_chars: int) -> dict:
    st = _run_git(root, "status", "--porcelain=v1", "-b")
    if st.returncode != 0:
        raise RuntimeError(f"git status 失败: {st.stderr.strip() or st.stdout.strip()}")

    du = _run_git(root, "diff", "--no-color")
    if du.returncode != 0:
        raise RuntimeError(f"git diff 失败: {du.stderr.strip() or du.stdout.strip()}")

    ds = _run_git(root, "diff", "--no-color", "--cached")
    if ds.returncode != 0:
        raise RuntimeError(f"git diff --cached 失败: {ds.stderr.strip() or ds.stdout.strip()}")

    diff_worktree, tw = _truncate(du.stdout or "", max_chars)
    diff_staged, ts = _truncate(ds.stdout or "", max_chars)

    lines = [ln for ln in (st.stdout or "").splitlines() if ln.strip()]
    branch_line = ""
    porcelain: list[dict[str, str]] = []
    for ln in lines:
        if ln.startswith("## "):
            branch_line = ln[3:].strip()
        else:
            porcelain.append({"raw": ln})

    return {
        "mode": "worktree",
        "root": str(root),
        "branchLine": branch_line,
        "porcelain": porcelain,
        "porcelainCount": len(porcelain),
        "diffWorktree": diff_worktree,
        "diffStaged": diff_staged,
        "diffTruncated": tw or ts,
        "rawDiffWorktreeChars": len(du.stdout or ""),
        "rawDiffStagedChars": len(ds.stdout or ""),
        "effectiveMaxDiffChars": max_chars,
    }


def _mode_log(root: Path, max_n: int) -> dict:
    if max_n <= 0:
        raise ValueError("logMax 必须 > 0")
    fmt = "%H%x1f%s%x1f%aN%x1f%ai%x1e"
    cp = _run_git(root, "log", f"-n{max_n}", f"--pretty=format:{fmt}", "--no-color")
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "git log 失败")
    entries: list[dict[str, str]] = []
    raw = (cp.stdout or "").strip()
    if not raw:
        return {"mode": "log", "root": str(root), "logMax": max_n, "entries": entries}
    for rec in raw.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 4:
            continue
        entries.append(
            {"commit": parts[0], "subject": parts[1], "author": parts[2], "date": parts[3]}
        )
    return {"mode": "log", "root": str(root), "logMax": max_n, "entries": entries}


_HEADER_RE = re.compile(r"^([0-9a-f]{7,40}) (\d+) (\d+) (\d+)$")


def _mode_blame(root: Path, rel_path: str, start_line: int | None, end_line: int | None) -> dict:
    target = _resolve_under_root(root, rel_path)
    if not target.is_file():
        raise FileNotFoundError(f"blame 目标不是文件: {target}")
    raw_size = target.stat().st_size
    if raw_size > BUILTIN_BLAME_MAX_BYTES and (start_line is None or end_line is None):
        raise ValueError(f"文件过大（>{BUILTIN_BLAME_MAX_BYTES} 字节），请指定 --startLine / --endLine（blame）")

    rel_git = target.resolve().relative_to(root.resolve()).as_posix()
    cmd: list[str] = ["blame", "--line-porcelain", "--no-color"]
    if start_line is not None and end_line is not None:
        if start_line < 1 or end_line < 1 or start_line > end_line:
            raise ValueError("blame 下行范围非法：--startLine / --endLine 须为 >=1 且 startLine<=endLine")
        cmd += ["-L", f"{start_line},{end_line}"]
    cmd += ["--", rel_git]

    cp = _run_git(root, *cmd)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "git blame 失败")

    lines_out: list[dict[str, str | int]] = []
    cur: dict[str, int | str] | None = None
    meta: dict[str, str] = {}
    for line in (cp.stdout or "").splitlines():
        m = _HEADER_RE.match(line)
        if m:
            cur = {"commit": m.group(1), "origLine": int(m.group(2)), "finalLine": int(m.group(3))}
            meta = {}
            continue
        if cur is None:
            continue
        if line.startswith("\t"):
            row = {
                "commit": str(cur["commit"]),
                "origLine": int(cur["origLine"]),
                "finalLine": int(cur["finalLine"]),
                "text": line[1:],
                "author": meta.get("author", ""),
                "summary": meta.get("summary", ""),
            }
            lines_out.append(row)
            cur = None
            meta = {}
            continue
        if " " in line:
            k, _, rest = line.partition(" ")
            meta[k] = rest.strip()

    return {
        "mode": "blame",
        "root": str(root),
        "path": rel_git,
        "lines": lines_out,
    }


def _mode_show(root: Path, ref: str, max_chars: int) -> dict:
    if not ref or not ref.strip():
        raise ValueError("showRef 不能为空")
    ref = ref.strip()

    meta_cp = _run_git(root, "show", "--no-color", "--no-patch", "--format=medium", ref)
    if meta_cp.returncode != 0:
        raise RuntimeError(meta_cp.stderr.strip() or meta_cp.stdout.strip() or "git show 失败")

    stat_cp = _run_git(root, "show", "--no-color", "--no-patch", "--stat", ref)
    stat_text = (stat_cp.stdout or "").strip() if stat_cp.returncode == 0 else ""

    ns_cp = _run_git(root, "diff-tree", "--no-color", "--no-commit-id", "--name-status", "-r", ref)
    if ns_cp.returncode != 0:
        raise RuntimeError(ns_cp.stderr.strip() or ns_cp.stdout.strip() or "git diff-tree 失败")

    files: list[dict[str, str]] = []
    for ln in (ns_cp.stdout or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.split("\t", maxsplit=1)
        if len(parts) == 2:
            files.append({"status": parts[0], "path": parts[1]})
        else:
            files.append({"status": "?", "path": ln})

    patch_cp = _run_git(root, "show", "--no-color", "--pretty=format:", "-p", ref)
    patch_raw = patch_cp.stdout or ""
    if patch_cp.returncode != 0:
        patch_raw = ""
    patch, trunc = _truncate(patch_raw, max_chars)

    return {
        "mode": "show",
        "root": str(root),
        "ref": ref,
        "commitMessage": (meta_cp.stdout or "").strip(),
        "statSummary": stat_text,
        "changedFiles": files,
        "patch": patch,
        "patchTruncated": trunc,
        "rawPatchChars": len(patch_raw),
        "effectiveMaxDiffChars": max_chars,
    }


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="Git 工作区：status/diff、log、blame、show 结构化输出")
    p.add_argument("--root", required=True, help="Git 仓库根目录（含 .git）")
    p.add_argument(
        "--mode",
        choices=["worktree", "log", "blame", "show"],
        default="worktree",
        help="worktree=状态+diff；log=提交历史；blame=行归属；show=某提交概览+补丁",
    )
    p.add_argument("--jsonOut", action="store_true", help="输出 {ok,data,error} JSON")
    p.add_argument(
        "--maxDiffChars",
        type=int,
        default=BUILTIN_MAX_DIFF_CHARS,
        help=f"worktree diff / show 补丁最大字符数，默认 {BUILTIN_MAX_DIFF_CHARS}",
    )
    p.add_argument("--logMax", type=int, default=BUILTIN_LOG_MAX, help=f"log 模式最多条数，默认 {BUILTIN_LOG_MAX}")
    p.add_argument(
        "--blamePath",
        help="blame 模式：相对 root 的文件路径，例如 src/a.py",
    )
    p.add_argument("--startLine", type=int, help="blame 模式：起始行（1-based，含）")
    p.add_argument("--endLine", type=int, help="blame 模式：结束行（1-based，含）")
    p.add_argument("--showRef", default="HEAD", help="show 模式：提交或引用，默认 HEAD")
    return p


def _emit_json(ok: bool, data=None, error=None) -> None:
    print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))


def agent_main(
    *,
    root: str,
    mode: str = "worktree",
    max_diff_chars: int = BUILTIN_MAX_DIFF_CHARS,
    log_max: int = BUILTIN_LOG_MAX,
    blame_path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    show_ref: str = "HEAD",
    parser_for_help: argparse.ArgumentParser | None = None,
) -> dict:
    """进程内入口；返回 {ok,data,error} 字典。"""
    r = Path(root).resolve()
    max_chars = max_diff_chars if max_diff_chars > 0 else BUILTIN_MAX_DIFF_CHARS
    try:
        _ensure_repo(r)
        if mode == "worktree":
            data = _mode_worktree(r, max_chars)
        elif mode == "log":
            data = _mode_log(r, log_max)
        elif mode == "blame":
            if not blame_path:
                raise ValueError("blame 模式需要 --blamePath（相对 root 的文件路径）")
            if (start_line is None) != (end_line is None):
                raise ValueError("blame 模式下 --startLine 与 --endLine 必须同时给出或同时省略")
            data = _mode_blame(r, blame_path, start_line, end_line)
        else:
            data = _mode_show(r, show_ref, max_chars)
        return {"ok": True, "data": data, "error": None}
    except Exception as e:
        msg = str(e) + ("\n\n--help:\n" + _capture_help(parser_for_help) if parser_for_help else "")
        return {
            "ok": False,
            "data": None,
            "error": _error_payload(
                code="E_GIT_WORKSPACE",
                message=msg,
                hint="检查 --root 是否为 Git 仓库根目录，并确认 git 在 PATH 中",
                exit_code=None,
                retryable=False,
            ),
        }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    env = agent_main(
        root=args.root,
        mode=args.mode,
        max_diff_chars=int(args.maxDiffChars),
        log_max=int(args.logMax),
        blame_path=args.blamePath,
        start_line=args.startLine,
        end_line=args.endLine,
        show_ref=args.showRef,
        parser_for_help=parser,
    )
    if env["ok"]:
        data = env["data"]
        assert data is not None
        if args.jsonOut:
            _emit_json(True, data=data, error=None)
        else:
            if args.mode == "worktree":
                print((data.get("branchLine") or ""))
                print("--- diff (worktree) ---")
                print(data.get("diffWorktree", ""), end="")
                print("--- diff (staged) ---")
                print(data.get("diffStaged", ""), end="")
            elif args.mode == "log":
                for e in data.get("entries", []):
                    print(f"{e.get('commit', '')[:8]} {e.get('date', '')} {e.get('author', '')} {e.get('subject', '')}")
            elif args.mode == "blame":
                for row in data.get("lines", []):
                    print(f"{row.get('finalLine')} {row.get('commit', '')[:8]} {row.get('text', '')}")
            else:
                print(data.get("commitMessage", ""))
                for f in data.get("changedFiles", []):
                    print(f"{f.get('status')}\t{f.get('path')}")
                print(data.get("patch", ""), end="")
        return
    err = env.get("error")
    if args.jsonOut:
        _emit_json(False, data=None, error=err)
    else:
        print(str(err.get("message", "") if err else ""), file=sys.stderr)
        raise RuntimeError(err.get("message", "") if err else "")


if __name__ == "__main__":
    main()
