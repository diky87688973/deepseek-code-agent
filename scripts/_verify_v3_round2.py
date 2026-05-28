#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""catalog ↔ agent_main 对齐自检（与 check_agent_v3_health 同规则，可单独快速跑）。"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def main() -> int:
    # 复用 health 模块中的对齐检查（避免两套规则漂移）
    import scripts.check_agent_v3_health as health

    health.errors = []
    health.check_catalog_agent_main_alignment()
    health.check_search_recursive_defaults()

    from agent_v3 import agent_core as core  # noqa: F401

    import agent_v3.core.context_pipeline as cp
    import agent_v3.core.modes_kb as mk

    for mod, fn, needs in (
        (cp, "_build_context_segments", ["_build_kb_system_messages", "_get_catalog_hints_system_prompt"]),
        (mk, "_resolve_conversation_mode", ["_is_audit_only_intent"]),
    ):
        f = getattr(mod, fn)
        g = f.__globals__
        for n in needs:
            if n not in g:
                health.errors.append(f"{mod.__name__}.{fn} 缺少全局 {n}")

    try:
        core._compute_context_layout_payload(
            "v", [{"role": "user", "content": "x", "_agent_message_id": "1"}]
        )
    except NameError as exc:
        health.errors.append(f"facade context_layout NameError: {exc}")

    for rel in (
        "tools/session_send.py",
        "tools/session_create.py",
        "main_tray.py",
        "util/tts/manager.py",
        "util/agent_openai_compatible_client.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "agent_v2" in text:
            health.errors.append(f"{rel} 仍含 agent_v2")

    if health.errors:
        print("VERIFY FAILED", len(health.errors))
        for e in health.errors:
            print(" -", e)
        return 1
    print("VERIFY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
