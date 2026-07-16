# -*- coding: utf-8 -*-
"""写门控 / 模式冲突 / 预览门；调用 host_quality，不注入 system。"""
from __future__ import annotations

from typing import Any, Dict, Optional  # Any used by gate_write_tool todo_list_mod

from agent_v4.core import host_quality as _host_quality
from agent_v4.core.tool_sets import PREVIEW_REQUIRED_SCRIPTS

# ── 写盘预览强制检查（集合见 tool_sets）──
_PREVIEW_PATH_SCRIPTS = PREVIEW_REQUIRED_SCRIPTS

_PREVIEW_REQUIRED_MSG = (
    "Execute/Auto 模式下禁止直接 dry_run=false 写入。"
    "请先对同一文件调用 dry_run=true 预览 diff，确认无误后再 dry_run=false 执行。"
)

# 会话级预览追踪（跨回合持久化）
_CONVERSATION_PREVIEWED = {}  # type: Dict[str, Dict[str, str]]

_PLAN_WRITE_BLOCK_MSG = (
    "当前为 Plan 模式，禁止执行写操作。请先切换为 Execute 模式后再执行。"
)
_EXECUTE_NO_TODO_MSG = (
    "当前为 Execute 模式，但未找到执行清单(Todo-List)。"
    "请先用 todo_list（action=create）创建执行清单后再执行写操作。"
)


def _check_write_preview(
    script: str,
    exec_args: dict,
    step_title: str,
    previewed_files: dict,
    written_files: dict,
):
    """强制 dry_run=true 预览后才允许 dry_run=false 写入。
    返回 None 表示通过检查；返回 dict 表示被拦截。
    注意：不在此处写入 written_files（须等真写成功后再记）。
    """
    sn = str(script or "")
    if sn not in _PREVIEW_PATH_SCRIPTS:
        return None
    path = _host_quality._resolve_write_path(exec_args, None)
    if not path:
        return None
    dr = exec_args.get("dry_run", True)
    if dr is True or dr == 1 or str(dr).strip().lower() in ("1", "true"):
        # 仅放行预览调用；previewed_files 须等工具成功返回后再记（见 agent_runtime）
        return None
    # confirm_id 模式下跳过预览指纹检查——质量门控层会验证 confirm_id 有效性
    if exec_args.get("confirm_id"):
        return None
    if path in previewed_files:
        return None
    return {
        "ok": False,
        "data": None,
        "error": {"type": "PreviewRequired", "message": _PREVIEW_REQUIRED_MSG},
    }


def _build_post_write_diagnostic(
    written_files: dict,
    conversation_id: str,
):
    """兼容旧名：质量报告已改挂写工具返回，不再生成独立 system 消息。"""
    return []


def _apply_host_quality_write_gate(
    conversation_id: str,
    script: str,
    exec_args: dict,
    step_title: str,
    previewed_files: dict,
    written_files: dict,
):
    """预览门 + 宿主质量门；返回 None 表示通过，否则返回拒绝 result。"""
    block = _check_write_preview(
        script, exec_args, step_title or "", previewed_files, written_files
    )
    if block:
        return block
    return _host_quality.check_pre_write_quality(
        conversation_id, script, exec_args, step_title=step_title or ""
    )


class HostPolicy:
    """Plan/Execute/Audit/todo/Preview + host_quality 预写检查。"""

    def check_write_preview(
        self,
        script: str,
        exec_args: dict,
        step_title: str,
        previewed_files: dict,
        written_files: dict,
    ):
        return _check_write_preview(
            script, exec_args, step_title, previewed_files, written_files
        )

    def apply_host_quality_write_gate(
        self,
        conversation_id: str,
        script: str,
        exec_args: dict,
        step_title: str,
        previewed_files: dict,
        written_files: dict,
    ):
        return _apply_host_quality_write_gate(
            conversation_id,
            script,
            exec_args,
            step_title,
            previewed_files,
            written_files,
        )

    def gate_write_tool(
        self,
        conversation_id: str,
        script: str,
        exec_args: dict,
        *,
        step_title: str,
        previewed_files: dict,
        written_files: dict,
        current_mode: str,
        audit_only: bool,
        audit_block_msg: str,
        todo_list_mod: Any,
    ) -> Optional[Dict[str, Any]]:
        """写类工具执行前门控。返回 None 表示放行；否则返回拒绝 result。"""
        if audit_only:
            return {
                "ok": False,
                "data": None,
                "error": {"type": "AuditOnly", "message": audit_block_msg},
            }
        if current_mode == "plan":
            if script == "replace_in_file":
                _dr = exec_args.get("dry_run", True)
                if _dr is False or _dr == 0:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {"type": "ModeConflict", "message": _PLAN_WRITE_BLOCK_MSG},
                    }
                return None
            return {
                "ok": False,
                "data": None,
                "error": {"type": "ModeConflict", "message": _PLAN_WRITE_BLOCK_MSG},
            }
        if current_mode == "execute":
            _todo = todo_list_mod.session_lists.get(conversation_id)
            _no_todo = _todo is None or not _todo.get("items")
            if _no_todo:
                return {
                    "ok": False,
                    "data": None,
                    "error": {"type": "ModeConflict", "message": _EXECUTE_NO_TODO_MSG},
                }
            return _apply_host_quality_write_gate(
                conversation_id,
                script,
                exec_args,
                step_title or "",
                previewed_files,
                written_files,
            )
        # Auto：不拦截模式，但强制预览 + 宿主质量门控
        return _apply_host_quality_write_gate(
            conversation_id,
            script,
            exec_args,
            step_title or "",
            previewed_files,
            written_files,
        )
