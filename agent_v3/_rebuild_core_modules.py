#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史脚本：曾从 agent_core_monolith.py.bak 重建 core 子模块；monolith 已删除，请勿再运行。"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MONO = ROOT / "agent_core_monolith.py.bak"
CORE = ROOT / "core"

# 与 _split_agent_core_once.FN_MODULE 相同
from _split_agent_core_once import FN_MODULE  # type: ignore

DEPS_END_MARKER = "USER_STOPPED_TOOL_MESSAGE"


def _fix_names(text: str) -> str:
    return text.replace("TOOL_agent_v3_EXECUTE_MODE_PROMPT", "TOOL_AGENT_EXECUTE_MODE_PROMPT").replace(
        "TOOL_agent_v3_SYSTEM_PROMPT", "TOOL_AGENT_SYSTEM_PROMPT"
    )


def main() -> None:
    if not MONO.is_file():
        raise SystemExit("agent_core_monolith.py.bak 已移除；core 已稳定拆分，本脚本仅作历史记录。")

    source = _fix_names(MONO.read_text(encoding="utf-8-sig"))
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)

    # deps: from __future__ through sse_events import block
    deps_lines: list[str] = []
    for i, line in enumerate(lines):
        if line.startswith("def ") or line.strip().startswith("USER_STOPPED_TOOL_MESSAGE"):
            break
        deps_lines.append(line)
    deps_body = "".join(deps_lines).rstrip() + "\n"
    (CORE / "deps.py").write_text(
        '# -*- coding: utf-8\n"""共享依赖导入（bootstrap、live_state、util）。"""\n' + deps_body,
        encoding="utf-8",
    )

    func_src: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            seg = ast.get_source_segment(source, node) or ""
            func_src[node.name] = seg

    mod_funcs: dict[str, list[str]] = {}
    for name in func_src:
        mod = FN_MODULE.get(name, "misc")
        mod_funcs.setdefault(mod, []).append(name)

    assigns_mod: dict[str, list[str]] = {"shared_state": [], "ui_bundle": []}
    ui_keys = {
        "UI_HTML_FILE", "RESET_CSS_FILE", "UI_CSS_FILE", "UI_JS_FILE", "THEME_UI_JS_FILE",
        "HLJS_JS_FILE", "CODE_HIGHLIGHT_JS_FILE", "HLJS_CSS_DARK_FILE", "HLJS_CSS_LIGHT_FILE",
        "_INLINE_CSS", "_INLINE_JS", "TTS_JS_FILE", "_INLINE_JS2", "_INLINE_HTML_TMPL", "INLINE_UI_HTML",
    }
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    k = t.id
                    seg = ast.get_source_segment(source, node) or ""
                    if k in ui_keys:
                        assigns_mod["ui_bundle"].append(seg)
                    elif k.startswith("_") or k in {"USER_STOPPED_TOOL_MESSAGE", "WRITE_TOOL_SCRIPTS", "MAX_TOOL_RESULT_CHARS"}:
                        if k not in ui_keys and not k.startswith("from"):
                            assigns_mod["shared_state"].append(seg)

    header = (
        '# -*- coding: utf-8\n"""agent_v3.core.{mod}"""\nfrom __future__ import annotations\n\n'
        "from agent_v3.core.deps import *  # noqa: F403\n"
        "from agent_v3.core.shared_state import *  # noqa: F403\n\n"
    )
    for mod, fnames in sorted(mod_funcs.items()):
        if mod in ("shared_state",):
            continue
        parts = [header.format(mod=mod)]
        for seg in assigns_mod.get(mod, []):
            parts.append(seg + "\n\n")
        for fn in sorted(fnames):
            parts.append(func_src[fn] + "\n\n")
        (CORE / f"{mod}.py").write_text("".join(parts), encoding="utf-8")

    # shared_state
    ss = [
        '# -*- coding: utf-8\n"""模块级可变状态与常量。"""\nfrom __future__ import annotations\n\n',
        "from agent_v3.core.deps import *  # noqa: F403\n\n",
    ]
    for seg in assigns_mod["shared_state"]:
        ss.append(seg + "\n\n")
    (CORE / "shared_state.py").write_text("".join(ss), encoding="utf-8")

    # ui_bundle
    ui = [
        '# -*- coding: utf-8\n"""内联经典 UI 资源打包。"""\nfrom __future__ import annotations\n\n',
        "from agent_v3.core.deps import *  # noqa: F403\n\n",
    ]
    for seg in assigns_mod["ui_bundle"]:
        ui.append(seg + "\n\n")
    for fn in sorted(mod_funcs.get("ui_bundle", [])):
        ui.append(func_src[fn] + "\n\n")
    (CORE / "ui_bundle.py").write_text("".join(ui), encoding="utf-8")

    print("rebuilt", len(mod_funcs), "modules")


if __name__ == "__main__":
    main()
