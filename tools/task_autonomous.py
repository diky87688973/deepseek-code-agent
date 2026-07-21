# -*- coding: utf-8 -*-
"""切换自主模式。enabled=true 开启，模型自主执行多步；enabled=false 关闭并结束本轮。"""

from __future__ import annotations


def agent_main(*, enabled: bool = False, summary: str = "", **kwargs) -> dict:
    if not isinstance(summary, str):
        summary = str(summary)
    if len(summary) > 200:
        summary = summary[:200]
    return {
        "ok": True,
        "data": {
            "action": "autonomous",
            "enabled": bool(enabled),
            "summary": summary,
        },
    }
