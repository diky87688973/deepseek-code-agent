#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_v3 运行栈健康检查（提示词、import、符号绑定、session_* 契约）。发版前建议执行。

Layer 0 通过 `scripts/run_layer0.py` 自动调用本脚本。
"""
from __future__ import annotations

import dis
import importlib
import inspect
import pkgutil
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

SKIP_DIRS = {
    "agent_v2",
    "_backups_before_tools_align_20260527",
    "_backups_before_v3_20260527_174419",
    "__pycache__",
    ".git",
}

RUNTIME_PY_RELS = (
    "main_tray.py",
    "deepseek_code_agent3.py",
    "tools/session_send.py",
    "tools/session_create.py",
    "tools/session_broadcast.py",
    "tools/session_list.py",
    "tools/session_wait.py",
    "tools/user_confirm.py",
    "util/agent_openai_compatible_client.py",
    "util/tts/manager.py",
)

errors: list = []


def _should_scan(path: Path) -> bool:
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        return False
    if "scripts" in parts:
        return False
    if path.suffix != ".py":
        return False
    if path.name.endswith(".bak") or path.name.startswith("_rebuild"):
        return False
    rel = path.relative_to(ROOT)
    if rel.parts[0] == "tests":
        return False
    if rel.parts[0] not in ("agent_v3", "tools", "util"):
        if rel.name not in ("main_tray.py", "deepseek_code_agent3.py"):
            return False
    return True


def check_no_broken_prompt_names() -> None:
    bad = re.compile(r"TOOL_agent_v3|TOOL_AGENT_V1|TOOL_AGENT_V2_")
    for path in ROOT.rglob("*.py"):
        if not _should_scan(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if bad.search(text):
            errors.append(f"{path.relative_to(ROOT)}: 含废弃提示词常量名（TOOL_agent_v3 / TOOL_AGENT_V2_* 等）")


def check_no_v2_legacy_aliases() -> None:
    legacy = re.compile(r"_v2_new_conversation_id|TOOL_AGENT_V2_")
    for path in ROOT.rglob("*.py"):
        if not _should_scan(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if legacy.search(text):
            errors.append(f"{path.relative_to(ROOT)}: 仍含 _v2_new_conversation_id 或 TOOL_AGENT_V2_*")


def check_runtime_no_agent_v2() -> None:
    for rel in RUNTIME_PY_RELS:
        p = ROOT / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if "agent_v2" in text:
            errors.append(f"{rel}: 运行栈仍 import agent_v2")


def check_main_system_prompt() -> None:
    from util import agent_prompt_constants as apc

    body = apc.TOOL_AGENT_SYSTEM_PROMPT
    if len(body) < 400:
        errors.append("TOOL_AGENT_SYSTEM_PROMPT 过短（可能误留 v1 精简版）")
    if "身份与边界" not in body:
        errors.append("TOOL_AGENT_SYSTEM_PROMPT 缺少「身份与边界」段")
    if hasattr(apc, "TOOL_AGENT_V2_SYSTEM_PROMPT"):
        errors.append("util.agent_prompt_constants 仍导出 TOOL_AGENT_V2_* 别名")


def check_session_create_prompt_import() -> None:
    import session_create

    src = inspect.getsource(session_create.agent_main)
    if "TOOL_agent_v3" in src:
        errors.append("session_create: 含错误常量 TOOL_agent_v3")
    if "TOOL_AGENT_V2" in src:
        errors.append("session_create: 仍引用 TOOL_AGENT_V2_*")
    if "_v2_new_conversation_id" in src:
        errors.append("session_create: 仍使用 _v2_new_conversation_id")
    if "_sys_prompt" not in src:
        errors.append("session_create: 未绑定 _sys_prompt")


def check_core_symbols() -> None:
    from agent_v3 import agent_core as core

    core._compute_context_layout_payload(
        "health-check",
        [{"role": "user", "content": "ping", "_agent_message_id": "1"}],
    )
    import agent_v3.core.context_pipeline as cp

    for name in ("_build_kb_system_messages", "_get_catalog_hints_system_prompt"):
        if name not in cp._build_context_segments.__globals__:
            errors.append(f"context_pipeline 缺少全局 {name}")


def check_search_recursive_defaults() -> None:
    for mod_name in ("grep_files", "file_search", "glob_files", "regex_locate"):
        mod = importlib.import_module(mod_name)
        default = inspect.signature(mod.agent_main).parameters["recursive"].default
        if default is not True:
            errors.append(f"{mod_name}.recursive default={default!r}，应为 True")


def check_core_load_global_all() -> None:
    from agent_v3 import agent_core as core
    from agent_v3.core import deps

    known = {n for n in dir(core) if not n.startswith("__")}
    known |= {n for n in dir(deps) if not n.startswith("__")}
    known |= set(dir(__import__("builtins")))

    import agent_v3.core as core_pkg

    for mi in pkgutil.iter_modules(core_pkg.__path__):
        if mi.name.startswith("_"):
            continue
        mod = importlib.import_module(f"agent_v3.core.{mi.name}")
        for name, obj in vars(mod).items():
            if not callable(obj) or not hasattr(obj, "__code__"):
                continue
            if getattr(obj, "__module__", "") != mod.__name__:
                continue
            for ins in dis.get_instructions(obj):
                if ins.opname == "LOAD_GLOBAL" and ins.argval not in known:
                    errors.append(f"{mod.__name__}.{name}: 未定义全局 {ins.argval!r}")


def check_core_import_order() -> None:
    """子模块应能通过 _base 单独初始化（勿在 turn_runner 顶层 import agent_turn）。"""
    tr = (ROOT / "agent_v3" / "core" / "turn_runner.py").read_text(encoding="utf-8")
    if re.search(r"^from agent_v3\.core\.agent_turn import", tr, re.M):
        errors.append("turn_runner.py 顶层仍 from agent_v3.core.agent_turn import（易循环导入）")
    to_clear = [k for k in list(sys.modules) if k.startswith("agent_v3")]
    for k in to_clear:
        del sys.modules[k]
    try:
        importlib.import_module("agent_v3.core.agent_turn")
    except Exception as exc:
        errors.append(f"直接 import agent_v3.core.agent_turn 失败: {exc}")


def check_catalog_param_policy() -> None:
    """P2/P3：只读工具无 restrict/run_type；path 唯一命名；glob 无 pattern 别名。"""
    import json

    cat = json.loads((ROOT / "tools" / "tool_list_agent.json").read_text(encoding="utf-8"))
    keep_restrict = frozenset(
        {
            "write_file.py",
            "read_write.py",
            "delete_file.py",
            "file_ops.py",
            "replace_in_file.py",
            "apply_patch.py",
            "archive.py",
            "run_command.py",
            "python_inline.py",
        }
    )
    no_run_type = frozenset(
        {
            "read_file.py",
            "glob_files.py",
            "grep_files.py",
            "find_in_file.py",
            "regex_locate.py",
            "file_search.py",
            "git_workspace.py",
            "web_fetch.py",
            "web_fetch_render.py",
            "unified_diagnose.py",
            "env_probe.py",
            "ip_geolocate.py",
            "open_meteo_weather.py",
            "data_table.py",
            "text_diff.py",
            "image_ocr.py",
            "session_send.py",
            "session_multisend.py",
            "session_broadcast.py",
            "session_wait.py",
            "session_list.py",
            "session_create.py",
        }
    )
    for t in cat.get("tools") or []:
        name = str(t.get("name") or "")
        flags = {str(a.get("flag") or "") for a in t.get("args") or [] if isinstance(a, dict)}
        if "--restrict_to_workspace" in flags and name not in keep_restrict:
            errors.append(f"{name}: 只读/检索类不应含 --restrict_to_workspace（见 workspace_safety）")
        if "--run_type" in flags and name in no_run_type:
            errors.append(f"{name}: 只读类 catalog 不应含 --run_type")
        if name == "glob_files.py" and "--pattern" in flags:
            errors.append("glob_files.py: 禁止 --pattern 别名，仅用 --glob_pattern")
        if name in ("data_table.py", "image_ocr.py") and "--source" in flags:
            errors.append(f"{name}: 表格/图片路径须用 --path，勿用 --source")


def check_catalog_agent_main_alignment() -> None:
    """tool_list_agent.json 的 --* 参数须与 agent_main 签名一致（与 _verify_v3_round2 同规则）。"""
    import json

    cat = json.loads((ROOT / "tools" / "tool_list_agent.json").read_text(encoding="utf-8"))
    extra_ok = frozenset({"_progress_dict", "step_title"})

    for t in cat.get("tools", []):
        fn = str(t.get("name", ""))
        if not fn.endswith(".py"):
            continue
        mod_name = fn[:-3]
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:
            errors.append(f"{fn}: import {exc}")
            continue
        if not hasattr(mod, "agent_main"):
            continue
        sig = inspect.signature(mod.agent_main)
        params = set(sig.parameters.keys())
        has_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        for a in t.get("args") or []:
            flag = str(a.get("flag", ""))
            if not flag.startswith("--"):
                continue
            pname = flag[2:]
            if pname in extra_ok:
                continue
            if "-" in pname:
                errors.append(f"{fn}: catalog 含连字符参数 {flag!r}（应 snake_case）")
            if pname not in params and not has_var_kw:
                errors.append(f"{fn}: catalog 参数 {pname!r} 不在 agent_main")


def check_session_tools_contract() -> None:
    """协作工具：宿主式 import（tools/ 在 path）、无 from tools.*、catalog 无 --action。"""
    import json

    tools_dir = ROOT / "tools"
    session_names = tuple(
        p.name for p in sorted(tools_dir.glob("session_*.py")) if p.is_file()
    )
    cat = json.loads((ROOT / "tools" / "tool_list_agent.json").read_text(encoding="utf-8"))
    by_name = {str(t.get("name") or ""): t for t in cat.get("tools") or []}

    for fname in session_names:
        text = (tools_dir / fname).read_text(encoding="utf-8", errors="replace")
        if "from tools." in text:
            errors.append(f"tools/{fname}: 含 from tools.*（应顶层 import，与 bootstrap 一致）")
        if re.search(
            r"try:\s*\n\s*import agent_common",
            text,
        ):
            errors.append(f"tools/{fname}: 含 try/import agent_common 双轨（应单行 import agent_common as ac）")
        entry = by_name.get(fname)
        if not entry:
            errors.append(f"tools/{fname}: 未在 tool_list_agent.json 注册")
            continue
        for arg in entry.get("args") or []:
            if str(arg.get("flag") or "") == "--action":
                errors.append(f"tools/{fname}: catalog 仍含 --action")
        for ex in entry.get("examples") or []:
            if isinstance(ex, dict) and "action" in (ex.get("args") or {}):
                errors.append(f"tools/{fname}: catalog 示例仍含 action 字段")

    saved_path = list(sys.path)
    try:
        sys.path.insert(0, str(tools_dir))
        for fname in session_names:
            mod_name = fname[:-3]
            try:
                importlib.import_module(mod_name)
            except Exception as exc:
                errors.append(f"tools/{fname}: tools/ 路径 import 失败: {exc}")
    finally:
        sys.path[:] = saved_path

    hints = str((cat.get("agent_hints") or {}).get("session_collab") or "")
    if "勿传 action" not in hints and "无 action" not in hints:
        errors.append("tool_list agent_hints.session_collab 未声明 session_* 无 action")
    if "suspend=false" not in hints:
        errors.append("tool_list agent_hints.session_collab 未说明 suspend=false 仅查询")

    import agent_v3.core.agent_turn as agent_turn_mod

    if not hasattr(agent_turn_mod, "should_suspend_after_session_wait"):
        errors.append("agent_turn 未绑定 should_suspend_after_session_wait（deps 导出）")


def check_tools_agent_core_refs() -> None:
    from agent_v3 import agent_core as core

    tools_dir = ROOT / "tools"
    for p in tools_dir.glob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "agent_v3.agent_core" not in text:
            continue
        for m in re.finditer(
            r"(?:from agent_v3\.agent_core import|agent_v3\.agent_core\.)([a-zA-Z_][a-zA-Z0-9_]*)",
            text,
        ):
            sym = m.group(1)
            if not hasattr(core, sym):
                errors.append(f"tools/{p.name}: agent_core 缺少 {sym}")


def main() -> int:
    check_no_broken_prompt_names()
    check_no_v2_legacy_aliases()
    check_runtime_no_agent_v2()
    check_main_system_prompt()
    check_session_create_prompt_import()
    check_core_symbols()
    check_search_recursive_defaults()
    check_core_load_global_all()
    check_core_import_order()
    check_catalog_param_policy()
    check_catalog_agent_main_alignment()
    check_session_tools_contract()
    check_tools_agent_core_refs()

    if errors:
        print("HEALTH CHECK FAILED", len(errors))
        for e in errors:
            print(" -", e)
        return 1
    print("HEALTH CHECK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
