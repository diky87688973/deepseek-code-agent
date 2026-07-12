# -*- coding: utf-8 -*-
"""HTTP / 托盘层窄接口：优先从此处导入，避免 routes 直接依赖 core._* 私有符号扩散。"""
from __future__ import annotations

# 会话与 turn
from agent_v4.core.agent_turn import run_agent_turn
from agent_v4.core.turn_runner import (
    publish_conversation_event,
    start_background_agent_turn,
)

# 工具执行（含完整写门控）
from agent_v4.core.tool_runtime import execute_tool_script, preflight_write_tool, load_catalog

# 门控与预览状态
from agent_v4.runtime.host_policy import (
    HostPolicy,
    _CONVERSATION_PREVIEWED,
    _PREVIEW_PATH_SCRIPTS,
    _check_write_preview,
    _apply_host_quality_write_gate,
)

# 工具集合单源
from agent_v4.core.tool_sets import (
    WRITE_TOOL_SCRIPTS,
    WRITE_GATED_SCRIPTS,
    PREVIEW_REQUIRED_SCRIPTS,
    QUALITY_WRITE_PATH_SCRIPTS,
    assert_tool_sets_consistent,
)

__all__ = [
    "run_agent_turn",
    "publish_conversation_event",
    "start_background_agent_turn",
    "execute_tool_script",
    "preflight_write_tool",
    "load_catalog",
    "HostPolicy",
    "_CONVERSATION_PREVIEWED",
    "_PREVIEW_PATH_SCRIPTS",
    "_check_write_preview",
    "_apply_host_quality_write_gate",
    "WRITE_TOOL_SCRIPTS",
    "WRITE_GATED_SCRIPTS",
    "PREVIEW_REQUIRED_SCRIPTS",
    "QUALITY_WRITE_PATH_SCRIPTS",
    "assert_tool_sets_consistent",
]
