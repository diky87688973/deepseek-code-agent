# -*- coding: utf-8 -*-
"""写入或覆盖文本文件。默认 dry_run 不落盘。"""

from __future__ import annotations

import difflib
from typing import Optional

import agent_common as ac


def agent_main(
    *,
    path: str = "",
    content: str = "",
    encoding: str = "utf-8",
    dry_run: bool = True,
    create_only: bool = False,
    backup: bool = True,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    confirm_id: str = "",
) -> dict:
    """dry_run=True 时只返回元信息不落盘。run_type=plan 时禁止实际写盘。"""
    try:
        rt = str(run_type or "").strip().lower()
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}
        if want_write and not confirm_id:
            return {"ok": False, "data": None, "error": {"type": "ConfirmIdRequired", "message": "dry_run=false 必须传 confirm_id：请先 dry_run=true 预览，再用返回的 confirm_id 提交。"}}

        # ── confirm_id 模式校验：检测模型是否同时传了编辑参数 ──
        _CONFIRM_EDIT_PARAMS = {
            "path": path, "content": content, "encoding": encoding,
            "create_only": create_only, "restrict_to_workspace": restrict_to_workspace,
        }
        _DEFAULTS = {"path": "", "content": "", "encoding": "utf-8", "create_only": False, "restrict_to_workspace": False}
        if confirm_id and not dry_run:
            _extra = [k for k, v in _CONFIRM_EDIT_PARAMS.items() if v != _DEFAULTS.get(k)]
            if _extra:
                return {"ok": False, "data": None, "error": {
                    "type": "BadToolArguments",
                    "message": "确认提交模式（dry_run=false + confirm_id）不接受编辑参数。"
                               f"你同时传了 confirm_id 和以下参数：{', '.join(_extra)}。"
                               "请只传 confirm_id + dry_run=false，编辑参数从预览存储自动恢复。",
                }}

        # ── confirm_id 模式：从预览存储恢复参数，模型无需重复传 content ──
        if confirm_id:
            from agent_v4.live_state import consume_confirm_id, confirm_id_needs_review
            cid_str = str(confirm_id).strip()
            if confirm_id_needs_review(cid_str):
                return {"ok": False, "data": None, "error": {"type": "DiffReviewRequired", "message": "请先调用 review_conclusion(confirm_id=...) 提交 diff review 结论后，再 dry_run=false 提交。"}}
            stored = consume_confirm_id(cid_str)
            if stored is None:
                return {"ok": False, "data": None, "error": {"type": "InvalidConfirmId", "message": "confirm_id 无效或已过期，请重新 dry_run=true 预览"}}
            sp = stored.get("params", {})
            path = sp.get("path", path)
            content = sp.get("content", content)
            encoding = sp.get("encoding", encoding)
            create_only = sp.get("create_only", create_only)
            restrict_to_workspace = sp.get("restrict_to_workspace", restrict_to_workspace)
            dry_run = False  # confirm_id 隐含已审查，强制写入

        fp = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        existed = fp.is_file()
        if create_only and existed:
            raise FileExistsError(f"create_only：文件已存在 {fp}")

        original = ac.read_file_text(fp, encoding) if existed else ""
        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(),
                content.splitlines(),
                fromfile=str(fp) if existed else "/dev/null",
                tofile=str(fp),
                lineterm="",
                n=3,
            )
        )
        diff_text = "\n".join(diff_lines) if diff_lines else ""
        preview = {
            "path": str(fp),
            "encoding": encoding,
            "byte_length_approx": len(content.encode(encoding, errors="replace")),
            "existed_before": existed,
            "dry_run": dry_run,
            "changed": content != original,
            "diff_text": diff_text[:16000] + ("…" if len(diff_text) > 16000 else ""),
        }
        # 生成 confirm_id 供后续免参数提交
        _confirm_id = ""
        if dry_run:
            from agent_v4.live_state import create_confirm_id, has_pending_confirm_for_path
            if has_pending_confirm_for_path(str(fp)):
                return {"ok": False, "data": None, "error": {"type": "PendingPreviewExists", "message": "该文件已有未提交的预览，请先 review_conclusion(confirm_id=..., cancel_preview=True) 取消，或 review_conclusion(confirm_id=...) 提交后再操作。"}}
            _confirm_id = create_confirm_id("write_file", {
                "path": str(fp),
                "content": content,
                "encoding": encoding,
                "create_only": create_only,
                "restrict_to_workspace": restrict_to_workspace,
            }, require_diff_review=True)

        if dry_run:
            return ac.ok({**preview, "written": False, "confirm_id": _confirm_id})

        # 版本化备份
        mod_id: Optional[str] = None
        if backup and fp.is_file():
            mod_id = ac.create_file_backup(
                fp, original, encoding, "write", diff_text
            )
        ac.write_unicode_file(fp, content, encoding=encoding)
        from agent_v4.live_state import invalidate_confirm_ids_for_path
        invalidate_confirm_ids_for_path(str(fp))
        return ac.ok({**preview, "written": True, "mod_id": mod_id})
    except Exception as e:
        return ac.err(e)




