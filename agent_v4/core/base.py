# -*- coding: utf-8 -*-
"""聚合无环依赖的核心子模块（含下划线前缀符号）。"""
from __future__ import annotations

from typing import Any


def _pull(mod: Any) -> None:
    g = globals()
    for name, val in vars(mod).items():
        if name.startswith("__"):
            continue
        g[name] = val


from agent_v4.core import shared_state as _m_shared_state
from agent_v4.core import turn_control as _m_turn_control
from agent_v4.core import conversation_store as _m_conversation_store
from agent_v4.core import tool_runtime as _m_tool_runtime
from agent_v4.core import llm_stream as _m_llm_stream
from agent_v4.core import modes_kb as _m_modes_kb
from agent_v4.core import context_pipeline as _m_context_pipeline
from agent_v4.core import peer_mesh as _m_peer_mesh
from agent_v4.core import usage_accum as _m_usage_accum
from agent_v4.core import ui_bundle as _m_ui_bundle

for _mod in (
    _m_shared_state,
    _m_turn_control,
    _m_conversation_store,
    _m_tool_runtime,
    _m_llm_stream,
    _m_modes_kb,
    _m_context_pipeline,
    _m_peer_mesh,
    _m_usage_accum,
    _m_ui_bundle,
):
    _pull(_mod)

from agent_v4.core.namespace import bind_core_modules

bind_core_modules()
