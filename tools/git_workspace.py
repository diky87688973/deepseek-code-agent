# -*- coding: utf-8 -*-
"""Git 工作区只读查询：status/diff、log、blame、show。不执行写入型 git 命令。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

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


def _truncate(s: str, max_chars: int) -> Tuple[str, bool]:
    if len(s) <= max_chars:
        return s, False
    return s[:max_chars], True


def _error_payload(
    *,
    code: str,
    message: str,
    hint: str,
    exit_code: Optional[int] = None,
    retryable: bool = False,
) -> dict:
    return {
        "code": code,
        "type": "GitWorkspaceError",
        "message": message,
        "exit_code": exit_code,
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
    except ValueError as err:
        raise ValueError(f"path 越出 root: {rel}") from err
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
    porcelain: List[Dict[str, str]] = []
    for ln in lines:
        if ln.startswith("## "):
            branch_line = ln[3:].strip()
        else:
            porcelain.append({"raw": ln})

    return {
        "mode": "worktree",
        "path": str(root),
        "branch_line": branch_line,
        "porcelain": porcelain,
        "porcelain_count": len(porcelain),
        "diff_worktree": diff_worktree,
        "diff_staged": diff_staged,
        "diff_truncated": tw or ts,
        "raw_diff_worktree_chars": len(du.stdout or ""),
        "raw_diff_staged_chars": len(ds.stdout or ""),
        "effective_max_diff_chars": max_chars,
    }


def _mode_log(root: Path, max_n: int) -> dict:
    if max_n <= 0:
        raise ValueError("log_max 必须 > 0")
    fmt = "%H%x1f%s%x1f%aN%x1f%ai%x1e"
    cp = _run_git(root, "log", f"-n{max_n}", f"--pretty=format:{fmt}", "--no-color")
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "git log 失败")
    entries: List[Dict[str, str]] = []
    raw = (cp.stdout or "").strip()
    if not raw:
        return {"mode": "log", "path": str(root), "log_max": max_n, "entries": entries}
    for rec in raw.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 4:
            continue
        entries.append({"commit": parts[0], "subject": parts[1], "author": parts[2], "date": parts[3]})
    return {"mode": "log", "path": str(root), "log_max": max_n, "entries": entries}


_HEADER_RE = re.compile(r"^([0-9a-f]{7,40}) (\d+) (\d+) (\d+)$")


def _mode_blame(root: Path, rel_path: str, start_line: Optional[int], end_line: Optional[int]) -> dict:
    target = _resolve_under_root(root, rel_path)
    if not target.is_file():
        raise FileNotFoundError(f"blame 目标不是文件: {target}")
    raw_size = target.stat().st_size
    if raw_size > BUILTIN_BLAME_MAX_BYTES and (start_line is None or end_line is None):
        raise ValueError(f"文件过大（>{BUILTIN_BLAME_MAX_BYTES} 字节），请指定 start_line / end_line（blame）")

    rel_git = target.resolve().relative_to(root.resolve()).as_posix()
    cmd: List[str] = ["blame", "--line-porcelain", "--no-color"]
    if start_line is not None and end_line is not None:
        if start_line < 1 or end_line < 1 or start_line > end_line:
            raise ValueError("blame 下行范围非法：start_line / end_line 须为 >=1 且 start_line<=end_line")
        cmd += ["-L", f"{start_line},{end_line}"]
    cmd += ["--", rel_git]

    cp = _run_git(root, *cmd)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "git blame 失败")

    lines_out: List[Dict[str, Union[str, int]]] = []
    cur: Optional[Dict[str, Union[int, str]]] = None
    meta: Dict[str, str] = {}
    for line in (cp.stdout or "").splitlines():
        m = _HEADER_RE.match(line)
        if m:
            cur = {"commit": m.group(1), "orig_line": int(m.group(2)), "final_line": int(m.group(3))}
            meta = {}
            continue
        if cur is None:
            continue
        if line.startswith("\t"):
            row = {
                "commit": str(cur["commit"]),
                "orig_line": int(cur["orig_line"]),
                "final_line": int(cur["final_line"]),
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
        "path": str(root),
        "relative_path": rel_git,
        "lines": lines_out,
    }


def _mode_show(root: Path, ref: str, max_chars: int) -> dict:
    if not ref or not ref.strip():
        raise ValueError("show_ref 不能为空")
    ref = ref.strip()

    meta_cp = _run_git(root, "show", "--no-color", "--no-patch", "--format=medium", ref)
    if meta_cp.returncode != 0:
        raise RuntimeError(meta_cp.stderr.strip() or meta_cp.stdout.strip() or "git show 失败")

    stat_cp = _run_git(root, "show", "--no-color", "--no-patch", "--stat", ref)
    stat_text = (stat_cp.stdout or "").strip() if stat_cp.returncode == 0 else ""

    ns_cp = _run_git(root, "diff-tree", "--no-color", "--no-commit-id", "--name-status", "-r", ref)
    if ns_cp.returncode != 0:
        raise RuntimeError(ns_cp.stderr.strip() or ns_cp.stdout.strip() or "git diff-tree 失败")

    files: List[Dict[str, str]] = []
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
        "path": str(root),
        "ref": ref,
        "commit_message": (meta_cp.stdout or "").strip(),
        "stat_summary": stat_text,
        "changed_files": files,
        "patch": patch,
        "patch_truncated": trunc,
        "raw_patch_chars": len(patch_raw),
        "effective_max_diff_chars": max_chars,
    }


def agent_main(
    *,
    path: str,
    mode: str = "worktree",
    max_diff_chars: int = BUILTIN_MAX_DIFF_CHARS,
    log_max: int = BUILTIN_LOG_MAX,
    blame_path: str = "",
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    show_ref: str = "HEAD",
) -> dict:
    max_chars = int(max_diff_chars) if max_diff_chars and max_diff_chars > 0 else BUILTIN_MAX_DIFF_CHARS
    log_n = int(log_max) if log_max else BUILTIN_LOG_MAX
    r = Path(str(path)).resolve()
    bp = (blame_path or "").strip()

    try:
        _ensure_repo(r)
        if mode == "worktree":
            data = _mode_worktree(r, max_chars)
        elif mode == "log":
            data = _mode_log(r, log_n)
        elif mode == "blame":
            if not bp:
                raise ValueError("blame 模式需要 blame_path（相对仓库根 path 的文件路径）")
            if (start_line is None) != (end_line is None):
                raise ValueError("blame 模式下 start_line 与 end_line 必须同时给出或同时省略")
            data = _mode_blame(r, bp, start_line, end_line)
        elif mode == "show":
            data = _mode_show(r, show_ref, max_chars)
        else:
            raise ValueError(f"未知 mode: {mode!r}（须为 worktree|log|blame|show）")
        return {"ok": True, "data": data, "error": None}
    except Exception as e:
        return {
            "ok": False,
            "data": None,
            "error": _error_payload(
                code="E_GIT_WORKSPACE",
                message=str(e),
                hint="检查 path 是否为 Git 仓库根目录，并确认 git 在 PATH 中",
                exit_code=None,
                retryable=False,
            ),
        }


def main() -> None:
    p = argparse.ArgumentParser(description="Git 工作区：status/diff、log、blame、show")
    p.add_argument("--path", required=True)
    p.add_argument(
        "--mode",
        choices=["worktree", "log", "blame", "show"],
        default="worktree",
    )
    p.add_argument("--max_diff_chars", type=int, default=BUILTIN_MAX_DIFF_CHARS)
    p.add_argument("--log_max", type=int, default=BUILTIN_LOG_MAX)
    p.add_argument("--blame_path", default="")
    p.add_argument("--start_line", type=int, default=None)
    p.add_argument("--end_line", type=int, default=None)
    p.add_argument("--show_ref", default="HEAD")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(
        path=args.path,
        mode=args.mode,
        max_diff_chars=args.max_diff_chars,
        log_max=args.log_max,
        blame_path=str(args.blame_path or ""),
        start_line=args.start_line,
        end_line=args.end_line,
        show_ref=str(args.show_ref or "HEAD"),
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    elif r.get("ok"):
        d = r.get("data") or {}
        if args.mode == "worktree":
            print((d.get("branch_line") or ""))
            print("--- diff (worktree) ---")
            print(d.get("diff_worktree", ""), end="")
            print("--- diff (staged) ---")
            print(d.get("diff_staged", ""), end="")
        elif args.mode == "log":
            for e in d.get("entries", []):
                print(f"{e.get('commit', '')[:8]} {e.get('date', '')} {e.get('author', '')} {e.get('subject', '')}")
        elif args.mode == "blame":
            for row in d.get("lines", []):
                print(f"{row.get('final_line')} {row.get('commit', '')[:8]} {row.get('text', '')}")
        else:
            print(d.get("commit_message", ""))
            for f in d.get("changed_files", []):
                print(f"{f.get('status')}\t{f.get('path')}")
            print(d.get("patch", ""), end="")
    else:
        print(str((r.get("error") or {}).get("message", "")), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
