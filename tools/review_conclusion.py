# -*- coding: utf-8 -*-
"""review_conclusion：写盘后提交 diff review 结论，解锁跨文件编辑。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import agent_common as ac


def agent_main(
    *,
    conclusion: str,
    dry_run: bool = True,
    review_type: str = "file",
) -> dict:
    try:
        text = str(conclusion or "").strip()
        if len(text) < 30:
            return ac.err(
                ValueError(
                    "结论至少 30 字。需包含：改了什么文件、改了什么内容、验证结果。"
                    "示例：'review结论：host_quality.py 修复了 HostQualityStackFirst 误判，正则加 (?![ntr]) + detect_and_update_intent 加 else 清空，回归 ALL GREEN。'"
                    f"（当前 {len(text)} 字）"
                )
            )
        if dry_run:
            return ac.ok({"reviewed": False, "dry_run": True, "review_type": review_type, "message": "结论格式校验通过，可 dry_run=false 提交。"})
        return ac.ok({"reviewed": True, "review_type": review_type, "message": "review 结论已记录。"})
    except Exception as e:
        return ac.err(e)
