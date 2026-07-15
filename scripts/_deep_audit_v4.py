# -*- coding: utf-8 -*-
"""Deep audit: backup vs current for tools + agent package integrity."""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAK_ROOT = Path(r"D:\FanFiles\SVN_Projects\apps\ai-parent\ai-agent\code-web-agent - 副本")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

errors = []
warns = []


def fail(msg: str) -> None:
    errors.append(msg)
    print("FAIL", msg)


def warn(msg: str) -> None:
    warns.append(msg)
    print("WARN", msg)


def ok(msg: str) -> None:
    print("OK  ", msg)


def _defs(src: str):
    tree = ast.parse(src)
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = n
    return out


def _agent_main_params(src: str):
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "agent_main":
            return [a.arg for a in n.args.args + n.args.kwonlyargs]
    return None


def audit_tools():
    print("=== TOOLS vs BACKUP ===")
    bak = BAK_ROOT / "tools"
    cur = ROOT / "tools"
    if not bak.is_dir():
        fail("backup tools missing")
        return

    bak_files = {p.name for p in bak.glob("*.py")}
    cur_files = {p.name for p in cur.glob("*.py")}
    # tool_help_share intentionally removed
    expected_removed = {"tool_help_share.py"}
    missing = (bak_files - cur_files) - expected_removed
    extra = cur_files - bak_files
    if missing:
        fail("tools missing vs bak: " + str(sorted(missing)))
    else:
        ok("no unexpected missing tool files")
    if extra:
        warn("extra tool files: " + str(sorted(extra)))

    cat = json.loads((cur / "tool_list_agent.json").read_text(encoding="utf-8"))
    catalog_names = [str(t.get("name") or "") for t in cat.get("tools") or []]

    for name in sorted(bak_files - expected_removed):
        bp = bak / name
        cp = cur / name
        if not cp.is_file():
            continue
        bsrc = bp.read_text(encoding="utf-8-sig")
        csrc = cp.read_text(encoding="utf-8")
        if "agent_v3" in csrc:
            fail("%s still references agent_v3" % name)
        bdefs = set(_defs(bsrc)) - {"main", "build_parser"}
        cdefs = set(_defs(csrc))
        lost = sorted(bdefs - cdefs)
        # CLI-only helpers intentionally removed with main/build_parser
        tool_only_ok = {
            "python_inline.py": {"_emit_envelope_file_and_stdout"},
        }
        lost = [x for x in lost if x not in tool_only_ok.get(name, set())]
        if lost:
            fail("%s lost symbols vs bak: %s" % (name, lost))
        if name in catalog_names or name in (
            "session_wait.py",
            "look_screenshot.py",
            "python_inline.py",
        ):
            if "def agent_main" not in csrc and name not in (
                "agent_common.py",
                "command_safety.py",
                "agent_patch_engine.py",
                "stdio_utf8.py",
            ):
                # some helpers have no agent_main
                bparams = _agent_main_params(bsrc)
                if bparams is not None and _agent_main_params(csrc) is None:
                    fail("%s lost agent_main" % name)
            bparams = _agent_main_params(bsrc)
            cparams = _agent_main_params(csrc)
            if bparams is not None and cparams is not None:
                # parser_for_help may be intentionally removed
                bset = set(bparams) - {"parser_for_help"}
                cset = set(cparams) - {"parser_for_help"}
                if bset != cset:
                    fail(
                        "%s agent_main params drifted: -%s +%s"
                        % (name, sorted(bset - cset), sorted(cset - bset))
                    )
        # CLI must be gone
        if re.search(r"^def main\(", csrc, re.M) or re.search(
            r"^def build_parser\(", csrc, re.M
        ):
            fail("%s still has CLI entry" % name)
        if re.search(r'if\s+__name__\s*==\s*[\'"]__main__[\'"]', csrc):
            fail("%s still has __main__" % name)
        # docstring lies about CLI
        if "仅供人工调试" in csrc or "build_parser() 供" in csrc or "`build_parser` 供" in csrc:
            warn("%s docstring still advertises CLI" % name)

    # catalog alignment quick
    for name in catalog_names:
        if not (cur / name).is_file():
            fail("catalog tool file missing: " + name)


def audit_package():
    print("=== PACKAGE / RUNTIME ===")
    if (ROOT / "agent_v3").exists():
        fail("agent_v3 directory exists")
    else:
        ok("no agent_v3 dir")

    hits = []
    for p in ROOT.rglob("*.py"):
        if "scripts" in p.parts or "副本" in str(p):
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "agent_v3" in t:
            hits.append(str(p.relative_to(ROOT)))
    if hits:
        fail("product agent_v3 refs: " + ", ".join(hits[:20]))
    else:
        ok("no product agent_v3 refs")

    # runtime must not publish
    for p in (ROOT / "agent_v4" / "runtime").rglob("*.py"):
        t = p.read_text(encoding="utf-8")
        if "publish_conversation_event(" in t:
            fail("runtime publishes: " + p.name)

    # bare yields in agent_runtime
    ar = (ROOT / "agent_v4" / "runtime" / "agent_runtime.py").read_text(encoding="utf-8")
    bare = []
    for i, ln in enumerate(ar.splitlines(), 1):
        s = ln.lstrip()
        if s.startswith("yield ") and "_emit(" not in s:
            bare.append(i)
    if bare:
        fail("bare yields in agent_runtime lines=%s" % bare[:20])
    else:
        ok("all agent_runtime yields go through _emit")

    if "TurnContext(" not in ar:
        fail("TurnContext not constructed in run_turn")
    else:
        ok("TurnContext constructed in run_turn")
    if "def _emit(" not in ar:
        fail("_emit missing in run_turn")
    if "YieldEventSink(" in ar:
        fail("YieldEventSink must not be used on production hot path")
    # _emit 必须在任何 yield _emit 之前定义
    emit_def = None
    first_yield_emit = None
    for i, ln in enumerate(ar.splitlines(), 1):
        if emit_def is None and ln.lstrip().startswith("def _emit("):
            emit_def = i
        if first_yield_emit is None and "yield _emit(" in ln:
            first_yield_emit = i
    if emit_def is None or first_yield_emit is None or emit_def >= first_yield_emit:
        fail("_emit defined after first yield _emit (UnboundLocalError risk) def=%s yield=%s" % (emit_def, first_yield_emit))
    else:
        ok("_emit defined before first yield (_emit@%s < yield@%s)" % (emit_def, first_yield_emit))
    if "preflight_write_tool(" not in ar and "self.policy.gate_write_tool" not in ar:
        fail("HostPolicy/preflight gate not used")
    if "self.scenario.sync_ephemeral_tail" not in ar:
        fail("ScenarioInjection not used")
    # WRITE∩PROGRESS 不得被 WRITE 分支独占（run_command/python_inline 需进度路径）
    if "WRITE∩PROGRESS" not in ar and "含 WRITE" not in ar:
        # structural: gate then progress
        if "if script in _TOOL_PROGRESS_SCRIPTS:" not in ar:
            fail("progress script branch missing")
        # ensure progress branch is not nested only under `else:` of WRITE with exclusive execute
        # smoke: import and first event
    try:
        from agent_v4.core.shared_state import CONVERSATIONS
        from agent_v4.core.agent_turn import run_agent_turn

        _cid = "__audit_emit_order__"
        CONVERSATIONS[_cid] = []
        _ev = next(run_agent_turn(_cid, "ping", run_id="audit-emit"))
        if not isinstance(_ev, dict) or _ev.get("type") != "conversation":
            fail("run_agent_turn first event not conversation: %r" % (_ev,))
        else:
            ok("run_agent_turn first event smoke OK")
    except Exception as e:
        fail("run_agent_turn first event smoke failed: %s" % e)

    # mode_tail ownership
    sc = (ROOT / "agent_v4" / "runtime" / "scenario_injection.py").read_text(encoding="utf-8")
    cp = (ROOT / "agent_v4" / "core" / "context_pipeline.py").read_text(encoding="utf-8")
    if "_ephemeral_mode_system_tail" in sc:
        fail("ScenarioInjection owns mode_tail")
    if "_ephemeral_mode_system_tail" not in cp:
        fail("context_pipeline missing mode_tail")

    # compare critical gate messages with bak agent_turn
    bak_turn = BAK_ROOT / "agent_v3" / "core" / "agent_turn.py"
    if bak_turn.is_file():
        bt = bak_turn.read_text(encoding="utf-8", errors="ignore")
        hp = (ROOT / "agent_v4" / "runtime" / "host_policy.py").read_text(encoding="utf-8")
        for msg_key in (
            "PreviewRequired",
            "ModeConflict",
            "AuditOnly",
            "Execute/Auto 模式下禁止直接 dry_run=false",
            "当前为 Plan 模式，禁止执行写操作",
            "未找到执行清单",
        ):
            if msg_key in bt and msg_key not in hp and msg_key not in ar:
                # AuditOnly message may live in shared_state
                ss = (ROOT / "agent_v4" / "core" / "shared_state.py").read_text(encoding="utf-8")
                if msg_key not in ss and msg_key not in ar:
                    fail("gate string missing vs bak: " + msg_key)

    # CHUNK_ORDER
    from util.context_manager_v2 import CHUNK_ORDER

    expected = (
        "系统提示词",
        "Skills",
        "知识库",
        "记忆文件",
        "远期记忆",
        "近期记忆",
        "模式",
    )
    if tuple(CHUNK_ORDER) != expected:
        fail("CHUNK_ORDER drifted")
    else:
        ok("CHUNK_ORDER intact")


def audit_imports_smoke():
    print("=== IMPORT / CALL SMOKE ===")
    for k in list(sys.modules):
        if k.startswith("agent_v4") or k in (
            "replace_in_file",
            "write_file",
            "read_file",
            "session_wait",
            "python_inline",
        ):
            del sys.modules[k]
    try:
        from agent_v4.http_app import create_app
        from agent_v4.core.agent_turn import run_agent_turn, _check_write_preview
        from agent_v4.runtime.agent_runtime import AgentRuntime
        from agent_v4.runtime.host_policy import HostPolicy

        create_app()
        ok("create_app")
        # HostPolicy gates
        class Todo:
            session_lists = {}

        from agent_v4.core.shared_state import _AUDIT_WRITE_BLOCK_MSG

        p = HostPolicy()
        r = p.gate_write_tool(
            "c",
            "write_file.py",
            {"path": "a.py", "dry_run": False},
            step_title="t",
            previewed_files={},
            written_files={},
            current_mode="plan",
            audit_only=False,
            audit_block_msg=_AUDIT_WRITE_BLOCK_MSG,
            todo_list_mod=Todo(),
        )
        if not r or r["error"]["type"] != "ModeConflict":
            fail("plan write gate broken: " + str(r))
        else:
            ok("plan write gate")
        r = p.gate_write_tool(
            "c",
            "write_file.py",
            {"path": "a.py", "dry_run": False},
            step_title="t",
            previewed_files={},
            written_files={},
            current_mode="execute",
            audit_only=False,
            audit_block_msg=_AUDIT_WRITE_BLOCK_MSG,
            todo_list_mod=Todo(),
        )
        if not r or r["error"]["type"] != "ModeConflict":
            fail("execute-no-todo gate broken: " + str(r))
        else:
            ok("execute-no-todo gate")

        # tool imports
        for mod in (
            "replace_in_file",
            "write_file",
            "read_file",
            "grep_files",
            "session_wait",
            "session_send",
            "python_inline",
            "look_screenshot",
            "run_command",
            "todo_list",
        ):
            m = importlib.import_module(mod)
            if not hasattr(m, "agent_main"):
                fail(mod + " missing agent_main")
            else:
                # ensure callable
                inspect.signature(m.agent_main)
        ok("critical tools import + agent_main signatures")
    except Exception as e:
        fail("import/call smoke: " + repr(e))


def audit_publish_surface():
    print("=== PUBLISH SURFACE ===")
    pubs = []
    for p in ROOT.rglob("*.py"):
        if "scripts" in p.parts or "副本" in str(p):
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "publish_conversation_event(" in t:
            pubs.append(str(p.relative_to(ROOT)))
    # allowed: turn_runner, agent_core reexport path, tools session_* maybe, tts manager
    allowed_prefix = (
        "agent_v4\\core\\turn_runner.py",
        "agent_v4/core/turn_runner.py",
        "agent_v4\\agent_core.py",
        "agent_v4/agent_core.py",
        "util\\tts\\",
        "util/tts/",
        "tools\\session_",
        "tools/session_",
    )
    unexpected = []
    for p in pubs:
        if any(p.replace("/", "\\").startswith(a.replace("/", "\\")) or a.replace("\\", "/") in p.replace("\\", "/") for a in allowed_prefix):
            continue
        if p.endswith("turn_runner.py") or "session_" in p or "tts" in p or p.endswith("agent_core.py"):
            continue
        unexpected.append(p)
    if unexpected:
        warn("publish call sites to review: " + ", ".join(unexpected[:30]))
    else:
        ok("publish call sites within expected modules (%d files)" % len(pubs))
    print("publish files:", ", ".join(pubs))


def main() -> int:
    audit_tools()
    audit_package()
    audit_imports_smoke()
    audit_publish_surface()
    print()
    print("ERRORS", len(errors))
    for e in errors:
        print(" -", e)
    print("WARNS", len(warns))
    for w in warns:
        print(" -", w)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
