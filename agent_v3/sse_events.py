# -*- coding: utf-8 -*-
"""SSE 事件组装：绑定 conversation_id、context_layout 等（agent_core 与 HTTP 层共用）。"""
from __future__ import annotations

from typing import Any, Dict, List

from util.http_pipeline_v2 import bind_conversation_id_to_sse_event


def conversation_sse_event(cid: str, ev: Dict[str, Any]) -> Dict[str, Any]:
    """为 SSE 事件绑定 conversation_id（对齐 HttpServiceDesign / v2 pipeline）。"""
    return bind_conversation_id_to_sse_event(cid, ev)


def context_layout_event(conversation_id: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """当前 messages 下的上下文视图（供底部比例条 / 悬浮层）。"""
    from agent_v3 import agent_core

    return conversation_sse_event(
        conversation_id,
        {"type": "context_layout", **agent_core._compute_context_layout_payload(conversation_id, messages)},
    )
