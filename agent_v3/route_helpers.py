# -*- coding: utf-8 -*-
"""HTTP 与 agent_core 之间的薄适配：SSE 包装、会话选项。"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException

from agent_v3 import agent_core as core
from agent_v3.sse_events import context_layout_event, conversation_sse_event

__all__ = (
    "apply_conversation_request_options",
    "context_layout_event",
    "conversation_sse_event",
)


def apply_conversation_request_options(cid: str, mode: str, model: str) -> None:
    mod = str(model or "").strip()
    if mod:
        okm, _m = core.set_conversation_model(cid, mod)
        if not okm:
            raise HTTPException(400, "invalid model")
    m = str(mode or "").strip().lower()
    if m == "auto":
        core.CONVERSATION_MODES.pop(cid, None)
    elif m in ("plan", "execute"):
        core.CONVERSATION_MODES[cid] = m
