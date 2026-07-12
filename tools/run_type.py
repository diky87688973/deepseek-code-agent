# -*- coding: utf-8 -*-
"""会话运行模式：查询/切换 Auto、Plan、Execute。

真实落盘由宿主 `_execute_run_type`（经 `execute_tool_script` host hook）完成。
本模块 `agent_main` 在传入 conversation_id（宿主注入）时转调同一实现；无会话 id 时拒绝假成功。
"""

from __future__ import annotations

from typing import Optional

import agent_common as ac


def agent_main(
    *,
    run_type: Optional[str] = None,
    json_out: bool = False,
    **_kwargs: object,
) -> dict:
    """不传 `run_type` 表示查询；传 `auto|plan|execute` 表示切换。"""
    _ = json_out
    cid = str(_kwargs.get("conversation_id") or "").strip()
    if not cid:
        return ac.err(
            ValueError("run_type 须由宿主注入 conversation_id 后执行；禁止无会话上下文直调")
        )
    from agent_v4.core.conversation_store import _execute_run_type

    return _execute_run_type(cid, {"run_type": run_type})
