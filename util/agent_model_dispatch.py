# -*- coding: utf-8 -*-
"""Per-conversation model id. 通过 config.ini 的 [model] allowed_models / default_model 配置。"""

from __future__ import annotations

import json
from typing import Dict, Tuple

from util.config_loader import load_config

_AGENT_CONFIG = load_config(verbose=False)


def _parse_models_csv(raw: str) -> Tuple[str, ...]:
    parts = [x.strip() for x in raw.replace(",", " ").split() if x.strip()]
    return tuple(dict.fromkeys(parts))


def _load_allowed_models() -> Tuple[str, ...]:
    raw = str(_AGENT_CONFIG.get("AGENT_ALLOWED_MODELS") or "").strip()
    if raw:
        return _parse_models_csv(raw)
    raise ValueError(
        "AGENT_ALLOWED_MODELS 未设置！请在 config.ini 的 [model] 节配置 allowed_models "
        "（如: allowed_models = deepseek-v4-pro, deepseek-v4-flash）"
        "或设置环境变量 CHAT_API_MODELS"
    )


def _load_model_context_token_map() -> Dict[str, int]:
    raw = str(_AGENT_CONFIG.get("AGENT_MODEL_CONTEXT_TOKENS_JSON") or "").strip()
    if not raw:
        raise ValueError(
            "AGENT_MODEL_CONTEXT_TOKENS_JSON 未设置！请在 config.ini 的 [model] 节配置 "
            "model_context_tokens_json（JSON 对象，可含 __default__）。"
        )
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("AGENT_MODEL_CONTEXT_TOKENS_JSON 必须是 JSON 对象。")
    out: Dict[str, int] = {}
    for k, v in data.items():
        key = str(k or "").strip()
        if not key:
            continue
        try:
            out[key] = max(1, int(v))
        except (TypeError, ValueError):
            continue
    if not out:
        raise ValueError("AGENT_MODEL_CONTEXT_TOKENS_JSON 未包含有效 token 上限。")
    return out


ALLOWED_MODELS: Tuple[str, ...] = _load_allowed_models()
MODEL_CONTEXT_TOKEN_MAP: Dict[str, int] = _load_model_context_token_map()


def default_model_from_env() -> str:
    raw = str(_AGENT_CONFIG.get("AGENT_DEFAULT_MODEL") or "").strip()
    if raw and raw in ALLOWED_MODELS:
        return raw
    if ALLOWED_MODELS:
        return ALLOWED_MODELS[0]
    raise ValueError("无可用模型，请在 config.ini 中配置 allowed_models")


_CONVERSATION_MODELS: Dict[str, str] = {}


def effective_model(conversation_id: str) -> str:
    cid = str(conversation_id or "").strip()
    if not cid:
        return default_model_from_env()
    m = _CONVERSATION_MODELS.get(cid)
    if m and m in ALLOWED_MODELS:
        return m
    return default_model_from_env()


def model_max_context_tokens(model_id: str) -> int:
    """模型上下文窗口 token 上限（来自 config.ini model_context_tokens_json）。"""
    mid = str(model_id or "").strip()
    if mid in MODEL_CONTEXT_TOKEN_MAP:
        return int(MODEL_CONTEXT_TOKEN_MAP[mid])
    default = MODEL_CONTEXT_TOKEN_MAP.get("__default__")
    if default is not None:
        return int(default)
    if MODEL_CONTEXT_TOKEN_MAP:
        return int(next(iter(MODEL_CONTEXT_TOKEN_MAP.values())))
    raise ValueError("无法解析 model_max_context_tokens：配置为空。")


def set_conversation_model(conversation_id: str, model: str) -> Tuple[bool, str]:
    cid = str(conversation_id or "").strip()
    m = str(model or "").strip()
    if not cid or m not in ALLOWED_MODELS:
        return False, ""
    _CONVERSATION_MODELS[cid] = m
    return True, m
