# -*- coding: utf-8 -*-
"""
CLI 文件操作工具
================

用途
----
对文件或目录执行删除、重命名（同卷）、复制、移动（可跨卷）。

动作 --action
--------------
- delete：**安全删除**——将文件或目录**移入回收站目录**（默认 `BUILTIN_RECYCLE_ROOT`），不直接 unlink；若 `--source` 已在回收站根目录之下，则为**彻底删除**（真正删除，便于清空回收站）。删除目录仍须加 `--recursive`
- rename：`Path.rename`，源与目标须在同一逻辑卷（典型为同目录改名）
- copy：`copy2`（文件）或 `copytree`（目录，`dirs_exist_ok`）
- move：`shutil.move`（可跨卷；目标父目录不存在则创建）

安全
----
- 可选 `--root`：若指定，`--source` / `--dest` 解析后须均落在该根目录之下（防止误操作越界）。
- 可选 `--dryRun`：只返回将执行的操作描述，不写盘。
- **delete**：默认移入 `BUILTIN_RECYCLE_ROOT`（C:/AI_DATA_ROOT/AI_安全删除回收站，改常量或环境变量 AGENT_RECYCLE_ROOT 即可调整）；路径已在回收站下时 **purge** 为物理删除。勿对回收站根目录本身执行 delete。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import os
import shutil
import sys
import uuid

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


from cli_help_share import _capture_help, _HelpFulParser


# ── 回收站根目录默认路径：用户目录下 ~/AI_DATA_ROOT/AI_安全删除回收站 ──
BUILTIN_DRY_RUN = False
BUILTIN_RECYCLE_ROOT = Path.home() / "AI_DATA_ROOT" / "AI_安全删除回收站"


def _emit_json(ok: bool, data=None, error=None) -> None:
    print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))


def _ensure_under_root(root: Path, p: Path) -> None:
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"path 越出 --root: {p}") from None


def _parse_paths(args: argparse.Namespace) -> tuple[Path | None, Path, Path | None]:
    root = Path(args.root).resolve() if args.root else None
    src = Path(args.source).expanduser().resolve()
    dest = None
    if args.dest:
        raw = Path(args.dest).expanduser()
        if raw.is_absolute():
            dest = raw.resolve()
        else:
            # 相对路径：相对于 source 的父目录解析（rename/move/copy 均适用）
            dest = (src.parent / raw).resolve()
    if root is not None:
        _ensure_under_root(root, src)
        if dest is not None:
            _ensure_under_root(root, dest)
    return root, src, dest


def _recycle_bin_root() -> Path:
    """回收站根目录：优先环境变量 AGENT_RECYCLE_ROOT，否则硬编码默认值"""
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
                return {"action": "purge", "target": str(src), "kind": "file", "dryRun": True}
            src.unlink(missing_ok=False)
            return {"action": "purge", "target": str(src), "kind": "file", "dryRun": False}
        if src.is_dir():
            if not recursive:
                raise IsADirectoryError(f"是目录且未指定 --recursive: {src}")
            if dry:
                return {
                    "action": "purge",
                    "target": str(src),
                    "kind": "dir",
                    "recursive": True,
                    "dryRun": True,
                }
            shutil.rmtree(src)
            return {
                "action": "purge",
                "target": str(src),
                "kind": "dir",
                "recursive": True,
                "dryRun": False,
            }
        raise ValueError(f"无法识别类型: {src}")

    if src.is_dir() and not recursive:
        raise IsADirectoryError(f"是目录且未指定 --recursive: {src}")

    bucket = _unique_recycle_bucket(recycle)
    dest = bucket / src.name
    kind = "dir" if src.is_dir() else "file"
    rec_info = {
        "action": "recycle",
        "source": str(src),
        "recycledTo": str(dest),
        "recycleRoot": str(recycle.resolve()),
        "kind": kind,
        "recursive": bool(src.is_dir()),
        "dryRun": dry,
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
    rec_info["recycledTo"] = str(dest)
    rec_info["dryRun"] = False
    return rec_info


def _do_rename(src: Path, dest: Path, dry: bool) -> dict:
    if dest is None:
        raise ValueError("rename 需要 --dest")
    if not src.exists():
        raise FileNotFoundError(f"不存在: {src}")
    if dest.exists():
        raise FileExistsError(f"目标已存在: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry:
        return {"action": "rename", "source": str(src), "dest": str(dest), "dryRun": True}
    src.rename(dest)
    return {"action": "rename", "source": str(src), "dest": str(dest), "dryRun": False}


def _do_copy(src: Path, dest: Path, dry: bool) -> dict:
    if dest is None:
        raise ValueError("copy 需要 --dest")
    if not src.exists():
        raise FileNotFoundError(f"不存在: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry:
        return {"action": "copy", "source": str(src), "dest": str(dest), "dryRun": True}
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest)
    return {"action": "copy", "source": str(src), "dest": str(dest), "dryRun": False}


def _do_move(src: Path, dest: Path, dry: bool) -> dict:
    if dest is None:
        raise ValueError("move 需要 --dest")
    if not src.exists():
        raise FileNotFoundError(f"不存在: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry:
        return {"action": "move", "source": str(src), "dest": str(dest), "dryRun": True}
    shutil.move(str(src), str(dest))
    return {"action": "move", "source": str(src), "dest": str(dest), "dryRun": False}


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="文件操作：delete / rename / copy / move")
    p.add_argument(
        "--action",
        required=True,
        choices=["delete", "rename", "copy", "move"],
        help="delete | rename | copy | move",
    )
    p.add_argument("--source", required=True, help="源路径（delete 时为待删路径）")
    p.add_argument("--dest", help="目标路径（delete 时不需要）")
    p.add_argument("--root", help="若指定，source/dest 均须落在该目录之下")
    p.add_argument("--recursive", action="store_true", help="delete 目录时必须指定")
    p.add_argument("--dryRun", action="store_true", help="仅描述将执行的操作，不写盘")
    p.add_argument("--runType", choices=["auto", "plan", "execute"], default="", help="当前运行模式；plan 时操作被拒绝")
    p.add_argument("--jsonOut", action="store_true", help="输出 {ok,data,error} JSON")
    return p


def agent_main(
    *,
    action: str,
    source: str,
    dest: str | None = None,
    root: str | None = None,
    recursive: bool = False,
    dry_run: bool = False,
    run_type: str = "",
    parser_for_help: argparse.ArgumentParser | None = None,
) -> dict:
    """进程内入口；返回 {ok,data,error} 字典，不打印。"""
    dry = dry_run or BUILTIN_DRY_RUN
    if run_type == "plan":
        return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写操作"}}
    try:
        ns = SimpleNamespace(root=root, source=source, dest=dest)
        _, src, dest_p = _parse_paths(ns)

        if action == "delete":
            if dest:
                raise ValueError("delete 不需要 --dest")
            data = _do_delete(src, recursive, dry)
        elif action in ("rename", "copy", "move"):
            if dest_p is None:
                raise ValueError(f"{action} 需要 --dest")
            if action == "rename":
                data = _do_rename(src, dest_p, dry)
            elif action == "copy":
                data = _do_copy(src, dest_p, dry)
            else:
                data = _do_move(src, dest_p, dry)
        else:
            raise ValueError(f"未知 action: {action}")

        return {"ok": True, "data": data, "error": None}
    except Exception as e:
        msg = str(e) + ("\n\n--help:\n" + _capture_help(parser_for_help) if parser_for_help else "")
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": msg}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    env = agent_main(
        action=args.action,
        source=args.source,
        dest=args.dest,
        root=args.root,
        recursive=bool(args.recursive),
        dry_run=bool(args.dryRun),
        run_type=str(getattr(args, "runType", "") or "").strip().lower(),
        parser_for_help=parser,
    )
    if env["ok"]:
        if args.jsonOut:
            _emit_json(True, data=env["data"], error=None)
        else:
            print("ok")
            print(json.dumps(env["data"], ensure_ascii=False))
    else:
        err = env.get("error") or {}
        if args.jsonOut:
            _emit_json(False, data=None, error=err)
        else:
            print(str(err.get("message", "")), file=sys.stderr)
            raise RuntimeError(err.get("message", ""))


if __name__ == "__main__":
    main()
