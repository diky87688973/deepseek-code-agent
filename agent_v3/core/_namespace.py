# -*- coding: utf-8 -*-
"""合并 core 子模块符号并绑定到各模块 globals（修复拆分后 LOAD_GLOBAL 跨模块引用）。"""
from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Dict, Any


def absorb(target: Dict[str, Any], *modules: ModuleType) -> None:
    for mod in modules:
        if mod is None:
            continue
        for name, val in vars(mod).items():
            if name.startswith("__"):
                continue
            target[name] = val


def merged_core_namespace() -> Dict[str, Any]:
    from agent_v3.core import deps as _deps
    from agent_v3.core import shared_state as _shared_state
    from agent_v3.core import turn_control as _turn_control
    from agent_v3.core import conversation_store as _conversation_store
    from agent_v3.core import tool_runtime as _tool_runtime
    from agent_v3.core import llm_stream as _llm_stream
    from agent_v3.core import modes_kb as _modes_kb
    from agent_v3.core import context_pipeline as _context_pipeline
    from agent_v3.core import peer_mesh as _peer_mesh
    from agent_v3.core import usage_accum as _usage_accum
    from agent_v3.core import ui_bundle as _ui_bundle
    from agent_v3.core import agent_turn as _agent_turn
    from agent_v3.core import turn_runner as _turn_runner
    from agent_v3 import bootstrap as _bootstrap
    from agent_v3 import live_state as _live_state

    ns: Dict[str, Any] = {}
    absorb(
        ns,
        _deps,
        _shared_state,
        _turn_control,
        _conversation_store,
        _tool_runtime,
        _llm_stream,
        _modes_kb,
        _context_pipeline,
        _peer_mesh,
        _usage_accum,
        _ui_bundle,
        _agent_turn,
        _turn_runner,
        _bootstrap,
        _live_state,
    )
    return ns


def bind_core_modules() -> None:
    """在 agent_core 门面加载时调用一次，保证各 core 子模块函数可解析跨文件符号。"""
    ns = merged_core_namespace()
    import agent_v3.core as core_pkg

    for modinfo in pkgutil.iter_modules(core_pkg.__path__):
        if modinfo.name.startswith("_"):
            continue
        mod = importlib.import_module(f"agent_v3.core.{modinfo.name}")
        mod.__dict__.update(ns)
