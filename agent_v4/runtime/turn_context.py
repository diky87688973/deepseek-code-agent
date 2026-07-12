# -*- coding: utf-8 -*-
"""单次 turn 可变态（不装全局表 / SSE）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class TurnContext:
    """一次 Agent turn 的局部状态。"""

    __slots__ = (
        "conversation_id",
        "run_id",
        "mode",
        "messages",
        "attachments",
        "resume_after_user_confirm",
        "active_round_id",
        "previewed_files",
        "written_files",
        "ephemeral_tail",
        "looked_screenshot",
        "vision_nudge_used",
        "vision_nudge_text",
        "turn_tool_records",
        "turn_tool_invocations_used",
    )

    def __init__(
        self,
        conversation_id: str,
        *,
        run_id: str = "",
        mode: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        resume_after_user_confirm: bool = False,
        active_round_id: Optional[str] = None,
        previewed_files: Optional[Dict[str, str]] = None,
        written_files: Optional[Dict[str, str]] = None,
    ) -> None:
        self.conversation_id = str(conversation_id or "").strip()
        self.run_id = str(run_id or "")
        self.mode = str(mode or "")
        self.messages = list(messages or [])
        self.attachments = list(attachments or [])
        self.resume_after_user_confirm = bool(resume_after_user_confirm)
        self.active_round_id = active_round_id
        self.previewed_files = previewed_files if previewed_files is not None else {}
        self.written_files = written_files if written_files is not None else {}
        self.ephemeral_tail = None  # type: Optional[Dict[str, Any]]
        self.looked_screenshot = False
        self.vision_nudge_used = False
        self.vision_nudge_text = ""
        self.turn_tool_records = []  # type: List[Dict[str, Any]]
        self.turn_tool_invocations_used = 0
