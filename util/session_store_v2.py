# -*- coding: utf-8 -*-
"""SessionDesign.java：会话 ID 规范化集中一处（最小改动，不改变现网校验语义）。"""
from __future__ import annotations

import re
import uuid
from typing import Optional

_CID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def parse_conversation_id(s: Optional[str]) -> Optional[str]:
    """合法则返回 strip 后的 id，否则 None（HTTP 层应转 400）。"""
    raw = str(s or "").strip()
    if not raw:
        return None
    if _CID_RE.match(raw):
        return raw
    return None


def new_conversation_id() -> str:
    """与 chat_stream 默认 uuid 策略一致。"""
    return str(uuid.uuid4())
