# -*- coding: utf-8 -*-
"""回归修复点：工具 bug 与标题占位符逻辑。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_v3.agent_core import (
    _is_placeholder_conversation_title,
    _merge_stream_tool_calls_with_snapshot,
)
import agent_patch_engine as pe
from tools import archive, github_api, replace_in_file, web_fetch, web_fetch_render


class TestPlaceholderTitle(unittest.TestCase):
    def test_placeholders(self) -> None:
        self.assertTrue(_is_placeholder_conversation_title(""))
        self.assertTrue(_is_placeholder_conversation_title("新会话"))
        self.assertTrue(_is_placeholder_conversation_title("生成标题中…"))
        self.assertTrue(_is_placeholder_conversation_title("会话 abcd1234"))

    def test_real_title(self) -> None:
        self.assertFalse(_is_placeholder_conversation_title("回归测试 Layer0"))


class TestReplaceInFileLinerange(unittest.TestCase):
    def test_line_range_replace_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "sample.py"
            fp.write_text("line1\nline2\nline3\n", encoding="utf-8")
            r = replace_in_file.agent_main(
                path=str(fp),
                line_start=2,
                line_end=2,
                new_text="REPLACED\n",
                dry_run=True,
            )
            self.assertTrue(r.get("ok"), r)
            self.assertEqual(r["data"]["replace_mode"], "linerange")
            self.assertTrue(r["data"]["changed"])
            self.assertIn("REPLACED", r["data"]["diff_text"])
            self.assertEqual(fp.read_text(encoding="utf-8"), "line1\nline2\nline3\n")


class TestWebFetchRenderCoercion(unittest.TestCase):
    def test_string_numeric_args_coerced(self) -> None:
        from unittest.mock import patch

        with patch.object(
            web_fetch_render, "_fetch_with_playwright", return_value=web_fetch_render.ac.ok({})
        ) as m:
            r = web_fetch_render.agent_main(
                url="https://httpbin.org/html",
                wait_sec="3",
                max_chars="1000",
            )
            self.assertTrue(r.get("ok"), r)
            m.assert_called_once_with("https://httpbin.org/html", 3.0, 1000)


class TestWebFetchIdna(unittest.TestCase):
    def test_ascii_safe_url_idna(self) -> None:
        out = web_fetch._ascii_safe_url("http://例子.测试/")
        self.assertIn("xn--", out)
        self.assertTrue(out.startswith("http://"))

    def test_invalid_chinese_domain_no_latin1_crash(self) -> None:
        r = web_fetch.agent_main(url="http://例子.测试/", timeout_sec=3, max_chars=100)
        err = r.get("error")
        self.assertIsInstance(err, dict)
        msg = str((err or {}).get("message") or "")
        self.assertNotIn("latin-1", msg.lower())


class TestToolCallsSnapshotFallback(unittest.TestCase):
    def test_empty_stream_uses_message_tool_calls(self) -> None:
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'},
                }
            ],
        }
        tcalls = _merge_stream_tool_calls_with_snapshot([], msg)
        self.assertEqual(len(tcalls), 1)
        self.assertEqual(tcalls[0]["function"]["name"], "read_file")

    def test_stream_wins_over_snapshot(self) -> None:
        chunks = [
            {
                "index": 0,
                "id": "call_x",
                "function": {"name": "glob_", "arguments": ""},
            },
            {"index": 0, "function": {"name": "files", "arguments": '{"path":"."}'}},
        ]
        msg = {
            "tool_calls": [
                {"id": "y", "function": {"name": "other", "arguments": "{}"}},
            ]
        }
        tcalls = _merge_stream_tool_calls_with_snapshot(chunks, msg)
        self.assertEqual(len(tcalls), 1)
        self.assertEqual(tcalls[0]["function"]["name"], "glob_files")


class TestMaxToolRoundsHint(unittest.TestCase):
    def test_wrap_ephemeral_user_hint(self) -> None:
        from agent_v3.agent_core import _max_tool_rounds_user_hint
        from agent_v3.bootstrap import MAX_TOOL_ROUNDS

        h = _max_tool_rounds_user_hint()
        self.assertTrue(h.strip())
        self.assertIn("继续", h)
        self.assertIn("拟人", h)
        _ = MAX_TOOL_ROUNDS

    def test_tool_budget_in_error_envelope(self) -> None:
        from util.agent_tool_budget import tool_call_limit_reached_result
        from agent_v3.bootstrap import MAX_TOOL_ROUNDS

        env = tool_call_limit_reached_result(used=MAX_TOOL_ROUNDS, limit=MAX_TOOL_ROUNDS)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["type"], "ToolCallLimitReached")
        self.assertIn("tool_calls_remaining", env["data"])
        self.assertEqual(env["data"]["tool_calls_remaining"], 0)
        self.assertIn("已达上限", env["error"]["message"])

class TestApplyPatchWindowsPath(unittest.TestCase):
    def test_windows_absolute_ab_prefix_not_rename(self) -> None:
        patch = (
            "--- a/E:/AgentTest/src/x.py\n"
            "+++ b/E:/AgentTest/src/x.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-line2\n"
            "+LINE2\n"
            " line3\n"
        )
        files = pe.parse_unified_diff(patch)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["old_path"], files[0]["new_path"])
        self.assertTrue(files[0]["new_path"].replace("\\", "/").endswith("AgentTest/src/x.py"))


class TestArchiveDryRun(unittest.TestCase):
    def test_agent_main_accepts_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            z = Path(td) / "t.zip"
            import zipfile

            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("a.txt", "hi")
            r = archive.agent_main(action="list", source=str(z), dry_run=True)
            self.assertTrue(r.get("ok"), r)
            r2 = archive.agent_main(
                action="extract",
                source=str(z),
                dest=str(Path(td) / "out"),
                dry_run=True,
            )
            self.assertTrue(r2.get("ok"), r2)
            self.assertTrue(r2["data"].get("dry_run"))
            self.assertFalse((Path(td) / "out").exists())


class TestFileSearchHostGate(unittest.TestCase):
    """file_search 仅 _RESTRICTED_TOOLS；须会话门控 + conversation_id 一致。"""

    def test_blocked_without_gate(self) -> None:
        from agent_v3.agent_core import execute_tool_script

        r = execute_tool_script(
            "file_search.py",
            {"pattern": "x", "path": "."},
            conversation_id="gate-test-cid",
        )
        self.assertFalse(r["ok"])
        self.assertEqual((r.get("error") or {}).get("type"), "Restricted")

    def test_allowed_when_gate_matches_cid(self) -> None:
        from agent_v3.agent_core import execute_tool_script
        from agent_v3.live_state import set_file_search_allowed

        cid = "gate-test-cid-open"
        vf = Path(__file__).resolve().parents[1] / "agent_v3" / "version.py"
        set_file_search_allowed(cid, True)
        try:
            r = execute_tool_script(
                "file_search.py",
                {"pattern": "AGENT_APP_VERSION", "path": str(vf), "recursive": True},
                conversation_id=cid,
            )
        finally:
            set_file_search_allowed(cid, False)
        self.assertNotEqual((r.get("error") or {}).get("type"), "Restricted")
        self.assertTrue(r.get("ok"), r.get("error"))

    def test_gate_cid_mismatch_still_restricted(self) -> None:
        from agent_v3.agent_core import execute_tool_script
        from agent_v3.live_state import set_file_search_allowed

        set_file_search_allowed("cid-a", True)
        try:
            r = execute_tool_script(
                "file_search.py",
                {"pattern": "x", "path": "."},
                conversation_id="cid-b",
            )
        finally:
            set_file_search_allowed("cid-a", False)
        self.assertEqual((r.get("error") or {}).get("type"), "Restricted")


class TestGithubApiEnvelope(unittest.TestCase):
    def test_missing_action_error_shape(self) -> None:
        r = github_api.agent_main(action="")
        self.assertFalse(r["ok"])
        err = r.get("error")
        self.assertIsInstance(err, dict)
        self.assertIn("type", err)
        self.assertIn("message", err)
        self.assertIsInstance(err["message"], str)

    def test_missing_repo_error_shape(self) -> None:
        r = github_api.agent_main(action="get_repo", repo="")
        self.assertFalse(r["ok"])
        err = r.get("error")
        self.assertIsInstance(err, dict)
        self.assertEqual(err.get("type"), "ValueError")


if __name__ == "__main__":
    unittest.main()
