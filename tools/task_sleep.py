# -*- coding: utf-8 -*-
"""暂停自主执行：宿主停止自动激活，N 秒后重新激活。"""

from __future__ import annotations


def agent_main(*, seconds: int = 0, reminder: str = "", **kwargs) -> dict:
    seconds = int(seconds) if isinstance(seconds, (int, float, str)) and int(seconds) > 0 else 0
    if seconds < 1 or seconds > 86400:
        return {
            "ok": False,
            "error": {
                "type": "ValueError",
                "message": f"seconds 必须在 1~86400 之间，收到 {seconds}",
            },
        }
    if not isinstance(reminder, str):
        reminder = str(reminder)
    if len(reminder) > 500:
        reminder = reminder[:500]
    return {
        "ok": True,
        "data": {
            "action": "sleep",
            "seconds": seconds,
            "reminder": reminder,
        },
    }
