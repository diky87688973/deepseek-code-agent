# -*- coding: utf-8 -*-
"""skill_manage 工具：LLM 按 name 读取 skill 全文（落 dialogue 区）。"""
from __future__ import annotations

from typing import Any, Dict, Optional


def agent_main(
    *,
    action: str = "",
    name: str = "",
    source: str = "",
    subdir: str = "",
    confirm_id: str = "",
    **_kwargs: Any,
) -> Dict[str, Any]:
    """skill_manage 入口。

    action:
      - read: 按 name 返回 skill 全文内容。
      - list: 返回所有可用 skill 的名称与描述（不含正文）。
      - copy: 将 source 路径下的 .md 文件复制到 skills 配置目录，subdir 为子目录名（可选）。

    source: action=copy 时必填，源目录路径。
    subdir: action=copy 时可选，源目录下的子目录名。
    """
    action = str(action or "").strip().lower()
    if not action:
        return {"ok": False, "error": {"type": "invalid_args", "message": "缺少 action 参数。可选: read, list"}}

    from util.skill_manager import get_skill_manager, init_skill_manager
    mgr = get_skill_manager()
    # 宿主未 bootstrap skills 时自动加载
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

    if action == "copy":
        from pathlib import Path
        _cid = str(confirm_id or "").strip()
        sub = str(subdir or "").strip()
        src = str(source or "").strip()

        # 生成源路径（按 name 或 source）
        def _resolve_src():
            nonlocal src
            name_key = str(name or "").strip()
            if name_key:
                meta = mgr.get_skill_meta(name_key)
                if not meta:
                    return None, f"未找到名为 '{name_key}' 的 skill"
                return Path(meta.file_path).expanduser().resolve(), None
            if src:
                return Path(src).expanduser().resolve(), None
            return None, "action=copy 需要指定 source（路径）或 name（skill 名称）"

        src_path, err = _resolve_src()
        if err:
            return {"ok": False, "error": {"type": "missing_source", "message": err}}

        result = mgr.copy_from(src_path, sub)

        # ── 无冲突：直接返回 ──
        if not result.get("conflict"):
            return _with_meta({"ok": True, "data": result})

        # ── 有冲突：confirm_id 流程 ──
        if _cid:
            from agent_v4.live_state import consume_confirm_id
            info = consume_confirm_id(_cid)
            if info and info.get("confirmed"):
                if info.get("action") != "copy":
                    return {"ok": False, "data": None, "error": {"type": "CrossToolConfirmId", "message": f"该 confirm_id 由 {info.get('action','?')} 创建，skill_manage 无法消费"}}
                import shutil
                conflict_path = Path(result["conflict"])
                if src_path.is_file():
                    conflict_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src_path), str(conflict_path))
                else:
                    sub = str(subdir or "").strip()
                    target_sub = mgr.skills_dir / sub if sub else mgr.skills_dir
                    target_sub.mkdir(parents=True, exist_ok=True)
                    for f in sorted(src_path.iterdir()):
                        if not f.is_file() or f.suffix != ".md":
                            continue
                        shutil.copy2(str(f), str(target_sub / f.name))
                mgr._scan(full=True)
                return _with_meta({"ok": True, "data": {"copied": 1, "overwritten": True}})
            return {"ok": False, "error": {"type": "confirm_failed",
                "message": "确认ID无效或尚未确认。请先调用 user_confirm 确认覆盖。"}}

        # ── 首次冲突：生成 confirm_id 让模型确认 ──
        from agent_v4.live_state import create_confirm_id
        new_id = create_confirm_id("copy", {"source": source, "subdir": subdir})
        return _with_meta({
            "ok": False,
            "error": {
                "code": "E_USER_CONFIRM_REQUIRED",
                "type": "UserConfirmRequired",
                "message": f"目标文件已存在: {result['conflict']}。确认覆盖后传入 confirm_id 重试。",
                "hint": "前端弹窗显示 data.title/data.confirms；用户确认后宿主会携带 confirm_id 重新调用 skill_manage",
                "retryable": False,
            },
            "data": {
                "title": "确认覆盖技能文件",
                "confirms": ["确认覆盖", "取消"],
                "confirm_id": new_id,
                "preview": {"action": "copy", "source": str(src_path), "target": result["conflict"]},
                "conflict": result["conflict"],
            },
        })

    return _with_meta({"ok": False, "error": {"type": "unknown_action", "message": f"未知 action: {action}。可选: read, list, copy"}})
