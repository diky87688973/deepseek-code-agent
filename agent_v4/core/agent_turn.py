# -*- coding: utf-8 -*-
"""agent_v4.core.agent_turn — 薄委托 AgentRuntime；再导出门控符号供脚本/测试（过渡期）。

新代码请优先 `from agent_v4.api_surface import ...` 或 `agent_v4.runtime.host_policy`。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 触发 bind_core_modules，保持「直接 import agent_turn」与旧行为一致
from agent_v4.core import base as _core_base  # noqa: F401
from agent_v4.core import host_quality as _host_quality  # noqa: F401 — 验证脚本/绑定可见
from agent_v4.runtime.host_policy import (
    HostPolicy,
    _CONVERSATION_PREVIEWED,
    _PREVIEW_PATH_SCRIPTS,
    _PREVIEW_REQUIRED_MSG,
    _apply_host_quality_write_gate,
    _build_post_write_diagnostic,
    _check_write_preview,
)


def run_agent_turn(
    conversation_id: str,
    user_text: str,
    client_ip: str = "",
    mode_hint: str = "",
    *,
    resume_after_user_confirm: bool = False,
    run_id: str = "",
    attachments: Optional[List[Dict[str, Any]]] = None,
):
    """Yields SSE lines (without prefix) as dicts (caller wraps data:)."""
    from agent_v4.runtime.agent_runtime import AgentRuntime

    yield from AgentRuntime().run_turn(
        conversation_id,
        user_text,
        client_ip,
        mode_hint,
        resume_after_user_confirm=resume_after_user_confirm,
        run_id=run_id,
        attachments=attachments,
    )
