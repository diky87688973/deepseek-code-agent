# -*- coding: utf-8 -*-
"""review_conclusion：写盘后提交 diff review 结论。支持三种用途：① 提交 review 结论满足宿主门禁；② 解锁二阶提交的 confirm_id；③ 取消预览释放锁。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import agent_common as ac


def agent_main(
    *,
    conclusion: str,
    file_name: str = "",
    dry_run: bool = True,
    review_type: str = "file",
    confirm_id: str = "",
    cancel_preview: bool = False,
) -> dict:
    try:
        text = str(conclusion or "").strip()
        fn = str(file_name or "").strip()
        if not fn:
            return ac.err(ValueError("file_name 必填：请填写本次 review 涉及的一个或多个文件名，多个用逗号分隔。"))
        cancel = str(cancel_preview or "").strip().lower() in ("true", "1", "yes")
        if not cancel and len(text) < 30:
            return ac.err(
                ValueError(
                    "结论至少 30 字，必须给出真实验证结果与证据，不能只写「验证通过」。"
                    "示例：'review结论：user_service.py 修复了空指针异常，加 None 判断，用 py_compile 确认语法通过，跑 test_user_service.py 全部 PASS。'"
                    f"（当前 {len(text)} 字）"
                )
            )
        if dry_run:
            return ac.ok({"reviewed": False, "dry_run": True, "review_type": review_type, "file_name": fn, "message": "结论格式校验通过，可 dry_run=false 提交。"})

        # ── 处理 confirm_id ──
        cid = str(confirm_id or "").strip()
        if cid:
            if cancel_preview:
                from agent_v4.live_state import cancel_confirm_id
                cancelled = cancel_confirm_id(cid)
                if not cancelled:
                    return ac.err(ValueError(f"confirm_id 无效或已被消耗: {cid}"))
                return ac.ok({"reviewed": True, "review_type": review_type, "file_name": fn, "confirm_id": cid, "message": "预览已取消。"})
            from agent_v4.live_state import mark_diff_reviewed
            unlocked = mark_diff_reviewed(cid)
            if not unlocked:
                return ac.err(ValueError(f"confirm_id 无效或已被消耗: {cid}"))

        return ac.ok({"reviewed": True, "review_type": review_type, "file_name": fn, "confirm_id": cid, "message": "review 结论已记录，confirm_id 已解锁。" if cid else "review 结论已记录。"})
    except Exception as e:
        return ac.err(e)
