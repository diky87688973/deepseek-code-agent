# -*- coding: utf-8 -*-
"""HttpServiceDesign.java 中与「装配边界」相关的薄封装（最小改动）。

- bind_conversation_id_to_sse_event ↔ ConversationSse.bindCid
- resolve_client_ip_from_request ↔ ClientIpResolver.resolve
"""
from __future__ import annotations

from typing import Any, Callable, Dict

try:
    from fastapi import Request
except ImportError:  # 类型检查或轻量环境
    Request = Any  # type: ignore


def bind_conversation_id_to_sse_event(cid: str, ev: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(ev or {})
    _cid = str(cid or "").strip()
    if _cid:
        out["conversation_id"] = _cid
    else:
        out.setdefault("conversation_id", "")
    return out


def resolve_client_ip_from_request(request: Request, normalize_fn: Callable[[str], str]) -> str:
    client_ip = ""
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        client_ip = xff.split(",")[0].strip()
    if not client_ip:
        client_ip = (request.headers.get("x-real-ip") or "").strip()
    if not client_ip and request.client is not None:
        client_ip = str(request.client.host or "").strip()
    return normalize_fn(client_ip)
