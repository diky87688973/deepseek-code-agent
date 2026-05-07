# -*- coding: utf-8 -*-
"""Per-conversation model id (独立封装). 可用 CHAT_API_MODELS 覆盖白名单。"""

from __future__ import annotations

import os
from typing import Dict, Tuple

_DEFAULT_MODELS: Tuple[str, ...] = ("deepseek-v4-pro", "deepseek-v4-flash")


def _parse_models_csv(raw: str) -> Tuple[str, ...]:
    parts = [x.strip() for x in raw.replace(",", " ").split() if x.strip()]
    return tuple(dict.fromkeys(parts))


def _allowed_models_from_env() -> Tuple[str, ...]:
    v = (os.environ.get("CHAT_API_MODELS") or "").strip()
    if v:
        return _parse_models_csv(v)
    return _DEFAULT_MODELS


ALLOWED_MODELS: Tuple[str, ...] = _allowed_models_from_env()


def default_model_from_env() -> str:
    for key in ("CHAT_API_DEFAULT_MODEL", "DEEPSEEK_MODEL"):
        v = (os.environ.get(key) or "").strip()
        if v and v in ALLOWED_MODELS:
            return v
    if "deepseek-v4-flash" in ALLOWED_MODELS:
        return "deepseek-v4-flash"
    return ALLOWED_MODELS[0] if ALLOWED_MODELS else "deepseek-v4-flash"


_CONVERSATION_MODELS: Dict[str, str] = {}


def effective_model(conversation_id: str) -> str:
    cid = str(conversation_id or "").strip()
    if not cid:
        return default_model_from_env()
    m = _CONVERSATION_MODELS.get(cid)
    if m and m in ALLOWED_MODELS:
        return m
    return default_model_from_env()


def set_conversation_model(conversation_id: str, model: str) -> Tuple[bool, str]:
    cid = str(conversation_id or "").strip()
    m = str(model or "").strip()
    if not cid or m not in ALLOWED_MODELS:
        return False, ""
    _CONVERSATION_MODELS[cid] = m
    return True, m
