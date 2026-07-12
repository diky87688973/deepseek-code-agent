# -*- coding: utf-8 -*-
"""安全删除：将文件移到宿主配置的回收目录（非 unlink），便于审计与恢复。

回收根目录由宿主 `configure_trash_root` 注入，与 file_ops / main_tray 共用
`AGENT_RECYCLE_ROOT`（默认 `DATA_ROOT/AI_安全删除回收站`）。
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import agent_common as ac

_trash_root: Optional[Path] = None


def configure_trash_root(root: Path) -> None:
    """由宿主 bootstrap / main_tray 调用，与 AGENT_RECYCLE_ROOT 对齐。"""
    global _trash_root
    _trash_root = Path(root)


def _resolved_trash_root() -> Optional[Path]:
    if _trash_root is not None:
        return _trash_root
    env = str(os.environ.get("AGENT_RECYCLE_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    return None


def _preview_dest(trash_root: Path, src: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:10]
    base = src.name or "file"
    dest_dir = trash_root / stamp[:8]
    return dest_dir / f"{stamp}_{short}_{base}"


def _unique_dest(trash_root: Path, src: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:10]
    base = src.name or "file"
    dest_dir = trash_root / stamp[:8]
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest_dir / f"{stamp}_{short}_{base}"
    n = 0
    while candidate.exists():
        n += 1
        candidate = dest_dir / f"{stamp}_{short}_{n}_{base}"
    return candidate


def agent_main(
    *,
    path: str,
    dry_run: bool = True,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    try:
        rt = str(run_type or "").strip().lower()
        want_move = not dry_run
        if want_move and rt == "plan":
            return {
                "ok": False,
                "data": None,
                "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许删除文件"},
            }

        trash = _resolved_trash_root()
        if trash is None:
            return ac.err(
                RuntimeError(
                    "delete_file: 未配置回收目录（宿主应调用 configure_trash_root，"
                    "或设置环境变量 AGENT_RECYCLE_ROOT）"
                )
            )

        fp = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        if not fp.is_file():
            return ac.err(FileNotFoundError(f"不是已存在文件: {fp}"))

        if dry_run:
            dest_preview = _preview_dest(trash, fp)
            return ac.ok(
                {
                    "path": str(fp),
                    "dry_run": True,
                    "would_move_to": str(dest_preview),
                    "trash_root": str(trash),
                }
            )

        dest = _unique_dest(trash, fp)
        shutil.move(str(fp), str(dest))
        return ac.ok(
            {
                "path": str(fp),
                "moved_to": str(dest),
                "trash_root": str(trash),
                "dry_run": False,
            }
        )
    except Exception as e:
        return ac.err(e)
