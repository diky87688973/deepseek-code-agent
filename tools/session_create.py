# -*- coding: utf-8 -*-
"""session_create 工具：创建自由 Agent 会话（Agent Session Mesh）。"""
from __future__ import annotations

import time
from typing import Any, Dict


def _normalize_worker_role(role: str) -> str:
    """模型常用别名 → team_roles.json 中的键名。"""
    r = str(role or "").strip()
    if not r:
        return r
    aliases = {
        "worker": "全栈开发",
        "dev": "全栈开发",
        "developer": "全栈开发",
        "全栈": "全栈开发",
        "全栈工程师": "全栈开发",
        "后端": "全栈开发",
        "前端": "全栈开发",
        "qa": "QA",
        "测试": "QA",
        "测试工程师": "QA",
        "quality": "QA",
    }
    low = r.lower()
    for k, v in aliases.items():
        if k.lower() == low:
            return v
    return r



def _pick_name(requested: str = "", scope_tags = None) -> str:
    """从配置代号池中取一个可用名称。池耗尽则用传入的名称。
    scope_tags 指定后，仅同标签的 agent 视为名称占用，不同标签可复用同名。"""
    requested = str(requested or "").strip()
    taken = set()
    scope = list(scope_tags or [])
    try:
        from agent_v3.live_state import AGENT_SESSIONS, _AGENT_SESSIONS_LOCK
        with _AGENT_SESSIONS_LOCK:
            for m in AGENT_SESSIONS.values():
                n = str(m.get("name") or "").strip()
                if not n:
                    continue
                if scope:
                    m_tags = m.get("tags") or []
                    if isinstance(m_tags, str):
                        m_tags = [m_tags]
                    if not any(t in m_tags for t in scope):
                        continue
                taken.add(n)
    except Exception:
        pass
    # 1. 尝试从配置读取代号池
    try:
        from util.config_loader import load_config
        cfg = load_config(verbose=False)
        pool_str = str(cfg.get("AGENT_TEAM_NAME_POOL") or "").strip()
        if pool_str:
            pool = [n.strip() for n in pool_str.replace("，", ",").split(",") if n.strip()]
            for name in pool:
                if name not in taken:
                    return name
    except Exception:
        pass
    base = requested if requested else "Agent"
    if base not in taken:
        return base
    i = 2
    while f"{base}{i}" in taken:
        i += 1
    return f"{base}{i}"

def agent_main(
    *,
    role: str = "",
    name: str = "",
    name_prefix: str = "",
    persona: str = "",
    count: int = 1,
    tags: Any = None,
    skill: str = "",
    **_kwargs: Any,
) -> Dict[str, Any]:
    """创建自由 Agent 会话（无 action 参数）。"""
    _kwargs.pop("action", None)

    try:
        n = max(1, min(50, int(count or 1)))
    except Exception:
        n = 1
    base_role = str(role or "Agent").strip() or "Agent"
    base_persona = str(persona or "").strip()
    tag_list = []
    if isinstance(tags, list):
        tag_list = [str(x).strip() for x in tags if str(x).strip()]
    elif isinstance(tags, str) and tags.strip():
        tag_list = [x.strip() for x in tags.replace("，", ",").split(",") if x.strip()]

    from util.session_store_v2 import new_conversation_id

    from agent_v3.live_state import CONVERSATIONS, upsert_agent_session
    from agent_v3.agent_core import _save_conversation as _svc, _save_title_file
    from util.agent_prompt_constants import TOOL_AGENT_SYSTEM_PROMPT as _sys_prompt
    agents = []
    now = int(time.time())
    for i in range(n):
        sid = new_conversation_id()
        if n == 1:
            agent_name = str(name or name_prefix or "").strip() or _pick_name("", scope_tags=tag_list)
        else:
            prefix = str(name_prefix or name or "").strip()
            if prefix:
                agent_name = f"{prefix}{i + 1}"
            else:
                agent_name = _pick_name("", scope_tags=tag_list) or f"Agent{i + 1}"
        persona_text = base_persona or f"你是多 Agent 协作网络中的 {agent_name}，角色是 {base_role}。收到其他 Agent 的消息时，应通过 session_send 回复对方；需要群体协作时使用 session_multisend 或 session_broadcast。"
        init_msgs = [
            {"role": "system", "content": _sys_prompt},
            {"role": "system", "content": f"【Agent 身份】\n名称：{agent_name}\n角色：{base_role}\n\n{persona_text}"},
        ]
        CONVERSATIONS[sid] = init_msgs
        _svc(sid, init_msgs)
        _save_title_file(sid, agent_name)
        _sk = str(skill or "").strip()
        if _sk:
            try:
                from util.skill_manager import get_skill_manager
                _mgr = get_skill_manager()
                if _mgr.get_skill(_sk):
                    from agent_v3.agent_core import _append_session_message_v2
                    _msg = {"role": "system", "content": f"【已加载技能：{_sk}】\n{_mgr.get_skill(_sk)}"}
                    _append_session_message_v2(sid, CONVERSATIONS[sid], _msg)
                    _svc(sid, CONVERSATIONS[sid])
            except Exception:
                pass
        meta = upsert_agent_session(
            sid,
            name=agent_name,
            role=base_role,
            persona=persona_text,
            status="idle",
            tags=tag_list,
            created_by=str(_kwargs.get("conversation_id") or ""),
            created_at=now,
        )
        row = {"cid": sid, "session_id": sid, "name": agent_name, "role": base_role, "tags": tag_list, "url": f"/?cid={sid}", "meta": meta}
        agents.append(row)

    data: Dict[str, Any] = {"agents": agents, "count": len(agents)}
    if agents:
        data.update(agents[0])
    return {"ok": True, "data": data}


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    p = argparse.ArgumentParser(description="session_create：人工调试 CLI → agent_main（无 --action）")
    p.add_argument("--conversation_id", default="", help="创建者会话 ID（可选）")
    p.add_argument("--role", default="Agent")
    p.add_argument("--name", default="")
    p.add_argument("--name_prefix", default="")
    p.add_argument("--persona", default="")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--tags", default="", help="逗号分隔标签")
    p.add_argument("--skill", default="")
    p.add_argument("--json_out", action="store_true")
    return p


def main() -> None:
    import json
    import sys

    args = build_parser().parse_args()
    tags_val: Any = None
    if str(args.tags or "").strip():
        tags_val = [x.strip() for x in str(args.tags).replace("，", ",").split(",") if x.strip()]
    r = agent_main(
        role=args.role,
        name=args.name,
        name_prefix=args.name_prefix,
        persona=args.persona,
        count=args.count,
        tags=tags_val,
        skill=args.skill,
        conversation_id=args.conversation_id or None,
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok"):
            print(json.dumps(r.get("data"), ensure_ascii=False, indent=2))
        else:
            err = r.get("error") or {}
            print(err.get("message", r), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
