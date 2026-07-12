# -*- coding: utf-8 -*-
"""摩擦修复回归：replace_in_file.raw、session_wait.sender_cid、python_inline 禁搜精确化。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import session_wait  # noqa: E402
import agent_common as ac  # noqa: E402

from tools import python_inline, read_file, replace_in_file, replace_undo


class TestReplaceInFileRaw(unittest.TestCase):
    def test_raw_turns_real_newline_into_literal_backslash_n(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "config.py"
            fp.write_text('MSG = "ok"\n', encoding="utf-8")
            r = replace_in_file.agent_main(
                path=str(fp),
                old_text='MSG = "ok"',
                new_text='MSG = "line1\nline2"',
                raw=True,
                dry_run=False,
            )
            self.assertTrue(r.get("ok"), r)
            body = fp.read_text(encoding="utf-8")
            self.assertIn('MSG = "line1\\nline2"', body)
            self.assertNotIn("line1\nline2", body)

    def test_linerange_reports_missing_newline_warning(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "app.py"
            fp.write_text("alpha\nbeta\n", encoding="utf-8")
            r = replace_in_file.agent_main(
                path=str(fp),
                line_start=1,
                line_end=1,
                new_text="alpha2",
                dry_run=True,
            )
            # 产品行为（与备份一致）：中间行替换且 new_text 无末尾换行 → 硬错误，防黏连
            self.assertFalse(r.get("ok"), r)
            err = (r.get("error") or {}).get("message") or ""
            self.assertIn("末尾缺少换行符", err)

    def test_backup_uses_versioned_store_without_bak_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ac.configure_replace_backup_root(root / "replace_backup")
            fp = root / "app.py"
            fp.write_text("alpha\n", encoding="utf-8")
            r = replace_in_file.agent_main(
                path=str(fp),
                old_text="alpha",
                new_text="beta",
                dry_run=False,
                backup=True,
            )
            self.assertTrue(r.get("ok"), r)
            data = r.get("data") or {}
            self.assertTrue(data.get("mod_id"), data)
            self.assertFalse(fp.with_suffix(fp.suffix + ".bak").exists())
            self.assertTrue((root / "replace_backup" / data["mod_id"] / "original").is_file())


class TestReadFileRaw(unittest.TestCase):
    def test_raw_returns_content_lines_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "quoted.txt"
            fp.write_text('result:"ok"\n', encoding="utf-8")
            r = read_file.agent_main(path=str(fp), raw=True)
            self.assertTrue(r.get("ok"), r)
            data = r.get("data") or {}
            self.assertEqual(['result:"ok"'], data.get("content_lines"))
            self.assertTrue(data.get("content_hash"))


class TestReplaceUndoSafety(unittest.TestCase):
    def test_replace_undo_is_registered_as_write_tool(self) -> None:
        from agent_v4.core.shared_state import WRITE_TOOL_SCRIPTS

        self.assertIn("replace_undo.py", WRITE_TOOL_SCRIPTS)

    def test_undo_rejects_plan_mode(self) -> None:
        r = replace_undo.agent_main(action="undo", mod_id="missing", run_type="plan")
        self.assertFalse(r.get("ok"))
        self.assertEqual("ModeConflict", (r.get("error") or {}).get("type"))


class TestSessionWaitSenderCid(unittest.TestCase):
    def _sentinel_msgs(self) -> List[Dict[str, Any]]:
        return [
            {
                "role": "system",
                "_requires_reply_sentinel": True,
                "_target_id": "peer-1",
                "_thread_id": "t1",
            },
        ]

    def test_sender_cid_reads_sentinel_from_other_conversation(self) -> None:
        msgs_a = self._sentinel_msgs()
        with unittest.mock.patch(
            "agent_v4.live_state.CONVERSATIONS",
            {"session-a": msgs_a, "session-b": []},
        ):
            with unittest.mock.patch(
                "agent_v4.agent_core._ensure_conversation_loaded",
                lambda _cid: None,
            ):
                r = session_wait.agent_main(
                    target_ids=["peer-1"],
                    thread_id="t1",
                    conversation_id="session-b",
                    sender_cid="session-a",
                    suspend=False,
                )
        self.assertTrue(r.get("ok"), r)
        data = r.get("data") or {}
        self.assertIn("peer-1", data.get("pending", []))

    def test_without_sender_cid_wrong_session_gets_wait_without_request(self) -> None:
        msgs_a = self._sentinel_msgs()
        with unittest.mock.patch(
            "agent_v4.live_state.CONVERSATIONS",
            {"session-a": msgs_a, "session-b": []},
        ):
            with unittest.mock.patch(
                "agent_v4.agent_core._ensure_conversation_loaded",
                lambda _cid: None,
            ):
                r = session_wait.agent_main(
                    target_ids=["peer-1"],
                    thread_id="t1",
                    conversation_id="session-b",
                    suspend=False,
                )
        self.assertFalse(r.get("ok"))
        msg = str((r.get("error") or {}).get("message", ""))
        self.assertIn("sender_cid", msg)

    def test_missing_conversation_mentions_sender_cid(self) -> None:
        r = session_wait.agent_main(target_ids=["peer-1"])
        self.assertFalse(r.get("ok"))
        msg = str((r.get("error") or {}).get("message", ""))
        self.assertIn("sender_cid", msg)


class TestPythonInlineForbidSearch(unittest.TestCase):
    def test_string_literal_without_call_not_forbidden(self) -> None:
        """子串 grep_files 无括号不再误杀；仅拦截 grep_files( 形态。"""
        self.assertFalse(python_inline._forbid_inline_search('hint = "see grep_files in docs"'))

    def test_actual_grep_files_call_forbidden(self) -> None:
        self.assertTrue(python_inline._forbid_inline_search('grep_files("x", pattern="y")'))

    def test_comment_without_call_not_forbidden(self) -> None:
        self.assertFalse(python_inline._forbid_inline_search("# use grep_files tool instead"))

    def test_base64_decode_call_forbidden(self) -> None:
        self.assertTrue(
            python_inline._forbid_inline_search("import base64\nbase64.b64decode(b'x')")
        )


class TestCatalogFrictionExamples(unittest.TestCase):
    def test_replace_in_file_second_example_is_raw(self) -> None:
        cat = json.loads((_ROOT / "tools" / "tool_list_agent.json").read_text(encoding="utf-8"))
        tool = next(t for t in cat["tools"] if t["name"] == "replace_in_file.py")
        ex = tool.get("examples") or []
        self.assertGreaterEqual(len(ex), 2)
        self.assertTrue(ex[1].get("args", {}).get("raw"))

    def test_read_file_exposes_raw_flag(self) -> None:
        cat = json.loads((_ROOT / "tools" / "tool_list_agent.json").read_text(encoding="utf-8"))
        tool = next(t for t in cat["tools"] if t["name"] == "read_file.py")
        flags = {a.get("flag") for a in tool.get("args") or []}
        self.assertIn("--raw", flags)

    def test_session_wait_has_sender_cid_example(self) -> None:
        cat = json.loads((_ROOT / "tools" / "tool_list_agent.json").read_text(encoding="utf-8"))
        hints = str((cat.get("agent_hints") or {}).get("session_collab") or "")
        self.assertIn("sender_cid", hints)
        # session_wait 为宿主协作工具，可不进 catalog；以 agent_main 签名为准
        import inspect
        import session_wait as sw

        params = inspect.signature(sw.agent_main).parameters
        self.assertIn("sender_cid", params)


if __name__ == "__main__":
    unittest.main()
