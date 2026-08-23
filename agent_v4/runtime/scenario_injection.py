# -*- coding: utf-8 -*-
"""flatten 之后的 API 尾注入：requires_reply / 截图 / wrap。不含 mode_tail。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class ScenarioInjection:
    """仅负责七段 + sanitize 之后的 ephemeral API 尾（不落盘、不进 CHUNK）。"""

    def sync_ephemeral_tail(
        self,
        messages: List[Dict[str, Any]],
        conversation_id: str,
        *,
        attachments: List[Dict[str, Any]],
        looked_screenshot: bool,
        vision_nudge_text: str,
        get_turn_attachments,
        ephemeral_attachment_tail,
        find_pending_requires_reply_peer_message,
        ephemeral_requires_reply_priority,
        vision_ok: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """requires_reply 与截图提示可并存；应答 RR / 看图后按状态重建，避免互相覆盖清空。"""
        parts = []  # type: List[str]
        _pending = find_pending_requires_reply_peer_message(messages)
        if _pending is not None:
            _rr = ephemeral_requires_reply_priority(
                str(_pending.get("_sender") or ""),
                str(_pending.get("_thread_id") or ""),
            )
            _c = str((_rr or {}).get("content") or "").strip()
            if _c:
                parts.append(_c)
        _cur_atts = get_turn_attachments(conversation_id) or attachments
        if _cur_atts and not looked_screenshot and not vision_ok:
            if vision_nudge_text:
                parts.append(vision_nudge_text)
            else:
                _att_tail = ephemeral_attachment_tail(_cur_atts)
                _c2 = str((_att_tail or {}).get("content") or "").strip()
                if _c2:
                    parts.append(_c2)
        if parts:
            return {"role": "system", "content": "\n\n".join(parts)}
        return None

    def with_ephemeral_tail(
        self,
        api_messages: List[Dict[str, Any]],
        tail: Optional[Dict[str, Any]],
        api_messages_with_ephemeral_tail,
    ) -> List[Dict[str, Any]]:
        return api_messages_with_ephemeral_tail(api_messages, tail)

    def max_tool_rounds_wrap_user(self, ephemeral_max_tool_rounds_wrap_user) -> Dict[str, Any]:
        return ephemeral_max_tool_rounds_wrap_user()

    def vision_nudge_content(self) -> str:
        return (
            "【宿主提醒】本轮有截图但尚未成功调用 look_screenshot。"
            "请先调用；prompt 表达用户意图并导向用户需要的答案，不要臆测画面。path / paths / attachment_ids 必须至少传其一。"
        )
