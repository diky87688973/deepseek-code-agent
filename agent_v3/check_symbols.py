#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态/运行时符号检查：v3 门面 agent_core 与 routes 的引用是否齐全。"""
from __future__ import annotations

import dis
import re
import sys
from pathlib import Path
from typing import List, Set, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_global_names() -> Set[str]:
    import builtins

    from agent_v3 import agent_core as core
    from agent_v3.core import deps

    names = {n for n in dir(core) if not n.startswith("__")}
    names |= {n for n in dir(deps) if not n.startswith("__")}
    return names | set(dir(builtins))


def _insn_lineno(ins: dis.Instruction) -> int:
    pos = getattr(ins, "positions", None)
    if pos is not None and getattr(pos, "lineno", None):
        return int(pos.lineno)
    return int(getattr(ins, "lineno", 0) or 0)


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
            out.append((attr, name, _insn_lineno(ins)))
    return out


def _routes_core_refs() -> List[str]:
    from agent_v3 import agent_core as core

    src = (Path(__file__).parent / "routes.py").read_text(encoding="utf-8-sig")
    names = sorted(set(re.findall(r"core\.([a-zA-Z_][a-zA-Z0-9_]*)", src)))
    return [n for n in names if not hasattr(core, n)]


def _no_globals_injection() -> List[str]:
    core_dir = Path(__file__).parent / "core"
    for p in core_dir.glob("*.py"):
        src = p.read_text(encoding="utf-8-sig")
        if "globals()[_name]" in src or ("globals()[" in src and "getattr(_pkg" in src):
            return [f"{p.name} 仍使用 globals() 动态灌入"]
    return []


def main() -> int:
    errors: List[str] = []

    errors.extend(_no_globals_injection())
    missing_routes = _routes_core_refs()
    if missing_routes:
        errors.append(f"routes.py 引用 agent_core 缺失: {missing_routes}")

    for mod, fn in (
        ("agent_v3.core.agent_turn", "run_agent_turn"),
        ("agent_v3.core.context_pipeline", "_maybe_schedule_summarization"),
        ("agent_v3.core.tool_runtime", "execute_tool_script"),
    ):
        for fname, name, ln in _missing_globals_in_module(mod, fn):
            errors.append(f"{mod}.{fname} L{ln}: 未定义全局 {name}")

    try:
        import deepseek_code_agent3 as entry  # noqa: F401

        if not hasattr(entry, "app"):
            errors.append("deepseek_code_agent3 缺少 app")
    except Exception as exc:
        errors.append(f"无法 import deepseek_code_agent3: {exc}")

    if errors:
        print("check_symbols: FAILED")
        for e in errors:
            print(" -", e)
        return 1

    print("check_symbols: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
