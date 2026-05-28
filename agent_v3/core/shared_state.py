# -*- coding: utf-8
"""模块级可变状态与常量。"""
from __future__ import annotations

from agent_v3.core.deps import *  # noqa: F403

_RESTRICTED_TOOLS: frozenset = frozenset({"file_search.py"})
_TOOL_PROGRESS_SCRIPTS: frozenset = frozenset(
    {"file_search.py", "grep_files.py", "regex_locate.py", "run_command.py", "python_inline.py"}
)
WRITE_TOOL_SCRIPTS: frozenset = frozenset(
    {
        "file_ops.py",
        "python_inline.py",
        "write_file.py",
        "read_write.py",
        "replace_in_file.py",
        "apply_patch.py",
        "run_command.py",
        "delete_file.py",
    }
)
_CATALOG_HINTS_SYSTEM_CACHE: Optional[str] = None

_HOST_DRY_RUN_NOTICE_ZH = (
    "【宿主提示】本次为预览（dry_run=true），磁盘未被修改。"
    "确认写入请传 dry_run: false（命令行对应 --commit），并满足当前会话模式（如 Execute）与执行清单等要求。"
)

_AUDIT_WRITE_BLOCK_MSG = (
    "当前为仅审查模式（用户消息触发了只读/审查意图），写盘类工具已被宿主拒绝。"
    "请去掉「只报告/不要改代码」类表述，并在界面切换到 Execute 模式后再修改文件。"
)

USER_STOPPED_TOOL_MESSAGE = "任务已被用户停止"

_HELP_CAPTURE_MAX = 24000

_CATALOG_TOOL_DESCRIPTION_MAX_CHARS = 12000
_CATALOG_EXAMPLES_MAX_COUNT = 2
_TOOL_HELP_MAX_CHARS = 6000
_TOOL_HELP_COMPACT_MAX_CHARS = 1200

_COERCE_JSON_CONTAINER_KEYS = frozenset({"rules", "items", "confirms", "indices"})

_HOST_INJECTED_TOOL_ARG_NAMES = frozenset({"conversation_id", "_progress_dict", "run_type"})

# 已从 schema 移除的 action；旧模型若仍传入则宿主剥离，避免 BadToolArguments
_SESSION_DROP_LEGACY_ACTION_SCRIPTS = frozenset(
    {
        "session_send.py",
        "session_multisend.py",
        "session_broadcast.py",
        "session_list.py",
        "session_wait.py",
        "session_create.py",
    }
)

_KLING_GENERATE_ACTIONS = {
    "text2video", "image2video", "multimodal2video", "multi_image2video",
    "motion_control", "video_extend", "lip_sync", "avatar",
    "text2image", "image2image", "multi_image2image", "omni_image",
    "virtual_try_on", "text2audio",
}

_CHAT_DIFF_BODY_MAX = 16000

_KB_MAX_FILE_SIZE = int(AGENT_CONFIG["AGENT_KB_MAX_FILE_SIZE"])

_TOOL_REPAIR_BODY = json.dumps(
    {"ok": False, "error": {"type": "ClientRepair", "message": "工具回合未完整配对，会话自检已填入占位结果"}},
    ensure_ascii=False,
)

_AGENT_SUMMARY_BRIDGE_TAIL = "\n\n【以上为历史摘要，下面继续当前对话】"

_PEER_REPLY_TOOL_APIS = frozenset({"session_send", "session_multisend", "session_broadcast"})

SESSION_PERSIST_UNREADABLE_SSE_DETAIL = "当前会话已停止，请重新发起会话。"

MAX_TOOL_RESULT_CHARS: int = 32000

_REASONING_EFFORTS: Dict[str, str] = {}

PLAN_MODE_KEYS = ("plan模式", "规划模式", "先给方案", "只给方案", "仅方案", "进入plan", "先不要执行")
EXECUTE_MODE_KEYS = ("执行模式", "进入执行", "开始执行", "落地", "实施", "按方案执行")
PLAN_MODE_COMMANDS = ("/plan", "#plan", "\\plan")
EXECUTE_MODE_COMMANDS = ("/execute", "#execute", "\\execute")

PURE_WINDOW_NO_FINAL_ASSISTANT = "（本轮含工具调用，完整细节见近期完整对话。）"

_PURE_ANCHOR_CACHE: Dict[str, int] = {}

_team_role_cache: Dict[str, str] = {}

_TURN_START_MESSAGE_IDS: Dict[str, Set[str]] = {}

TOOL_PAIRING_SYNTH_MAX_MISSING = 1

