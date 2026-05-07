# -*- coding: utf-8 -*-
"""
CLI 目录检索工具
================

用途
----
按目录列出文件/子目录，支持递归、glob 过滤、类型过滤与结果限制。
在 Git 工作区内且未关闭时，默认排除 .gitignore 命中的路径（与 `git check-ignore` 一致）。

说明
----
- 默认开启忽略规则：`--noGitignore` 可恢复「列一切路径」的旧行为。
- 需在 `--root` 的祖先目录中存在 `.git` 才应用忽略；否则不报错，仅不过滤。
- 应用忽略时，会跳过路径中含 `.git` 的条目（减少对象库噪音）。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import fnmatch
import json
import os
import shutil
import subprocess

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser


BUILTIN_GITIGNORE_BATCH = 400


def _find_git_root(start: Path) -> Path | None:
    cur = start.resolve()
    for d in [cur, *cur.parents]:
        if (d / ".git").exists():
            return d
    return None


def _git_check_ignore_batch(repo_root: Path, relative_posix_paths: list[str]) -> set[str]:
    if not relative_posix_paths:
        return set()
    exe = shutil.which("git")
    if not exe:
        return set()
    raw = "\0".join(relative_posix_paths) + "\0"
    try:
        cp = subprocess.run(
            [exe, "-C", str(repo_root), "check-ignore", "-z", "--stdin"],
            input=raw.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    # 0：至少一个被忽略；1：无一被忽略；其它：异常
    if cp.returncode not in (0, 1):
        return set()
    out = (cp.stdout or b"").split(b"\0")
    ignored: set[str] = set()
    for chunk in out:
        if not chunk:
            continue
        try:
            s = chunk.decode("utf-8", errors="replace")
        except Exception:
            continue
        if s:
            ignored.add(s)
    return ignored


def _match_path(rel_posix: str, name: str, pattern: str) -> bool:
    """按 pathlib glob 的常见用法匹配路径；Windows 下保持大小写不敏感。"""
    pat = pattern or "*"
    target_rel = rel_posix
    target_name = name
    if os.name == "nt":
        pat = pat.lower()
        target_rel = target_rel.lower()
        target_name = target_name.lower()
    if "/" in pat or "\\" in pat:
        pat = pat.replace("\\", "/")
        if fnmatch.fnmatchcase(target_rel, pat):
            return True
        if pat.startswith("**/"):
            return fnmatch.fnmatchcase(target_rel, pat[3:])
        return False
    return fnmatch.fnmatchcase(target_name, pat)


def _iter_matching_paths(root_p: Path, pattern: str, recursive: bool):
    if recursive:
        for dirpath, dirnames, filenames in os.walk(str(root_p)):
            dirnames.sort()
            filenames.sort()
            for entry_name in [*dirnames, *filenames]:
                p = Path(dirpath) / entry_name
                try:
                    rel_posix = p.relative_to(root_p).as_posix()
                except ValueError:
                    continue
                if _match_path(rel_posix, entry_name, pattern):
                    yield p
        return

    try:
        names = sorted(os.listdir(str(root_p)))
    except OSError:
        names = []
    for entry_name in names:
        p = root_p / entry_name
        rel_posix = entry_name.replace("\\", "/")
        if _match_path(rel_posix, entry_name, pattern):
            yield p


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="目录检索：列出文件/目录，可选遵守 .gitignore")
    p.add_argument("--root", required=True, help="根目录路径")
    p.add_argument("--recursive", action="store_true", help="是否递归")
    p.add_argument("--glob", default="*", help='名称过滤，如 "*.py"、"**/*.md"')
    p.add_argument("--type", choices=["all", "file", "dir"], default="all", help="返回类型")
    p.add_argument("--limit", type=int, default=200, help="最多返回条数")
    p.add_argument(
        "--noGitignore",
        action="store_true",
        help="不遵守 .gitignore（默认在 Git 仓库内会遵守）",
    )
    p.add_argument("--jsonOut", action="store_true", help="以 JSON 输出")
    return p


def agent_main(
    *,
    root: str | Path,
    recursive: bool = False,
    glob_pattern: str = "*",
    type_filter: str = "all",
    limit: int = 200,
    no_gitignore: bool = False,
) -> dict:
    """供进程内（如 agent）直接调用；返回与 CLI ``--jsonOut`` 相同的外层结构（字典）。"""
    try:
        if limit <= 0:
            raise ValueError("limit 必须 > 0")

        root_p = Path(root).resolve()
        if not root_p.exists():
            raise FileNotFoundError(f"目录不存在: {root_p}")
        if not root_p.is_dir():
            raise ValueError(f"root 不是目录: {root_p}")

        respect_gitignore = not no_gitignore
        repo_root = _find_git_root(root_p) if respect_gitignore else None
        git_available = shutil.which("git") is not None

        meta: dict = {
            "respectGitignoreRequested": respect_gitignore,
            "gitRepoRoot": str(repo_root) if repo_root else None,
            "gitignoreApplied": False,
            "gitignoreNote": None,
        }

        if respect_gitignore and repo_root is None:
            meta["gitignoreNote"] = "未处于 Git 仓库内（向上未找到 .git），未应用忽略规则"
        elif respect_gitignore and not git_available:
            meta["gitignoreNote"] = "未找到 git 可执行文件，未应用忽略规则"
        elif respect_gitignore and repo_root is not None and git_available:
            meta["gitignoreApplied"] = True

        pattern = glob_pattern or "*"
        iterator = _iter_matching_paths(root_p, pattern, recursive)

        out: list[dict] = []

        def _append_item(p: Path) -> bool:
            if type_filter == "file" and not p.is_file():
                return False
            if type_filter == "dir" and not p.is_dir():
                return False
            out.append(
                {
                    "path": str(p),
                    "relative_path": str(p.relative_to(root_p)),
                    "kind": "dir" if p.is_dir() else "file",
                }
            )
            return len(out) >= limit

        if not meta["gitignoreApplied"]:
            for p in iterator:
                if _append_item(p):
                    break
        else:
            assert repo_root is not None
            rr = repo_root
            pending_paths: list[Path] = []
            pending_rels: list[str] = []

            def flush_batch() -> bool:
                """返回 True 表示 out 已达 limit。"""
                nonlocal pending_paths, pending_rels
                if not pending_paths:
                    return False
                ignored = _git_check_ignore_batch(rr, pending_rels)
                for idx in range(len(pending_paths)):
                    p = pending_paths[idx]
                    rel = pending_rels[idx]
                    if rel in ignored:
                        continue
                    if _append_item(p):
                        pending_paths = []
                        pending_rels = []
                        return True
                pending_paths = []
                pending_rels = []
                return False

            for p in iterator:
                if len(out) >= limit:
                    break
                pr = p.resolve()
                if ".git" in pr.parts:
                    continue
                try:
                    rel_posix = pr.relative_to(rr).as_posix()
                except ValueError:
                    continue
                pending_paths.append(p)
                pending_rels.append(rel_posix)
                if len(pending_rels) >= BUILTIN_GITIGNORE_BATCH:
                    if flush_batch():
                        break
            if len(out) < limit and pending_rels:
                flush_batch()

        data = {"root": str(root_p), "count": len(out), "items": out, **meta}
        return {"ok": True, "data": data, "error": None}
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e)}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    res = agent_main(
        root=args.root,
        recursive=bool(args.recursive),
        glob_pattern=args.glob,
        type_filter=args.type,
        limit=int(args.limit),
        no_gitignore=bool(args.noGitignore),
    )
    if res["ok"]:
        data = res["data"]
        assert data is not None
        if args.jsonOut:
            print(json.dumps(res, ensure_ascii=False))
        else:
            for item in data["items"]:
                print("[" + item["kind"] + "] " + item["relative_path"])
        return
    err = res.get("error") or {}
    msg = str(err.get("message", ""))
    full_msg = msg + "\n\n--help:\n" + _capture_help(parser)
    if args.jsonOut:
        print(
            json.dumps(
                {"ok": False, "data": None, "error": {**err, "message": full_msg}},
                ensure_ascii=False,
            )
        )
    else:
        raise RuntimeError(full_msg)


if __name__ == "__main__":
    main()
