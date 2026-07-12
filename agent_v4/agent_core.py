#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_v4 门面：聚合 core 子模块，保持 routes 层 `import agent_core as core` 兼容。"""
from __future__ import annotations

from typing import Any


def _export_module(mod: Any) -> None:
    g = globals()
    for name, val in vars(mod).items():
        if name.startswith("__"):
            continue
        g[name] = val


from agent_v4.core import base as _core_base
from agent_v4.core import agent_turn as _core_agent_turn
from agent_v4.core import turn_runner as _core_turn_runner
_export_module(_core_base)
_export_module(_core_agent_turn)
_export_module(_core_turn_runner)

from agent_v4 import bootstrap as _bootstrap_mod
from agent_v4 import live_state as _live_state_mod

_export_module(_bootstrap_mod)
_export_module(_live_state_mod)
from util.agent_model_dispatch import (  # noqa: F401
    ALLOWED_MODELS,
    default_model_from_env,
    effective_model,
    set_conversation_model,
)
from util.agent_deepseek_pricing import get_model_pricing_snapshot  # noqa: F401
from util.session_store_v2 import new_conversation_id as _new_conversation_id  # noqa: F401
