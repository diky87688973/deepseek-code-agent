# -*- coding: utf-8 -*-
"""将 unified diff 应用到 workspace root 下的文件（仅更新已存在文件，不支持 rename）。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import agent_patch_engine as pe
import agent_common as ac


def agent_main(
    *,
    path: str = "",
    patch_text: Optional[str] = None,
    patch_file: Optional[str] = None,
    dry_run: bool = True,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    confirm_id: str = "",
) -> dict:
    try:
        rt = str(run_type or "").strip().lower()
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}
        if want_write and not confirm_id:
            return {"ok": False, "data": None, "error": {"type": "ConfirmIdRequired", "message": "dry_run=false 必须传 confirm_id：请先 dry_run=true 预览，再用返回的 confirm_id 提交。"}}

        # ── confirm_id 模式：从预览存储恢复参数，模型无需重复传 patch_text ──
        if confirm_id:
            from agent_v4.live_state import consume_confirm_id
            stored = consume_confirm_id(str(confirm_id).strip())
            if stored is None:
                return {"ok": False, "data": None, "error": {"type": "InvalidConfirmId", "message": "confirm_id 无效或已过期，请重新 dry_run=true 预览"}}
            sp = stored.get("params", {})
            path = sp.get("path", path)
            patch_text = sp.get("patch_text")
            patch_file = sp.get("patch_file")
            restrict_to_workspace = sp.get("restrict_to_workspace", restrict_to_workspace)
            dry_run = False  # confirm_id 隐含用户已审核预览，强制写入

        if (patch_text is None) == (patch_file is None):
            raise ValueError("patch_text 与 patch_file 必须且只能提供一个")

        r = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        if not r.is_dir():
            raise ValueError(f"path 必须是目录: {r}")

        raw = pe.load_patch_text(patch_text=patch_text, patch_file=patch_file)
        file_patches = pe.parse_unified_diff(raw)
        changed: List[str] = []

        for fp in file_patches:
            old_p = str(fp["old_path"] or "")
            new_p = str(fp["new_path"] or "")
            if old_p != new_p and "/dev/null" not in (old_p, new_p):
                raise ValueError(
                    f"当前版本不支持 rename（--- 与 +++ 路径不一致: {old_p!r} vs {new_p!r}），"
                    "请保持同一文件；Windows 绝对路径勿省略 a/ b/ 前缀外的盘符。"
                )
            rel = Path(new_p if new_p != "/dev/null" else old_p)
            abs_path = (r / rel).resolve()
            try:
                abs_path.relative_to(r)
            except ValueError:
                raise ValueError(f"越界路径: {abs_path}")
            if not abs_path.is_file():
                raise FileNotFoundError(f"仅支持更新已存在文件: {abs_path}")
            new_content = pe.apply_file_patch(abs_path, fp["hunks"])
            if not dry_run:
                ac.write_unicode_file(abs_path, new_content, encoding="utf-8")
            changed.append(str(abs_path))

        # 生成 confirm_id 供后续免参数提交
        _confirm_id = ""
        if dry_run:
            from agent_v4.live_state import create_confirm_id, mark_confirmed
            _confirm_id = create_confirm_id("apply_patch", {
                "path": str(r),
                "patch_text": patch_text,
                "patch_file": patch_file,
                "restrict_to_workspace": restrict_to_workspace,
            })
            mark_confirmed(_confirm_id)

        return ac.ok(
            {
                "path": str(r),
                "dry_run": dry_run,
                "changed_files": changed,
                "diff_text": raw[:16000] + ("…" if len(raw) > 16000 else ""),
                "confirm_id": _confirm_id,
            }
        )
    except Exception as e:
        return ac.err(e)




