# -*- coding: utf-8
"""agent_v4.core.usage_accum"""
from __future__ import annotations

from agent_v4.core.deps import *  # noqa: F403
from agent_v4.core.shared_state import *  # noqa: F403

def _default_usage_accum_dict() -> Dict[str, int]:
    return {
        "session_token_used": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_cache_hit_tokens": 0,
        "total_cache_miss_tokens": 0,
    }

def _load_usage_accumulator() -> Dict[str, int]:
    base = _default_usage_accum_dict()
    try:
        if not USAGE_ACCUM_FILE.is_file():
            return base
        raw = json.loads(USAGE_ACCUM_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(raw, dict):
        return base
    out: Dict[str, int] = dict(base)
    for k in base:
        if k in raw:
            try:
                out[k] = max(0, int(raw[k]))
            except (TypeError, ValueError):
                pass
    return out

def _save_usage_accumulator(data: Dict[str, Any]) -> None:
    clean = _default_usage_accum_dict()
    for k in clean:
        if k in data:
            try:
                clean[k] = max(0, int(data[k]))
            except (TypeError, ValueError):
                pass
    USAGE_ACCUM_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

