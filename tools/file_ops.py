# -*- coding: utf-8 -*-
"""文件系统操作：安全删除（回收站/ purge）、同卷重命名、复制、移动。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

import agent_common as ac
from tool_help_share import HelpfulParser

# 回收站根：优先环境变量 AGENT_RECYCLE_ROOT，否则使用内建默认路径（见 BUILTIN_RECYCLE_ROOT）
BUILTIN_RECYCLE_ROOT = Path.home() / "AI_DATA_ROOT" / "AI_安全删除回收站"


def _ensure_under_root(root: Path, p: Path) -> None:
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"path 越出安全根目录 (--security_root): {p}") from None


def _recycle_bin_root() -> Path:
    env = os.environ.get("AGENT_RECYCLE_ROOT", "").strip()
    if env:
        return Path(env)
    return Path(BUILTIN_RECYCLE_ROOT)


def _is_inside_recycle(src: Path, recycle_root: Path) -> bool:
    try:
        s = src.resolve()
        r = recycle_root.expanduser().resolve()
    except OSError:
        return False
    if not r.exists():
        return False
    try:
        s.relative_to(r)
        return True
    except ValueError:
        return False


def _unique_recycle_bucket(recycle_root: Path) -> Path:
    name = f"{datetime.now().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    return recycle_root / name


def _resolve_src_dest(
    source: str,
    dest: str | None,
    *,
    security_root: str | None,
    restrict_to_workspace: bool,
) -> tuple[Path | None, Path, Path | None]:
    src = ac.resolve_path(source, allow_outside_workspace=not restrict_to_workspace)
    dest_p: Path | None = None
    if dest is not None and str(dest).strip() != "":
        raw = Path(str(dest).strip())
        if raw.is_absolute():
            dest_p = ac.resolve_path(str(raw), allow_outside_workspace=not restrict_to_workspace)
        else:
            combined = (src.parent / raw).resolve()
            if restrict_to_workspace:
                root = ac.workspace_root()
                try:
                    combined.relative_to(root)
                except ValueError:
                    raise PermissionError(
                        f"路径越出工作区限制: {combined}（工作区根: {root}）"
                    ) from None
            dest_p = combined
    root_path: Path | None = None
    if security_root is not None and str(security_root).strip() != "":
        root_path = Path(str(security_root).strip()).expanduser().resolve()
        _ensure_under_root(root_path, src)
        if dest_p is not None:
            _ensure_under_root(root_path, dest_p)
    return root_path, src, dest_p


def _do_delete(src: Path, recursive: bool, dry: bool) -> dict:
    if not src.exists():
        raise FileNotFoundError(f"不存在: {src}")

    recycle = _recycle_bin_root()
    try:
        if src.resolve() == recycle.expanduser().resolve():
            raise ValueError("不允许 delete 回收站根目录本身；请仅删除其下子路径")
    except OSError:
        pass
    if _is_inside_recycle(src, recycle):
        if src.is_file() or src.is_symlink():
            if dry:
                return {"action": "purge", "target": str(src), "kind": "file", "dry_run": True}
            src.unlink(missing_ok=False)
            return {"action": "purge", "target": str(src), "kind": "file", "dry_run": False}
        if src.is_dir():
            if not recursive:
                raise IsADirectoryError(f"是目录且未指定 recursive: {src}")
            if dry:
                return {
                    "action": "purge",
                    "target": str(src),
                    "kind": "dir",
                    "recursive": True,
                    "dry_run": True,
                }
            shutil.rmtree(src)
            return {
                "action": "purge",
                "target": str(src),
                "kind": "dir",
                "recursive": True,
                "dry_run": False,
            }
        raise ValueError(f"无法识别类型: {src}")

    if src.is_dir() and not recursive:
        raise IsADirectoryError(f"是目录且未指定 recursive: {src}")

    bucket = _unique_recycle_bucket(recycle)
    dest = bucket / src.name
    kind = "dir" if src.is_dir() else "file"
    rec_info = {
        "action": "recycle",
        "source": str(src),
        "recycled_to": str(dest),
        "recycle_root": str(recycle.resolve()),
        "kind": kind,
        "recursive": bool(src.is_dir()),
        "dry_run": dry,
    }
    if dry:
        return rec_info

    recycle.mkdir(parents=True, exist_ok=True)
    bucket.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if src.is_dir():
            dest = bucket / f"{src.name}_{uuid.uuid4().hex[:4]}"
        else:
            dest = bucket / f"{src.stem}_{uuid.uuid4().hex[:4]}{src.suffix}"

    shutil.move(str(src), str(dest))
    rec_info["recycled_to"] = str(dest)
    rec_info["dry_run"] = False
    return rec_info


def _do_rename(src: Path, dest: Path, dry: bool) -> dict:
    if not src.exists():
        raise FileNotFoundError(f"不存在: {src}")
    if dest.exists():
        raise FileExistsError(f"目标已存在: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry:
        return {"action": "rename", "source": str(src), "dest": str(dest), "dry_run": True}
    src.rename(dest)
    return {"action": "rename", "source": str(src), "dest": str(dest), "dry_run": False}


def _do_copy(src: Path, dest: Path, dry: bool) -> dict:
    if not src.exists():
        raise FileNotFoundError(f"不存在: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry:
        return {"action": "copy", "source": str(src), "dest": str(dest), "dry_run": True}
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest)
    return {"action": "copy", "source": str(src), "dest": str(dest), "dry_run": False}


def _do_move(src: Path, dest: Path, dry: bool) -> dict:
    if not src.exists():
        raise FileNotFoundError(f"不存在: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry:
        return {"action": "move", "source": str(src), "dest": str(dest), "dry_run": True}
    shutil.move(str(src), str(dest))
    return {"action": "move", "source": str(src), "dest": str(dest), "dry_run": False}


def build_parser() -> argparse.ArgumentParser:
    p = HelpfulParser(description="文件操作：delete / rename / copy / move")
    p.add_argument(
        "--action",
        required=True,
        choices=["delete", "rename", "copy", "move"],
        help="delete | rename | copy | move",
    )
    p.add_argument("--source", required=True, help="源路径")
    p.add_argument("--dest", help="目标路径（delete 时不需要；相对路径相对于 source 父目录）")
    p.set_defaults(dry_run=True)
    p.add_argument("--security_root", help="若指定，source/dest 均须落在该目录下")
    p.add_argument("--recursive", action="store_true", help="delete 目录时必须指定")
    p.add_argument("--dry_run", dest="dry_run", action="store_true", help="仅描述（默认）")
    p.add_argument("--commit", dest="dry_run", action="store_false", help="真实执行")
    p.add_argument(
        "--restrict_to_workspace",
        action="store_true",
        help="路径解析限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--run_type", choices=["auto", "plan", "execute"], default="", help="Plan 时拒绝写操作")
    p.add_argument("--json_out", action="store_true")
    return p


def agent_main(
    *,
    action: str,
    source: str,
    dest: str | None = None,
    security_root: str | None = None,
    recursive: bool = False,
    dry_run: bool = True,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    """扁平参数。delete 默认移入回收站（环境变量 AGENT_RECYCLE_ROOT）；在回收站内的 delete 为 purge。"""
    try:
        rt = str(run_type or "").strip().lower()
        dry = dry_run
        want_mutate = not dry
        act = str(action or "").strip().lower()
        if want_mutate and rt == "plan":
            return {
                "ok": False,
                "data": None,
                "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许文件操作"},
            }

        _, src, dest_p = _resolve_src_dest(
            source,
            dest,
            security_root=security_root,
            restrict_to_workspace=restrict_to_workspace,
        )

        if act == "delete":
            if dest is not None and str(dest).strip() != "":
                raise ValueError("delete 不需要 dest")
            data = _do_delete(src, recursive, dry)
        elif act in ("rename", "copy", "move"):
            if dest_p is None:
                raise ValueError(f"{act} 需要 dest")
            if act == "rename":
                data = _do_rename(src, dest_p, dry)
            elif act == "copy":
                data = _do_copy(src, dest_p, dry)
            else:
                data = _do_move(src, dest_p, dry)
        else:
            raise ValueError(f"未知 action: {action}")

        return ac.ok(data)
    except Exception as e:
        return ac.err(e)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    r = agent_main(
        action=str(args.action),
        source=str(args.source),
        dest=args.dest,
        security_root=args.security_root,
        recursive=bool(args.recursive),
        dry_run=bool(args.dry_run),
        restrict_to_workspace=bool(args.restrict_to_workspace),
        run_type=str(args.run_type or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
