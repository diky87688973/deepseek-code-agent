# -*- coding: utf-8 -*-
"""共享依赖导入（bootstrap、live_state、util）。"""
from __future__ import annotations

import asyncio
import copy
import difflib
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_v4.bootstrap import (
    AGENT_CONFIG,
    AGENT_ROOT,
    AT_MESSAGE_FILE_PREFETCH,
    AUDIT_INTENT_KEYS,
    CONTEXT_FULL_USER_ROUNDS,
    CONTEXT_LAYOUT_BUDGET_TOKENS,
    CONTEXT_PURE_USER_ROUNDS,
    CONTEXT_SUMMARY_TOKEN_THRESHOLD,
    DATA_ROOT,
    EXCERPTS_DIR,
    IMAGE_TOKENS_PER_IMAGE,
    KB_BASE_DIR,
    LAST_OPEN_SESSION_STATE_FILE,
    MAX_TOOL_ROUNDS,
    MAX_CONSECUTIVE_PEER_TURNS,
    MIN_PEER_TURN_INTERVAL_SEC,
    PREVIEW_INTENT_KEYS,
    SESSION_APP_ENTROPY,
    SESSION_DIR,
    SESSION_ENCRYPTION_MAGIC,
    SESSION_KEY_FILE,
    SUMMARY_IN_PROGRESS_TTL_SEC,
    SUMMARY_OUTPUT_MAX_CHARS,
    SUMMARY_THINKING_ENABLED,
    TOKEN_ESTIMATE_EN_PER_CHAR,
    TOKEN_ESTIMATE_ZH_PER_CHAR,
    TOOLS_DIR,
    TOOL_LIST_JSON,
    UI_RESTORE_MAX_CHAT_ITEMS,
    UI_RESTORE_MAX_TABS,
    USER_RULES_SYSTEM_PROMPT,
    USAGE_ACCUM_FILE,
    _KB_CHECKED_LOCK,
    _KB_CHECKED_STATE,
    _ensure_tools_sys_path,
    _execute_todo_list,
    _kb_load_single_cid_checked,
    _kb_persist_checked,
    _log_agent_console_sse,
    _log_agent_console_tool,
    _record_tool_debug_failure,
    _resolve_tool_script_path,
    _strip_config_path_value,
    _todo_list_mod,
)
from agent_v4.version import AGENT_APP_VERSION
from agent_v4.live_state import (
    CONVERSATIONS,
    CONVERSATION_AUDIT_ONLY,
    CONVERSATION_MODES,
    PENDING_EXCERPT_PATHS,
    PENDING_USER_CONFIRM,
    SESSION_INBOX,
    SUMMARY_IN_PROGRESS,
    _SUMMARY_STATE_LOCK,
    _TOOL_EXEC_LOCK,
    get_tool_exec_lock,
    is_file_search_allowed,
    set_file_search_allowed,
    _begin_conversation_run,
    _consume_conversation_stop_requested,
    _end_conversation_run,
    _peek_conversation_stop_requested,
    clear_agent_wait,
    should_suspend_after_session_wait,
    conversation_run_locks,
    enqueue_session_inbox,
    get_conversation_run_lock,
    pop_session_inbox,
    pop_waits_satisfied_by,
    publish_global_sse_event,
    reset_peer_turn_chain,
    try_acquire_peer_turn_slot,
    _request_conversation_stop,
    _ACTIVE_CONVERSATION_RUNS,
    server_shutting_down,
    create_confirm_id,
    consume_confirm_id,
)
from util.agent_tool_budget import (
    apply_turn_tool_budget_to_result,
    tool_call_limit_reached_result,
    turn_tool_budget_exhausted,
)
from util.agent_prompt_constants_v2 import (
    AGENT_CODE_HINT_SYSTEM_PROMPT,
    TOOL_AGENT_AUDIT_MODE_PROMPT,
    TOOL_AGENT_PLAN_MODE_PROMPT,
    TOOL_AGENT_AUTO_MODE_PROMPT,
    TOOL_AGENT_SYSTEM_PROMPT,
    TOOL_AGENT_EXECUTE_MODE_PROMPT,
    build_catalog_hints_system_prompt,
    ephemeral_requires_reply_priority_prompt,
    format_agent_max_tool_rounds_user_hint,
)
from util.agent_model_dispatch import (
    ALLOWED_MODELS,
    default_model_from_env,
    effective_model,
    model_max_context_tokens,
    set_conversation_model,
)
from util.agent_deepseek_pricing import get_model_pricing_snapshot
from util.agent_openai_compatible_client import chat_completion_request, chat_completion_stream
from util.context_manager_v2 import ContextManager, adjust_excerpt_range_half_open as _adjust_excerpt_range_half_open
from util.session_persist import (
    append_raw_message as _append_raw_message,
    bootstrap_raw_from_messages as _bootstrap_raw_from_messages,
    cache_session_json_path as _cache_session_json_path,
    ensure_conversation_message_ids_v2 as _ensure_conversation_message_ids_v2,
    excerpt_meta_round_ids as _excerpt_meta_round_ids,
    load_messages_from_raw as _load_messages_from_raw,
    remove_messages_by_round_ids as _remove_messages_by_round_ids,
    resolve_session_json_path as _resolve_session_json_path,
    round_ids_from_messages as _round_ids_from_messages,
    stamp_message_v2 as _stamp_message_v2,
)
from util.session_crypto import (
    decrypt_session_payload as _decrypt_session_payload,
    encrypt_session_payload as _encrypt_session_payload,
)
from util.session_store_v2 import new_conversation_id as _new_conversation_id
from util.skill_manager import get_skill_manager as _get_skill_manager
from agent_v4.sse_events import (
    context_layout_event as _context_layout_event,
    conversation_sse_event as _conversation_sse_event,
)
