# -*- coding: utf-8 -*-
"""提示词与 tool catalog schema 中的 function 名须与 tool_list_agent.json 注册名一致。"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, Iterable, List

ROOT = Path(__file__).resolve().parents[1]

# 提示词里常见但 catalog 不存在的「简称/错误名」
FORBIDDEN_STANDALONE = frozenset(
    {
        "grep",
        "glob",
        "write",
        "find",
        "delete",
        "read",
        "send",
        "todo",
    }
)

# 允许出现在 prose 的非 function 词（小写匹配时跳过）
ALLOW_SUBSTR = (
    "dry_run",
    "requires_reply",
    "Boss",
    "Execute",
    "Plan",
    "Auto",
    "Skills",
    "Skill",
    "peer",
    "thread_id",
    "py_compile",
    "git ",
    "JSON",
    "UTF-8",
    "Windows",
    "PowerShell",
    "CDN",
    "HTML",
    "markdown",
    "function",
    "schema",
    "MCP",
    "SOP",
    "todo_list",
    "glob_pattern",
    "glob_files",
    "grep_files",
    "read_file",
    "read_write",
    "write_file",
    "find_in_file",
    "find-replace",
    "delete_file",
    "action=read",
    "action=send",
    "action=delete",
    "action=list",
    "action=create",
    "action=check",
    "action=wait",
    "action=extract",
    "action=filter",
    "action=sort",
    "action=preview",
    "action=stats",
    "action=broadcast",
    "action=multisend",
    "session_send",
    "session_multisend",
    "session_broadcast",
    "skill_manage",
    "file_ops",
    "data_table",
    "archive",
)


def _load_catalog() -> dict:
    return json.loads((ROOT / "tools" / "tool_list_agent.json").read_text(encoding="utf-8"))


def _load_catalog_api_names() -> set[str]:
    data = _load_catalog()
    out: set[str] = set()
    for t in data.get("tools") or []:
        name = str(t.get("name") or "")
        if name.endswith(".py"):
            out.add(name[:-3])
    return out


def _iter_catalog_schema_strings(data: dict) -> Iterable[str]:
    """purpose / extended_description / args.description / examples 等会进入 function schema 的文本。"""
    for t in data.get("tools") or []:
        for key in ("purpose", "extended_description"):
            val = t.get(key)
            if isinstance(val, str) and val.strip():
                yield val
        for arg in t.get("args") or []:
            if isinstance(arg, dict):
                desc = arg.get("description")
                if isinstance(desc, str) and desc.strip():
                    yield desc
        for ex in t.get("examples") or []:
            if isinstance(ex, str) and ex.strip():
                yield ex
            elif isinstance(ex, dict):
                for key in ("title", "note", "description"):
                    val = ex.get(key)
                    if isinstance(val, str) and val.strip():
                        yield val
    hints = data.get("agent_hints") or {}
    for val in hints.values():
        if isinstance(val, str) and val.strip():
            yield val


def _catalog_schema_text_blob() -> str:
    return "\n".join(_iter_catalog_schema_strings(_load_catalog()))


def _shorthand_allowed_in_context(token: str, ctx: str) -> bool:
    low = ctx.lower()
    if any(s.lower() in low for s in ALLOW_SUBSTR):
        return True
    if token == "glob" and ("glob_pattern" in ctx or "glob_files" in ctx):
        return True
    if token == "grep" and "grep_files" in ctx:
        return True
    if token == "read" and ("read_file" in ctx or "read_write" in ctx or "action=read" in ctx):
        return True
    if token == "write" and ("write_file" in ctx or "read_write" in ctx):
        return True
    if token == "find" and ("find_in_file" in ctx or "find-replace" in ctx or "cli_find" in ctx):
        return True
    if token == "delete" and ("delete_file" in ctx or "action=delete" in ctx):
        return True
    if token == "send" and ("session_send" in ctx or "action=send" in ctx or "session_multisend" in ctx):
        return True
    if token == "todo" and "todo_list" in ctx:
        return True
    if token == "read" and "thread_id" in ctx:
        return True
    if token == "check" and (
        "ruff check" in ctx
        or "check-ignore" in ctx
        or "check_login" in ctx
        or "action=check" in ctx
    ):
        return True
    return False


def _iter_catalog_tool_text_fields(data: dict) -> Iterable[tuple[str, str, str]]:
    """(api_name, field_id, text) — 会进入 function schema 的文本。"""
    for t in data.get("tools") or []:
        fn = str(t.get("name") or "")
        if not fn.endswith(".py"):
            continue
        api = fn[:-3]
        for key in ("purpose", "extended_description"):
            val = t.get(key)
            if isinstance(val, str) and val.strip():
                yield api, key, val
        for arg in t.get("args") or []:
            if not isinstance(arg, dict):
                continue
            flag = str(arg.get("flag") or "")
            desc = arg.get("description")
            if isinstance(desc, str) and desc.strip():
                yield api, flag or "arg", desc
        for i, ex in enumerate(t.get("examples") or []):
            if isinstance(ex, str) and ex.strip():
                yield api, f"example[{i}]", ex
            elif isinstance(ex, dict):
                for key in ("title", "note", "description"):
                    val = ex.get(key)
                    if isinstance(val, str) and val.strip():
                        yield api, f"example[{i}].{key}", val


def _tool_has_action_param(tool: dict) -> bool:
    for arg in tool.get("args") or []:
        if isinstance(arg, dict) and arg.get("flag") == "--action":
            return True
    return False


def _action_param_description(tool: dict) -> str:
    for arg in tool.get("args") or []:
        if isinstance(arg, dict) and arg.get("flag") == "--action":
            return str(arg.get("description") or "")
    return ""


def _find_shorthand_issues_in_text(text: str) -> List[str]:
    issues: List[str] = []
    for bad in FORBIDDEN_STANDALONE:
        pat = rf"(?<![_/a-z]){re.escape(bad)}(?![_/a-z])"
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            ctx = text[max(0, m.start() - 32) : m.end() + 32]
            if _shorthand_allowed_in_context(bad, ctx):
                continue
            issues.append(f"{bad!r} @ …{ctx}…")
            break
    for pat, label in (
        (r"glob\s*过滤", "glob 过滤"),
        (r"文件名\s*glob\b", "文件名 glob"),
        (r"\b的\s*glob\b", "的 glob"),
        (r"\b层\s*glob\b", "层 glob"),
        (r"\bTodo\b", "Todo"),
    ):
        m = re.search(pat, text)
        if m:
            ctx = text[max(0, m.start() - 24) : m.end() + 24]
            issues.append(f"{label!r} @ …{ctx}…")
    return issues


def _find_shorthand_issues(blob: str) -> List[str]:
    return _find_shorthand_issues_in_text(blob)


def _prompt_text_blobs() -> str:
    from util import agent_prompt_constants as apc

    return "\n".join(
        [
            apc.AGENT_REGISTERED_FUNCTION_NAMES,
            apc.TOOL_AGENT_V2_SYSTEM_PROMPT,
            apc.AGENT_CODE_HINT_SYSTEM_PROMPT,
            apc.TOOL_AGENT_PLAN_MODE_PROMPT,
            apc.TOOL_AGENT_AUTO_MODE_PROMPT,
            apc.TOOL_AGENT_EXECUTE_MODE_PROMPT,
            apc.TOOL_AGENT_AUDIT_MODE_PROMPT,
            apc.TEAM_ROLE_DEFAULT,
            apc.ephemeral_requires_reply_priority_prompt("peer-test", "t1"),
        ]
    )


def _agent_hints_blob() -> str:
    data = _load_catalog()
    hints = data.get("agent_hints") or {}
    return "\n".join(str(v) for v in hints.values())


class TestPromptToolNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registered = _load_catalog_api_names()
        cls.prompt_blob = _prompt_text_blobs()
        cls.catalog_blob = _catalog_schema_text_blob()

    def test_no_forbidden_shorthand_tokens(self) -> None:
        """禁止独立出现的 grep/glob/read 等简称（须用 grep_files 等）。"""
        issues = _find_shorthand_issues(self.prompt_blob)
        self.assertEqual([], issues, f"提示词仍含未注册简称: {issues}")

    def test_catalog_schema_no_shorthand_tokens(self) -> None:
        """tool_list_agent.json 的 purpose/description 禁止口语简称，避免 schema 误导模型。"""
        issues = _find_shorthand_issues(self.catalog_blob)
        self.assertEqual([], issues, f"catalog schema 仍含未注册简称: {issues}")

    def test_each_tool_purpose_contains_api_name(self) -> None:
        """每个 tool 的 purpose 须显式出现 OpenAI function 名。"""
        data = _load_catalog()
        missing: List[str] = []
        for t in data.get("tools") or []:
            fn = str(t.get("name") or "")
            if not fn.endswith(".py"):
                continue
            api = fn[:-3]
            purpose = str(t.get("purpose") or "")
            if api not in purpose:
                missing.append(api)
        self.assertEqual([], missing, f"purpose 未含 function 名: {missing}")

    def test_action_tools_use_action_prefix_in_schema_text(self) -> None:
        """含 --action 的工具：purpose 与 --action 描述须用 action= 写法。"""
        data = _load_catalog()
        issues: List[str] = []
        for t in data.get("tools") or []:
            fn = str(t.get("name") or "")
            if not fn.endswith(".py") or not _tool_has_action_param(t):
                continue
            api = fn[:-3]
            purpose = str(t.get("purpose") or "")
            if "action=" not in purpose:
                issues.append(f"{api}.purpose 缺少 action=")
            action_desc = _action_param_description(t)
            if action_desc and "action=" not in action_desc:
                issues.append(f"{api}.--action 描述缺少 action=")
        self.assertEqual([], issues, issues)

    def test_catalog_fields_no_shorthand_per_tool(self) -> None:
        """逐字段扫描 catalog，便于定位具体 tool/参数。"""
        data = _load_catalog()
        issues: List[str] = []
        for api, field_id, text in _iter_catalog_tool_text_fields(data):
            found = _find_shorthand_issues_in_text(text)
            for item in found:
                issues.append(f"{api}/{field_id}: {item}")
        self.assertEqual([], issues, f"catalog 字段仍含简称: {issues[:8]}")

    def test_explicit_function_names_are_registered(self) -> None:
        """形如 xxx(action= 或 session_ 前缀的名称应在 catalog 中。"""
        candidates = set(re.findall(r"\b([a-z][a-z0-9_]{2,})\s*\(\s*action\s*=", self.prompt_blob))
        candidates |= set(re.findall(r"\b(session_[a-z_]+)\b", self.prompt_blob))
        known = {
            "todo_list",
            "skill_manage",
            "session_send",
            "session_multisend",
            "session_broadcast",
            "session_wait",
            "session_create",
            "session_list",
            "kling_generate",
            "dreamina_generate",
            "archive",
            "run_type",
            "file_ops",
            "data_table",
            "github_api",
        }
        for name in candidates:
            if name in known and name not in self.registered:
                self.fail(f"已知 function {name} 不在 catalog")
            if name.startswith("session_") and name not in self.registered:
                self.fail(f"session function {name} 不在 catalog")

    def test_agent_hints_no_slash_shorthand(self) -> None:
        """agent_hints 禁止 glob/grep/read 等未带 _files/_file 的斜杠简称。"""
        blob = _agent_hints_blob()
        for pat in (r"\bglob/grep\b", r"\bread/grep\b", r"\bglob/read\b"):
            self.assertIsNone(re.search(pat, blob), f"agent_hints 含简称: {pat}")
        from util.agent_prompt_constants import list_registered_api_names

        names = list_registered_api_names()
        self.assertIn("grep_files", names)
        self.assertIn("read_file", names)
        self.assertNotIn("grep", names)


if __name__ == "__main__":
    unittest.main()
