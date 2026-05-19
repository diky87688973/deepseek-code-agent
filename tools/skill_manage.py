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

    from util.skill_manager import get_skill_manager, init_skill_manager
    mgr = get_skill_manager()
    # CLI 路径下未初始化，自动加载
    if mgr.registry_count == 0:
        from pathlib import Path
        from util.config_loader import load_config
        _cfg = load_config(verbose=False)
        _dir = str(_cfg.get("AGENT_SKILLS_DIR") or "").strip()
        _size = int(_cfg.get("AGENT_SKILLS_MAX_FILE_SIZE", "200000"))
        if _dir:
            mgr = init_skill_manager(Path(_dir), _size)

    def _with_meta(result: Dict[str, Any]) -> Dict[str, Any]:
        """在返回结果中附加旁支模型使用量和通知。"""
        extra = {}
        if mgr.pending_notifications:
            extra["notifications"] = list(mgr.pending_notifications)
            mgr.pending_notifications.clear()
        if any(mgr._side_usage.values()):
            extra["side_usage"] = dict(mgr._side_usage)
        if extra:
            if "data" not in result or not isinstance(result["data"], dict):
                result["data"] = result.get("data") or {}
            if isinstance(result["data"], dict):
                result["data"].update(extra)
        return result

    if action == "list":
        skills = mgr.list_skills()
        return _with_meta({"ok": True, "data": {"skills": skills}})

    if action == "read":
        key = str(name or "").strip()
        if not key:
            available = [s["name"] for s in mgr.list_skills()]
            return _with_meta({
                "ok": False,
                "error": {
                    "type": "missing_name",
                    "message": "请指定 name 参数。当前可用: " + ", ".join(available),
                },
                "data": {"available": available},
            })
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

    return _with_meta({"ok": False, "error": {"type": "unknown_action", "message": f"未知 action: {action}。可选: read, list"}})


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
