# -*- coding: utf-8 -*-
"""文件版本化备份回滚工具。支持 list 查看备份历史、undo 回滚到指定版本。"""

from __future__ import annotations

from typing import Optional

import json

import agent_common as ac


def agent_main(
    *,
    action: str = "list",
    mod_id: Optional[str] = None,
    path: Optional[str] = None,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    """
    回滚 replace_in_file 的版本化备份。

    - action="list" + path → 列出指定文件的历史备份
    - action="undo" + mod_id → 回滚到指定备份版本
    """
    _ = run_type
    try:
        rt = str(run_type or "").strip().lower()
        if action == "list":
            if not path:
                raise ValueError("action=list 时须提供 path")
            fp = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
            backups = ac.list_backups(fp)
            return ac.ok({
                "path": str(fp.resolve()),
                "backups": backups,
                "total": len(backups),
            })

        elif action == "undo":
            if rt == "plan":
                return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许回滚写文件"}}
            if not mod_id:
                raise ValueError("action=undo 时须提供 mod_id")

            # 读取备份信息
            original_path, backup_content, encoding = ac.restore_backup(mod_id)
            fp = ac.resolve_path(original_path, allow_outside_workspace=not restrict_to_workspace)

            if not fp.is_file():
                raise FileNotFoundError(f"目标文件不存在: {fp}")

            # 1. 先备份当前文件
            current = ac.read_file_text(fp, encoding)
            new_mod_id = ac.create_file_backup(
                fp, current, encoding, "undo_pre", ""
            )

            # 2. 写入备份内容
            ac.write_unicode_file(fp, backup_content, encoding=encoding)
            return ac.ok({
                "action": "undo",
                "mod_id": mod_id,
                "restored_path": str(fp.resolve()),
                "pre_undo_backup_mod_id": new_mod_id,
                "message": f"已回滚备份 {mod_id}，回滚前当前文件已备份为 {new_mod_id}",
            })

        elif action == "diff":
            if not mod_id:
                raise ValueError("action=diff 时须提供 mod_id")
            original_path, backup_content, encoding = ac.restore_backup(mod_id)
            if not path:
                path = original_path
            fp = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
            if not fp.is_file():
                raise FileNotFoundError(f"文件不存在: {fp}")
            current = ac.read_file_text(fp, encoding)
            import difflib
            diff_lines = list(difflib.unified_diff(
                backup_content.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile=f"备份/{mod_id[:12]}",
                tofile=str(fp.name),
            ))
            diff_text = "".join(diff_lines)
            # 读取备份元数据中的时间戳用于 step_title
            _bak_dir = ac.backup_dir_for_mod(mod_id)
            _meta_file = _bak_dir / "metadata.json"
            _ts = ""
            try:
                _meta = json.loads(_meta_file.read_text(encoding="utf-8"))
                _raw_ts = str(_meta.get("timestamp") or "")
                if len(_raw_ts) >= 16:
                    _ts = _raw_ts[11:16]
            except Exception:
                pass
            return ac.ok({
                "action": "diff",
                "mod_id": mod_id,
                "path": str(fp.resolve()),
                "backup_time": _ts,
                "diff_text": diff_text[:16000] + ("…" if len(diff_text) > 16000 else ""),
            })

        else:
            raise ValueError(f"不支持的 action: {action}，仅支持 list、undo 和 diff")

    except Exception as e:
        return ac.err(e)




