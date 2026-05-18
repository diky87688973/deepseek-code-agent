# -*- coding: utf-8 -*-
"""skill_manage 工具：LLM 按 name 读取 skill 全文（落 dialogue 区）。"""
from __future__ import annotations

from typing import Any, Dict, Optional


def agent_main(
    *,
    action: str = "",
    name: str = "",
    **_kwargs: Any,
) -> Dict[str, Any]:
    """skill_manage 入口。

    action:
      - read: 按 name 返回 skill 全文内容。
      - list: 返回所有可用 skill 的名称与描述（不含正文）。
    """
    action = str(action or "").strip().lower()
    if not action:
        return {"ok": False, "error": {"type": "invalid_args", "message": "缺少 action 参数。可选: read, list"}}

    from util.skill_manager import get_skill_manager
    mgr = get_skill_manager()

    if action == "list":
        skills = mgr.list_skills()
        return {"ok": True, "data": {"skills": skills}}

    if action == "read":
        key = str(name or "").strip()
        if not key:
            # 若未指定 name，列出所有可用 skill 名供选择
            available = [s["name"] for s in mgr.list_skills()]
            return {
                "ok": False,
                "error": {
                    "type": "missing_name",
                    "message": "请指定 name 参数。当前可用: " + ", ".join(available),
                },
                "data": {"available": available},
            }
        content = mgr.get_skill(key)
        if content is None:
            available = [s["name"] for s in mgr.list_skills()]
            return {
                "ok": False,
                "error": {
                    "type": "not_found",
                    "message": f"未找到名为 '{key}' 的 skill。当前可用: " + ", ".join(available),
                },
                "data": {"available": available},
            }
        return {"ok": True, "data": {"name": key, "content": content}}

    return {"ok": False, "error": {"type": "unknown_action", "message": f"未知 action: {action}。可选: read, list"}}


# ── CLI 入口（供手动调试）──
if __name__ == "__main__":
    import json
    import sys

    # 用 argv 模拟参数：python skill_manage.py --action read --name "xxx"
    args: Dict[str, Any] = {}
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a.startswith("--") and i + 1 < len(sys.argv):
            key = a[2:]
            val = sys.argv[i + 1]
            # 布尔/数字尝试转换
            if val.lower() in ("true", "false"):
                val = val.lower() == "true"
            elif val.isdigit():
                val = int(val)
            args[key] = val
            i += 2
        else:
            i += 1

    result = agent_main(**args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
