# -*- coding: utf-8 -*-
"""agent_v4.runtime — Turn 编排层（显式 import，不参与 bind_core_modules）。"""
from __future__ import annotations

# 避免在 bind_core_modules 期间急切导入 AgentRuntime（会拉 base 形成环）
from agent_v4.runtime.event_sink import EventSink, NullEventSink, YieldEventSink
from agent_v4.runtime.host_policy import HostPolicy
from agent_v4.runtime.scenario_injection import ScenarioInjection
from agent_v4.runtime.turn_context import TurnContext

__all__ = [
    "AgentRuntime",
    "EventSink",
    "NullEventSink",
    "YieldEventSink",
    "HostPolicy",
    "ScenarioInjection",
    "TurnContext",
]


def __getattr__(name):
    if name == "AgentRuntime":
        from agent_v4.runtime.agent_runtime import AgentRuntime

        return AgentRuntime
    raise AttributeError(name)
