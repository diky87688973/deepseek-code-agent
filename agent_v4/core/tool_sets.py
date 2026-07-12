# -*- coding: utf-8 -*-
"""写类工具集合：单一定义源，避免 host_policy / host_quality / runtime 三处漂移。"""
from __future__ import annotations

# 宿主写门控范围（Plan/Execute/Audit/预览质量）
WRITE_GATED_SCRIPTS: frozenset = frozenset(
    {
        "file_ops.py",
        "python_inline.py",
        "write_file.py",
        "read_write.py",
        "replace_in_file.py",
        "replace_undo.py",
        "apply_patch.py",
        "run_command.py",
        "delete_file.py",
    }
)

# 必须先 dry_run 预览再真写的路径类工具（⊆ WRITE_GATED）
PREVIEW_REQUIRED_SCRIPTS: frozenset = frozenset(
    {"write_file.py", "replace_in_file.py", "read_write.py", "apply_patch.py"}
)

# 宿主质量诊断跟踪的写路径（与 PREVIEW 对齐）
QUALITY_WRITE_PATH_SCRIPTS: frozenset = frozenset(PREVIEW_REQUIRED_SCRIPTS)

# 兼容旧名
WRITE_TOOL_SCRIPTS = WRITE_GATED_SCRIPTS


def assert_tool_sets_consistent() -> None:
    if not PREVIEW_REQUIRED_SCRIPTS <= WRITE_GATED_SCRIPTS:
        raise AssertionError("PREVIEW_REQUIRED_SCRIPTS must be subset of WRITE_GATED_SCRIPTS")
    if QUALITY_WRITE_PATH_SCRIPTS != PREVIEW_REQUIRED_SCRIPTS:
        raise AssertionError("QUALITY_WRITE_PATH_SCRIPTS must equal PREVIEW_REQUIRED_SCRIPTS")
