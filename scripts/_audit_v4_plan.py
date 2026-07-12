# -*- coding: utf-8 -*-
"""Full plan compliance audit for agent_v4 refactor."""
from __future__ import annotations

import ast
import importlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

errors = []
warns = []


def ok(msg):
    print("OK ", msg)


def fail(msg):
    errors.append(msg)
    print("FAIL", msg)


def warn(msg):
    warns.append(msg)
    print("WARN", msg)


def main():
    print("=== 1. Package boundary ===")
    if (ROOT / "agent_v3").exists():
        fail("agent_v3 directory still exists")
    else:
        ok("no agent_v3 directory")
    if not (ROOT / "agent_v4").is_dir():
        fail("agent_v4 missing")
    else:
        ok("agent_v4 present")

    hits = []
    for p in ROOT.rglob("*.py"):
        if "副本" in str(p):
            continue
        # 审计/迁移脚本可提及历史包名
        if "scripts" in p.parts:
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "agent_v3" in t:
            hits.append(str(p.relative_to(ROOT)))
    if hits:
        fail("agent_v3 refs in py: " + ", ".join(hits[:15]))
    else:
        ok("no agent_v3 imports in product *.py")

    entry = (ROOT / "deepseek_code_agent.py").read_text(encoding="utf-8")
    if "from agent_v4" not in entry or "agent_v3" in entry:
        fail("entry not exclusively agent_v4")
    else:
        ok("deepseek_code_agent.py -> agent_v4")
    if (ROOT / "deepseek_code_agent3.py").exists():
        fail("legacy deepseek_code_agent3.py still exists")

    print("=== 2. Runtime modules ===")
    rt = ROOT / "agent_v4" / "runtime"
    for n in (
        "turn_context.py",
        "event_sink.py",
        "host_policy.py",
        "scenario_injection.py",
        "agent_runtime.py",
        "__init__.py",
    ):
        if not (rt / n).is_file():
            fail("missing " + n)
        else:
            ok("runtime/" + n)

    # class names
    for mod, cls in (
        ("turn_context", "TurnContext"),
        ("event_sink", "EventSink"),
        ("event_sink", "YieldEventSink"),
        ("host_policy", "HostPolicy"),
        ("scenario_injection", "ScenarioInjection"),
        ("agent_runtime", "AgentRuntime"),
    ):
        m = importlib.import_module("agent_v4.runtime." + mod)
        if not hasattr(m, cls):
            fail("missing class %s.%s" % (mod, cls))
        else:
            ok("%s.%s" % (mod, cls))

    print("=== 3. Dependency direction ===")
    core_rt = []
    for p in (ROOT / "agent_v4" / "core").rglob("*.py"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "agent_v4.runtime" in t:
            core_rt.append(p.name)
    allowed = {
        "agent_turn.py",  # 薄委托 AgentRuntime
        "tool_runtime.py",  # execute_tool_script 下沉完整写门控 → HostPolicy
    }
    bad = [x for x in core_rt if x not in allowed]
    if bad:
        fail("core (non-agent_turn) imports runtime: " + str(bad))
    else:
        ok("only agent_turn/tool_runtime may import runtime among core")

    tools_rt = []
    for p in (ROOT / "tools").glob("*.py"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "agent_v4.runtime" in t:
            tools_rt.append(p.name)
    if tools_rt:
        fail("tools import runtime: " + str(tools_rt))
    else:
        ok("tools do not import runtime")

    print("=== 4. SSE ownership ===")
    pub_rt = []
    for p in rt.rglob("*.py"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "publish_conversation_event" in t:
            pub_rt.append(p.name)
    if pub_rt:
        fail("runtime calls publish_conversation_event: " + str(pub_rt))
    else:
        ok("runtime never publishes SSE")

    tr = (ROOT / "agent_v4" / "core" / "turn_runner.py").read_text(encoding="utf-8")
    if "def publish_conversation_event" not in tr:
        fail("turn_runner missing publish_conversation_event")
    else:
        ok("turn_runner owns publish_conversation_event")

    print("=== 5. agent_turn thin + AgentRuntime ===")
    at = (ROOT / "agent_v4" / "core" / "agent_turn.py").read_text(encoding="utf-8")
    if "AgentRuntime" not in at or "run_turn" not in at:
        fail("agent_turn not delegating to AgentRuntime")
    else:
        ok("agent_turn delegates to AgentRuntime.run_turn (%d lines)" % len(at.splitlines()))
    ar = (ROOT / "agent_v4" / "runtime" / "agent_runtime.py").read_text(encoding="utf-8")
    if "preflight_write_tool(" not in ar and "self.policy.gate_write_tool" not in ar:
        fail("AgentRuntime missing write preflight (preflight_write_tool / HostPolicy)")
    else:
        ok("AgentRuntime uses write preflight")
    if "self.scenario.sync_ephemeral_tail" not in ar:
        fail("AgentRuntime not using ScenarioInjection")
    else:
        ok("AgentRuntime uses ScenarioInjection.sync_ephemeral_tail")
    if "TurnContext(" not in ar or "yield _emit(" not in ar or "def _emit(" not in ar:
        fail("AgentRuntime missing TurnContext / _emit wiring")
    else:
        ok("AgentRuntime wires TurnContext + _emit")
    if "YieldEventSink(" in ar:
        fail("AgentRuntime must not buffer events with YieldEventSink on hot path")
    if "mode_tail" in (ROOT / "agent_v4" / "runtime" / "scenario_injection.py").read_text(encoding="utf-8") and "_ephemeral_mode_system_tail" in (
        ROOT / "agent_v4" / "runtime" / "scenario_injection.py"
    ).read_text(encoding="utf-8"):
        fail("ScenarioInjection owns mode_tail")
    else:
        # ensure mode still in context_pipeline
        cp = (ROOT / "agent_v4" / "core" / "context_pipeline.py").read_text(encoding="utf-8")
        if "_ephemeral_mode_system_tail" not in cp:
            fail("mode_tail missing from context_pipeline")
        else:
            ok("mode_tail stays in context_pipeline; ScenarioInjection excludes it")

    print("=== 6. Context lock ===")
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
    if CHUNK_ORDER != expected:
        fail("CHUNK_ORDER drifted: " + str(CHUNK_ORDER))
    else:
        ok("CHUNK_ORDER intact")
    cp = (ROOT / "agent_v4" / "core" / "context_pipeline.py").read_text(encoding="utf-8")
    for name in (
        "_build_api_messages_for_model",
        "_split_pure_and_full_dialogue",
        "_maybe_schedule_summarization",
        "_merge_pending_excerpts_for_conversation",
    ):
        if "def %s" % name not in cp:
            fail("context_pipeline missing " + name)
        else:
            ok("context_pipeline has " + name)

    print("=== 7. Tool CLI zero retention ===")
    cli = []
    no_agent_main = []
    for p in sorted((ROOT / "tools").glob("*.py")):
        if p.name in ("agent_common.py", "tool_help_share.py", "stdio_utf8.py"):
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        bad = []
        if re.search(r'if\s+__name__\s*==\s*[\'"]__main__[\'"]', t):
            bad.append("__main__")
        if re.search(r"^def main\(", t, re.M):
            bad.append("main")
        if re.search(r"^def build_parser\(", t, re.M):
            bad.append("build_parser")
        if bad:
            cli.append((p.name, bad))
        # catalog tools should have agent_main
        if "def agent_main" not in t and p.name.endswith(".py"):
            # some helpers may not
            if p.name not in ("command_safety.py", "agent_patch_engine.py", "stdio_utf8.py"):
                # check if it's a tool with agent_main historically
                try:
                    tree = ast.parse(t)
                    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                    if "agent_main" not in names and p.name.replace(".py", "") not in (
                        "tool_help_share",
                    ):
                        # only warn for known catalog scripts later
                        pass
                except SyntaxError:
                    fail("syntax error in " + p.name)
        if "def agent_main" not in t:
            no_agent_main.append(p.name)
    if cli:
        fail("CLI leftovers: " + str(cli))
    else:
        ok("no tool CLI (__main__/main/build_parser)")

    # catalog scripts must have agent_main
    import json

    cat = json.loads((ROOT / "tools" / "tool_list_agent.json").read_text(encoding="utf-8"))
    missing_am = []
    for t in cat.get("tools") or []:
        name = str(t.get("name") or "")
        fp = ROOT / "tools" / name
        if not fp.is_file():
            missing_am.append(name + "(file missing)")
            continue
        src = fp.read_text(encoding="utf-8", errors="ignore")
        if "def agent_main" not in src:
            missing_am.append(name)
    if missing_am:
        fail("catalog tools missing agent_main: " + str(missing_am))
    else:
        ok("all catalog tools have agent_main")

    trt = (ROOT / "agent_v4" / "core" / "tool_runtime.py").read_text(encoding="utf-8")
    if "_capture_tool_help_from_module" in trt:
        fail("tool_runtime still uses build_parser help path")
    else:
        ok("tool_help catalog-only in tool_runtime")

    print("=== 8. HostPolicy error types ===")
    hp = (ROOT / "agent_v4" / "runtime" / "host_policy.py").read_text(encoding="utf-8")
    for et in ("PreviewRequired", "ModeConflict", "AuditOnly"):
        if et not in hp:
            fail("HostPolicy missing error type " + et)
        else:
            ok("HostPolicy has " + et)

    print("=== 9. Import / health smoke ===")
    for k in list(sys.modules):
        if k.startswith("agent_v4"):
            del sys.modules[k]
    try:
        from agent_v4.http_app import create_app
        from agent_v4.core.agent_turn import run_agent_turn, _check_write_preview
        from agent_v4.runtime.agent_runtime import AgentRuntime
        from agent_v4.runtime.host_policy import HostPolicy

        assert callable(create_app)
        assert callable(run_agent_turn)
        assert callable(_check_write_preview)
        assert callable(AgentRuntime)
        assert callable(HostPolicy)
        ok("imports: create_app / run_agent_turn / AgentRuntime / HostPolicy")
    except Exception as e:
        fail("import smoke failed: " + repr(e))

    # EventSink no publish
    from agent_v4.runtime.event_sink import YieldEventSink

    s = YieldEventSink()
    s.emit({"type": "done"})
    assert s.drain()[0]["type"] == "done"
    ok("YieldEventSink collects without publish")

    print("=== 10. TurnContext fields ===")
    from agent_v4.runtime.turn_context import TurnContext

    slots = set(TurnContext.__slots__)
    for need in (
        "conversation_id",
        "run_id",
        "mode",
        "messages",
        "attachments",
        "previewed_files",
        "ephemeral_tail",
    ):
        if need not in slots:
            fail("TurnContext missing " + need)
    for forbid in ("CONVERSATIONS", "catalog"):
        if forbid in slots:
            fail("TurnContext wrongly holds " + forbid)
    ok("TurnContext slot shape OK")

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
