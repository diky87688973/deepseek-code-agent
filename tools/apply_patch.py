# -*- coding: utf-8 -*-
"""将 unified diff 应用到 workspace root 下的文件（仅更新已存在文件，不支持 rename）。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import agent_patch_engine as pe
import agent_common as ac


def agent_main(
    *,
    path: str = "",
    patch_text: Optional[str] = None,
    patch_file: Optional[str] = None,
    dry_run: bool = True,
    backup: bool = True,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    confirm_id: str = "",
    cancel_previous: bool = False,
) -> dict:
    try:
        rt = str(run_type or "").strip().lower()
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}
        if want_write and not confirm_id:
            return {"ok": False, "data": None, "error": {"type": "ConfirmIdRequired", "message": "dry_run=false 必须传 confirm_id：请先 dry_run=true 预览，再用返回的 confirm_id 提交。"}}

        # ── confirm_id 模式校验：检测模型是否同时传了编辑参数 ──
        _EDIT_KEYS = ["path", "patch_text", "patch_file", "restrict_to_workspace"]
        _EDIT_VALS = {k: v for k, v in locals().items() if k in _EDIT_KEYS}
        _DEFAULTS = {"path": "", "restrict_to_workspace": False}
        if confirm_id and not dry_run:
            _extra = [k for k, v in _EDIT_VALS.items()
                      if v is not None and v != _DEFAULTS.get(k) and v != [] and v != {}]
            if _extra:
                return {"ok": False, "data": None, "error": {
                    "type": "BadToolArguments",
                    "message": "确认提交模式（dry_run=false + confirm_id）不接受编辑参数。"
                               f"你同时传了 confirm_id 和以下参数：{', '.join(_extra)}。"
                               "请只传 confirm_id + dry_run=false，编辑参数从预览存储自动恢复。",
                }}

        # ── confirm_id 模式：从预览存储恢复参数，模型无需重复传 patch_text ──
        if confirm_id:
            from agent_v4.live_state import consume_confirm_id, confirm_id_needs_review
            cid_str = str(confirm_id).strip()
            if confirm_id_needs_review(cid_str):
                return {"ok": False, "data": None, "error": {"type": "DiffReviewRequired", "message": "请先调用 review_conclusion(confirm_id=...) 提交 diff review 结论后，再 dry_run=false 提交。"}}
            stored = consume_confirm_id(cid_str)
            if stored is None:
                return {"ok": False, "data": None, "error": {"type": "InvalidConfirmId", "message": "confirm_id 无效或已过期，请重新 dry_run=true 预览"}}
            if stored.get("action") != "apply_patch":
                return {"ok": False, "data": None, "error": {"type": "CrossToolConfirmId", "message": f"该 confirm_id 由 {stored.get('action','?')} 创建，apply_patch 无法消费，请使用创建该 ID 的原始工具提交"}}
            sp = stored.get("params", {})
            path = sp.get("path", path)
            patch_text = sp.get("patch_text", patch_text)
            patch_file = sp.get("patch_file", patch_file)
            restrict_to_workspace = sp.get("restrict_to_workspace", restrict_to_workspace)
            dry_run = False  # confirm_id 隐含已审查，强制写入

        if (patch_text is None) == (patch_file is None):
            raise ValueError("patch_text 与 patch_file 必须且只能提供一个")

        r = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        if not r.is_dir():
            raise ValueError(f"path 必须是目录: {r}")

        raw = pe.load_patch_text(patch_text=patch_text, patch_file=patch_file)
        file_patches = pe.parse_unified_diff(raw)
        changed: List[str] = []
        file_backups: Dict[str, str] = {}

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
                # 版本化备份（每个文件写前单独备份）
                mod_id: Optional[str] = None
                if backup:
                    original = ac.read_file_text(abs_path, encoding="utf-8")
                    mod_id = ac.create_file_backup(
                        abs_path, original, "utf-8", "patch", ""
                    )
                    file_backups[str(abs_path)] = mod_id
                ac.write_unicode_file(abs_path, new_content, encoding="utf-8")
            changed.append(str(abs_path))

        # 生成 confirm_id 供后续免参数提交
        _confirm_id = ""
        if dry_run:
            from agent_v4.live_state import create_confirm_id, has_pending_confirm_for_path, invalidate_confirm_ids_for_path
            if cancel_previous:
                invalidate_confirm_ids_for_path(str(r))
            elif has_pending_confirm_for_path(str(r)):
                return {"ok": False, "data": None, "error": {"type": "PendingPreviewExists", "message": "该文件已有未提交的预览，请先 review_conclusion(confirm_id=..., cancel_preview=True) 取消，或 review_conclusion(confirm_id=...) 提交后再操作。"}}
            _confirm_id = create_confirm_id("apply_patch", {
                "path": str(r),
                "patch_text": patch_text,
                "patch_file": patch_file,
                "restrict_to_workspace": restrict_to_workspace,
            }, require_diff_review=True)

        from agent_v4.live_state import invalidate_confirm_ids_for_path
        for _p in changed:
            invalidate_confirm_ids_for_path(_p)
        return ac.ok(
            {
                "path": str(r),
                "dry_run": dry_run,
                "changed_files": changed,
                "file_backups": file_backups if not dry_run else {},
                "diff_text": raw[:16000] + ("…" if len(raw) > 16000 else ""),
                "confirm_id": _confirm_id,
            }
        )
    except Exception as e:
        return ac.err(e)




