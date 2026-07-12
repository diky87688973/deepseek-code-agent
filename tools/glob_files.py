# -*- coding: utf-8 -*-
"""在目录下按 glob 列出路径，支持仅文件 / 仅目录 / 全部，并可选遵守 .gitignore。"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Set

import agent_common as ac

BUILTIN_GITIGNORE_BATCH = 400


def _find_git_root(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for d in [cur, *cur.parents]:
        if (d / ".git").exists():
            return d
    return None


def _git_check_ignore_batch(repo_root: Path, relative_posix_paths: List[str]) -> Set[str]:
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
    if cp.returncode not in (0, 1):
        return set()
    out = (cp.stdout or b"").split(b"\0")
    ignored: Set[str] = set()
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
    """按 pathlib/fnmatch 常见用法匹配路径；Windows 下路径与模式大小写不敏感。"""
    pat = pattern or "*"
    pat_norm = pat.replace("\\", "/")
    # 默认「递归全部」：**/* 应对根目录单层文件生效；仅用 fnmatch 时 **/* 常无法匹配不带 / 的相对路径。
    if pat_norm in ("**/*", "**"):
        return True
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


def _normalize_entry_type(raw: str) -> str:
    t = (raw or "file").strip().lower()
    if t in ("all", "file", "dir", "files", "dirs", "directories"):
        if t == "files":
            t = "file"
        if t in ("dirs", "directories"):
            t = "dir"
        return t
    raise ValueError(f"entry_type 须为 all、file 或 dir: {raw!r}")


def agent_main(
    *,
    path: str,
    glob_pattern: str = "**/*",
    recursive: bool = True,
    limit: int = 500,
    run_type: str = "",
    entry_type: str = "file",
    no_gitignore: bool = False,
    **_kwargs: object,
) -> dict:
    if _kwargs.get("pattern"):
        return ac.err(
            ValueError("glob_files 使用 glob_pattern 匹配文件名，勿传已废弃的 pattern 别名")
        )
    """
    entry_type: file | dir | all；默认 file 与历史 glob_files 行为一致。
    no_gitignore=False 时在 Git 仓库内尝试用 git check-ignore 批量过滤。
    """
    _ = run_type
    try:
        if limit <= 0:
            raise ValueError("limit 必须 > 0")

        et = _normalize_entry_type(entry_type)

        rp = ac.resolve_path(path, allow_outside_workspace=True)
        if not rp.exists():
            raise FileNotFoundError(f"路径不存在: {rp}")
        if not rp.is_dir():
            raise ValueError(f"path 必须是目录: {rp}")

        match_pat = (glob_pattern or "").strip() or ("**/*" if recursive else "*")

        respect_gitignore = not no_gitignore
        repo_root = _find_git_root(rp) if respect_gitignore else None
        git_available = shutil.which("git") is not None

        meta: dict = {
            "respect_gitignore_requested": respect_gitignore,
            "git_repo_root": str(repo_root) if repo_root else None,
            "gitignore_applied": False,
            "gitignore_note": None,
        }

        if respect_gitignore and repo_root is None:
            meta["gitignore_note"] = "未处于 Git 仓库内(向上未找到 .git),未应用忽略规则"
        elif respect_gitignore and not git_available:
            meta["gitignore_note"] = "未找到 git 可执行文件,未应用忽略规则"
        elif respect_gitignore and repo_root is not None and git_available:
            meta["gitignore_applied"] = True

        iterator = _iter_matching_paths(rp, match_pat, recursive)

        items: List[dict] = []

        def _append_item(p: Path) -> bool:
            if et == "file" and not p.is_file():
                return False
            if et == "dir" and not p.is_dir():
                return False
            rel = str(p.relative_to(rp))
            items.append(
                {
                    "path": str(p),
                    "relative_path": rel,
                    "kind": "dir" if p.is_dir() else "file",
                }
            )
            return len(items) >= limit

        if not meta["gitignore_applied"]:
            for p in iterator:
                if _append_item(p):
                    break
        else:
            assert repo_root is not None
            rr = repo_root
            pending_paths: List[Path] = []
            pending_rels: List[str] = []

            def flush_batch() -> bool:
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
                if len(items) >= limit:
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
            if len(items) < limit and pending_rels:
                flush_batch()

        truncated = len(items) >= limit
        paths = [x["path"] for x in items]

        return ac.ok(
            {
                "path": str(rp),
                "glob_pattern": match_pat,
                "entry_type": et,
                "count": len(items),
                "truncated": truncated,
                "paths": paths,
                "items": items,
                **meta,
            }
        )
    except Exception as e:
        return ac.err(e)




