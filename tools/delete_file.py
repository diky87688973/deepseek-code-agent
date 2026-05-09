# -*- coding: utf-8 -*-
"""安全删除：将文件移到宿主配置的回收目录（非 unlink），便于审计与恢复。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import agent_common as ac

_trash_root: Path | None = None


def configure_trash_root(root: Path) -> None:
    """由 deepseek `DATA_ROOT / \"safe_delete\"` 调用。"""
    global _trash_root
    _trash_root = Path(root)


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
    allow_outside_workspace: bool = False,
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

        if _trash_root is None:
            return ac.err(RuntimeError("delete_file: 未配置回收目录（宿主应调用 configure_trash_root）"))

        fp = ac.resolve_path(path, allow_outside_workspace=allow_outside_workspace)
        if not fp.is_file():
            return ac.err(FileNotFoundError(f"不是已存在文件: {fp}"))

        if dry_run:
            dest_preview = _preview_dest(_trash_root, fp)
            return ac.ok(
                {
                    "path": str(fp),
                    "dry_run": True,
                    "would_move_to": str(dest_preview),
                    "trash_root": str(_trash_root),
                }
            )

        dest = _unique_dest(_trash_root, fp)
        shutil.move(str(fp), str(dest))
        return ac.ok({"path": str(fp), "moved_to": str(dest), "dry_run": False})
    except Exception as e:
        return ac.err(e)


def main() -> None:
    p = argparse.ArgumentParser(description="delete_file（调试）")
    p.add_argument("--path", required=True)
    p.add_argument("--dryRun", action="store_true", default=True)
    p.add_argument("--commit", action="store_false", dest="dryRun", help="真正移到回收目录（关闭 dryRun）")
    p.add_argument("--allowOutsideWorkspace", action="store_true")
    p.add_argument("--runType", default="")
    p.add_argument("--jsonOut", action="store_true")
    args = p.parse_args()
    r = agent_main(
        path=args.path,
        dry_run=bool(args.dryRun),
        allow_outside_workspace=bool(args.allowOutsideWorkspace),
        run_type=str(args.runType or ""),
    )
    if args.jsonOut:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok"):
            print(json.dumps(r.get("data"), ensure_ascii=False))
        else:
            print((r.get("error") or {}).get("message", ""), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
