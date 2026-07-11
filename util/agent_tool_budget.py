# -*- coding: utf-8 -*-
"""单轮用户消息内的工具调用预算（通过 tool 消息信封告知模型）。"""
from __future__ import annotations

from typing import Any, Dict, Tuple


def tool_call_budget_fields(*, used: int, limit: int) -> Dict[str, int]:
    """used：本回合已成功计数的工具调用次数（含本次）。"""
    lim = max(1, int(limit))
    u = max(0, min(int(used), lim))
    return {
        "tool_calls_limit": lim,
        "tool_calls_used": u,
        "tool_calls_remaining": max(0, lim - u),
    }


def tool_call_limit_reached_result(*, used: int, limit: int) -> Dict[str, Any]:
    lim = max(1, int(limit))
    fields = tool_call_budget_fields(used=lim, limit=lim)
    return {
        "ok": False,
        "data": fields,
        "error": {
            "type": "ToolCallLimitReached",
            "message": (
                f"本回合工具调用已达上限（{lim} 次）。"
                "请根据已有工具返回结果向用户总结；如需继续请用户发送「继续」开启新回合。"
            ),
        },
    }


def attach_tool_call_budget(result: Any, *, used: int, limit: int) -> Dict[str, Any]:
    """在 tool 返回 data 中附加 tool_calls_limit / used / remaining。"""
    if not isinstance(result, dict):
        return {
            "ok": False,
            "data": tool_call_budget_fields(used=used, limit=limit),
            "error": {"type": "InvalidResult", "message": repr(result)},
        }
    out = dict(result)
    raw_data = out.get("data")
    if raw_data is None:
        data: Dict[str, Any] = {}
    elif isinstance(raw_data, dict):
        data = dict(raw_data)
    else:
        data = {"value": raw_data}
    data.update(tool_call_budget_fields(used=used, limit=limit))
    out["data"] = data
    return out


def turn_tool_budget_exhausted(used: int, limit: int) -> bool:
    return int(used) >= max(1, int(limit))


def apply_turn_tool_budget_to_result(
    result: Any,
    *,
    turn_tool_invocations_used: int,
    limit: int,
    limit_blocked: bool,
) -> Tuple[Any, int]:
    if limit_blocked:
        used = max(0, min(int(turn_tool_invocations_used), int(limit)))
        return attach_tool_call_budget(result, used=used, limit=limit), turn_tool_invocations_used
    new_used = int(turn_tool_invocations_used) + 1
    return attach_tool_call_budget(result, used=new_used, limit=limit), new_used
