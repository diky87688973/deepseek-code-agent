#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性脚本：将 agent_core.py 按职责拆到 agent_v3/core/。运行后由 facade agent_core.py 聚合。"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "agent_core.py"
CORE = ROOT / "core"

# 函数名 -> 目标模块（未列出则归入 misc）
FN_MODULE: Dict[str, str] = {}
_groups = {
    "turn_control": [
        "_user_stopped_tool_result_dict",
        "_user_stopped_tool_content",
        "_turn_abort_requested",
        "_pad_trailing_missing_tool_results_for_user_stop",
        "_finish_conversation_stopped",
        "_ensure_conversation_loaded",
    ],
    "conversation_store": [
        "_subprocess_cli_help",
        "_enrich_tool_error_message",
        "_enrich_tool_result_error",
        "_intent_tool_hints",
        "_unknown_tool_result",
        "_execute_run_type",
        "_safe_json_loads",
        "_reasoning_delta_field_names",
        "_best_message_reasoning_field",
        "_session_date_group_from_path",
        "_find_conversation_file",
        "_find_title_file",
        "_save_title_file",
        "_conversation_file_for_save",
        "_append_session_message_v2",
        "_save_conversation",
        "_load_conversation",
        "_persisted_session_unreadable_after_load",
        "_chat_history_from_messages",
        "_load_conversation_title",
        "_fallback_title_from_messages",
        "_clean_conversation_title",
        "_is_placeholder_conversation_title",
        "_generate_conversation_title",
        "_save_last_open_session_state",
        "_load_last_open_session_state",
        "_format_catalog_tool_examples",
        "_catalog_tool_full_description",
    ],
    "tool_runtime": [
        "load_catalog",
        "api_function_name",
        "catalog_to_openai_tools",
        "_openai_tools_sort_key",
        "normalize_cli_args",
        "_camel_to_snake",
        "_agent_main_param_name",
        "_strip_internal_tool_result",
        "_execute_tool_agent_main",
        "_catalog_public_arg_names",
        "_validate_public_tool_args",
        "_normalize_nested_tool_arg_keys",
        "_coerce_tool_arguments_for_agent",
        "_capture_tool_help_from_module",
        "_capture_tool_help_from_catalog",
        "attach_tool_help_on_failure",
        "maybe_attach_write_tool_host_dry_run_notice",
        "execute_tool_script",
        "_tool_progress_sse_event",
        "_tool_host_wall_timeout_sec",
        "_execute_tool_script_stoppable",
        "_kling_estimate_cost",
        "_execute_tool_script_locked",
        "preview_payload",
        "preview_tool_result",
        "_fenced_diff_from_unified_lines",
        "_chat_diff_markdown_for_tool",
        "_truncate_tool_result",
        "_is_user_confirm_required",
        "_merge_confirm_into_user_confirm_args",
        "_truncate_large_values",
        "_is_preview_intent",
        "_is_audit_only_intent",
        "_get_catalog_hints_system_prompt",
        "_build_direct_preview_message",
    ],
    "llm_stream": [
        "_chat_api_key_available",
        "_get_reasoning_effort",
        "_set_reasoning_effort",
        "deepseek_request",
        "deepseek_stream_request",
        "_choice_snapshot_message",
        "_finalize_stream_reasoning",
        "_reasoning_stream_finalize_events",
        "_assistant_display_content_for_sse",
        "_assistant_message_for_persist",
        "_finalize_stream_content_text",
        "_merge_stream_tool_calls",
        "_tool_calls_from_snapshot_message",
        "_merge_stream_tool_calls_with_snapshot",
        "_max_tool_rounds_user_hint",
        "_ephemeral_max_tool_rounds_wrap_user",
        "_normalize_client_ip_for_tools",
    ],
    "modes_kb": [
        "_has_explicit_mode_command",
        "_resolve_conversation_mode",
        "_kb_rel_has_hidden_segment",
        "_kb_safe_resolve_rel",
        "list_kb_files_for_api",
        "_kb_file_allowed_when_checked",
        "_read_kb_file_text",
        "_build_kb_system_messages",
        "_kb_attached_file_count",
        "_extract_dispatch_title",
    ],
    "context_pipeline": [
        "_tail_drop_incomplete_tool_assistant",
        "_strip_internal_message_for_api",
        "_include_message_for_api",
        "_normalize_persisted_conversation",
        "_find_first_user_index",
        "_message_id_set",
        "_reconcile_peer_messages_from_store",
        "_has_new_peer_messages_after_turn",
        "_clear_turn_start_message_ids",
        "_resume_turn_for_pending_peer_messages",
        "_context_manager_v2",
        "_split_pure_and_full_dialogue",
        "_count_user_turns_in_messages",
        "_fold_pure_window_for_api",
        "_strip_tool_trace_for_summary",
        "_char_token_estimate_weight",
        "_estimate_tokens_text_ratio",
        "_approx_tokens_text",
        "_approx_tokens_message",
        "_build_skill_registry_message",
        "_build_auto_load_skill_messages",
        "_build_team_role_prefix",
        "_build_context_segments",
        "_compute_context_layout_payload",
        "_parse_excerpt_file",
        "_excerpt_disk_paths_for_cid",
        "_excerpt_file_needs_merge",
        "_mark_excerpt_merged",
        "_range_is_only_summaries",
        "_insert_summary_message",
        "_merge_one_excerpt_file",
        "_collect_excerpt_paths_to_merge",
        "_merge_pending_excerpts_for_conversation",
        "_merge_pending_excerpts_for_conversation_impl",
        "messages_for_history_api",
        "_ephemeral_mode_system_tail",
        "_stored_mode_for_tail",
        "_assistant_tool_call_ids",
        "_synthetic_tool_result",
        "_sanitize_tool_pairing_for_api",
        "_build_api_messages_for_model",
        "_is_degenerate_summary_body",
        "_summarize_messages_slice_with_llm",
        "_maybe_schedule_summarization",
    ],
    "peer_mesh": [
        "_find_pending_requires_reply_peer_message",
        "_exec_requires_reply_true",
        "_normalize_sent_target_ids",
        "_mark_requires_reply_answered_for_senders",
        "_extract_reply_tool_target_ids",
        "_turn_replied_to_peer",
        "_apply_inbound_requires_reply_answered",
        "_ephemeral_requires_reply_priority",
        "_api_messages_with_ephemeral_tail",
    ],
    "agent_turn": ["run_agent_turn"],
    "turn_runner": [
        "publish_conversation_event",
        "_append_incoming_session_message_impl",
        "_append_incoming_session_message",
        "_drain_session_inbox_after_run",
        "start_background_agent_turn",
    ],
    "ui_bundle": ["_scope_hljs_css"],
    "usage_accum": [
        "_default_usage_accum_dict",
        "_load_usage_accumulator",
        "_save_usage_accumulator",
    ],
}
for mod, names in _groups.items():
    for n in names:
        FN_MODULE[n] = mod

HEADER = '''# -*- coding: utf-8 -*-
"""agent_v3.core.{mod} — 自 agent_core 拆出。AI-GENERATED (Cursor)"""
from __future__ import annotations

'''

SHARED_IMPORTS_START = 6  # skip shebang + docstring line count approx - we'll copy from source


def _extract_top_imports(source: str) -> str:
    lines = source.splitlines(keepends=True)
    out: List[str] = []
    i = 0
    if lines and lines[0].startswith("#!"):
        i = 1
    if i < len(lines) and '"""' in lines[i]:
        i += 1
        while i < len(lines) and '"""' not in lines[i]:
            i += 1
        i += 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("class "):
            break
        if stripped.startswith("_FILE_SEARCH") or stripped.startswith("USER_STOPPED"):
            break
        if stripped and not (
            stripped.startswith("import ")
            or stripped.startswith("from ")
            or stripped.startswith("#")
            or stripped == ""
        ):
            # module-level const before imports ended - still part of imports block in our file
            if stripped.startswith("_") or stripped.startswith("WRITE_") or stripped.startswith("PLAN_"):
                break
        out.append(line)
        i += 1
    return "".join(out)


def _module_level_assignments(source: str) -> Dict[str, str]:
    """提取模块级赋值（非 import、非 def）按名称。"""
    tree = ast.parse(source)
    lines = source.splitlines()
    assigns: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    seg = "\n".join(lines[node.lineno - 1 : node.end_lineno])
                    assigns[t.id] = seg
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            seg = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            assigns[node.target.id] = seg
    return assigns


def main() -> None:
    source = SRC.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    imports_block = _extract_top_imports(source)

    func_nodes: Dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            func_nodes[node.name] = node

    mod_funcs: Dict[str, List[str]] = {}
    for name, node in func_nodes.items():
        mod = FN_MODULE.get(name, "misc")
        mod_funcs.setdefault(mod, []).append(name)

    # 模块级代码块（UI 内联等）
    mod_level: Dict[str, List[str]] = {
        "shared_state": [],
        "ui_bundle": [],
    }
    assigns = _module_level_assignments(source)
    ui_keys = {
        "UI_HTML_FILE",
        "RESET_CSS_FILE",
        "UI_CSS_FILE",
        "UI_JS_FILE",
        "THEME_UI_JS_FILE",
        "HLJS_JS_FILE",
        "CODE_HIGHLIGHT_JS_FILE",
        "HLJS_CSS_DARK_FILE",
        "HLJS_CSS_LIGHT_FILE",
        "_INLINE_CSS",
        "_INLINE_JS",
        "TTS_JS_FILE",
        "_INLINE_JS2",
        "_INLINE_HTML_TMPL",
        "INLINE_UI_HTML",
    }
    state_keys = {
        k
        for k in assigns
        if k.startswith("_")
        or k in {
            "USER_STOPPED_TOOL_MESSAGE",
            "WRITE_TOOL_SCRIPTS",
            "MAX_TOOL_RESULT_CHARS",
            "PLAN_MODE_KEYS",
            "EXECUTE_MODE_KEYS",
            "PLAN_MODE_COMMANDS",
            "EXECUTE_MODE_COMMANDS",
            "SESSION_PERSIST_UNREADABLE_SSE_DETAIL",
            "_TURN_START_MESSAGE_IDS",
        }
    }
    for k, seg in assigns.items():
        if k in ui_keys:
            mod_level["ui_bundle"].append(seg)
        elif k in state_keys or k.endswith("_CACHE") or "frozenset" in seg or "FROZENSET" in seg.upper():
            mod_level["shared_state"].append(seg)

    CORE.mkdir(exist_ok=True)
    (CORE / "__init__.py").write_text(
        '# -*- coding: utf-8\n"""agent_v3 核心子模块。"""\n', encoding="utf-8"
    )

    all_mods = sorted(set(mod_funcs) | set(mod_level))
    written: Dict[str, Path] = {}
    for mod in all_mods:
        parts: List[str] = [HEADER.format(mod=mod), imports_block, "\n"]
        for seg in mod_level.get(mod, []):
            parts.append(seg + "\n\n")
        for fname in sorted(mod_funcs.get(mod, [])):
            node = func_nodes[fname]
            parts.append(ast.get_source_segment(source, node) or "")
            parts.append("\n\n")
        path = CORE / f"{mod}.py"
        path.write_text("".join(parts), encoding="utf-8")
        written[mod] = path

    # facade
    facade_lines = [
        '#!/usr/bin/env python3\n# -*- coding: utf-8\n',
        '"""agent_v3 门面：聚合 core 子模块，保持 routes 层 `import agent_core as core` 兼容。"""\n',
        "from __future__ import annotations\n\n",
        imports_block,
        "\n",
    ]
    for mod in sorted(written):
        facade_lines.append(f"from agent_v3.core.{mod} import *  # noqa: F403\n")
    facade_lines.append("\n# 显式 re-export bootstrap / live_state 符号供 routes 使用\n")
    facade_lines.append("from agent_v3.bootstrap import *  # noqa: F403,F401\n")
    facade_lines.append("from agent_v3.live_state import *  # noqa: F403,F401\n")
    facade_lines.append("from util.agent_model_dispatch import ALLOWED_MODELS, default_model_from_env, effective_model, set_conversation_model  # noqa: F401\n")
    facade_lines.append("from util.agent_deepseek_pricing import get_model_pricing_snapshot  # noqa: F401\n")

    SRC.write_text("".join(facade_lines), encoding="utf-8")
    print("split OK:", ", ".join(sorted(written)))


if __name__ == "__main__":
    main()
