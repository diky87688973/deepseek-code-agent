#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态/运行时符号检查：拆包后 agent_core 与 routes 的引用是否齐全。"""
from __future__ import annotations
from typing import List, Set, Tuple

import dis
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_global_names() -> Set[str]:
    import builtins
    from agent_v2 import agent_core as core

    return {n for n in dir(core) if not n.startswith("__")} | set(dir(builtins))


def _missing_globals_in_module(module_name: str, attr: str) -> List[Tuple[str, str, int]]:
    import importlib

    mod = importlib.import_module(module_name)
    fn = getattr(mod, attr)
    known = _load_global_names()
    out: List[Tuple[str, str, int]] = []
    for ins in dis.get_instructions(fn):
        if ins.opname != "LOAD_GLOBAL":
            continue
        name = ins.argval
        if name not in known:
            ln = ins.positions.lineno if ins.positions else 0
            out.append((attr, name, ln))
    return out


def _routes_core_refs() -> List[str]:
    from agent_v2 import agent_core as core

    src = (Path(__file__).parent / "routes.py").read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"core\.([a-zA-Z_][a-zA-Z0-9_]*)", src)))
    return [n for n in names if not hasattr(core, n)]


def _no_globals_injection() -> List[str]:
    src = (Path(__file__).parent / "agent_core.py").read_text(encoding="utf-8")
    if "globals()[_name]" in src or "globals()[" in src and "getattr(_pkg" in src:
        return ["agent_core.py 仍使用 globals() 动态灌入 bootstrap/live_state"]
    return []


def _bootstrap_live_import_coverage() -> List[str]:
    """agent_core / routes 用到的 bootstrap·live_state 符号须在 agent_core 顶部显式 import。"""
    import ast

    from agent_v2 import agent_core as core
    import agent_v2.bootstrap as boot
    import agent_v2.live_state as live

    core_src = (Path(__file__).parent / "agent_core.py").read_text(encoding="utf-8")
    routes_src = (Path(__file__).parent / "routes.py").read_text(encoding="utf-8")
    used = set(re.findall(r"\b([_a-zA-Z][_a-zA-Z0-9]*)\b", core_src))
    used |= set(re.findall(r"core\.([a-zA-Z_][a-zA-Z0-9_]*)", routes_src))

    boot_live = set(getattr(boot, "__all__", [])) | set(getattr(live, "__all__", []))
    need = sorted(used & boot_live)

    tree = ast.parse(core_src)
    imported: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in ("agent_v2.bootstrap", "agent_v2.live_state"):
            continue
        for alias in node.names:
            imported.add(alias.asname or alias.name)

    missing = [n for n in need if n not in imported and not hasattr(core, n)]
    return missing


def main() -> int:
    errors: List[str] = []

    errors.extend(_no_globals_injection())
    missing_explicit = _bootstrap_live_import_coverage()
    if missing_explicit:
        errors.append(f"agent_core 未显式 import（bootstrap/live_state）: {missing_explicit}")

    missing_routes = _routes_core_refs()
    if missing_routes:
        errors.append(f"routes.py 引用 agent_core 缺失: {missing_routes}")

    for fn in ("run_agent_turn", "_maybe_schedule_summarization", "execute_tool_script"):
        for fname, name, ln in _missing_globals_in_module("agent_v2.agent_core", fn):
            errors.append(f"agent_core.{fname} L{ln}: 未定义全局 {name}")

    try:
        import deepseek_code_agent2 as entry  # noqa: F401

        if not hasattr(entry, "app"):
            errors.append("deepseek_code_agent2 缺少 app")
    except Exception as exc:
        errors.append(f"无法 import deepseek_code_agent2: {exc}")

    if errors:
        print("check_symbols: FAILED")
        for e in errors:
            print(" -", e)
        return 1

    print("check_symbols: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
