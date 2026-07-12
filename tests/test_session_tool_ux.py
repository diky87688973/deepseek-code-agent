# -*- coding: utf-8 -*-
"""协作工具 UX：session_wait 哨兵/thread 匹配与 suspend 门控。"""
from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import session_wait  # noqa: E402

from agent_v4.live_state import should_suspend_after_session_wait


class TestSessionWaitHostSuspendGate(unittest.TestCase):
    def test_suspend_false_not_host_suspend(self) -> None:
        result = {
            "ok": True,
            "data": {"pending": ["peer-1"], "suspend": False, "should_stop_turn": True},
        }
        self.assertFalse(should_suspend_after_session_wait(result))

    def test_suspend_true_host_suspend(self) -> None:
        result = {"ok": True, "data": {"pending": ["peer-1"], "suspend": True}}
        self.assertTrue(should_suspend_after_session_wait(result))

    def test_all_done_not_host_suspend(self) -> None:
        result = {"ok": True, "data": {"pending": [], "suspend": True}}
        self.assertFalse(should_suspend_after_session_wait(result))


class TestSessionWaitThreadMatch(unittest.TestCase):
    def test_sentinel_match_empty_thread_on_send(self) -> None:
        self.assertTrue(session_wait._sentinel_matches_wait_thread("", "round-1"))

    def test_sentinel_match_same_thread(self) -> None:
        self.assertTrue(session_wait._sentinel_matches_wait_thread("round-1", "round-1"))

    def test_sentinel_mismatch(self) -> None:
        self.assertFalse(session_wait._sentinel_matches_wait_thread("a", "b"))

    def test_wait_without_request_lists_sentinels(self) -> None:
        msgs: List[Dict[str, Any]] = [
            {
                "role": "system",
                "_requires_reply_sentinel": True,
                "_target_id": "peer-1",
                "_thread_id": "t1",
            },
        ]
        with unittest.mock.patch("agent_v4.live_state.CONVERSATIONS", {"me": msgs}):
            with unittest.mock.patch(
                "agent_v4.agent_core._ensure_conversation_loaded",
                lambda _cid: None,
            ):
                r = session_wait.agent_main(
                    target_ids=["peer-1"],
                    thread_id="wrong-thread",
                    conversation_id="me",
                )
        self.assertFalse(r.get("ok"))
        msg = str((r.get("error") or {}).get("message", ""))
        self.assertIn("peer-1@thread=t1", msg)


class TestSessionWaitSuspend(unittest.TestCase):
    def _pending_msgs(self) -> List[Dict[str, Any]]:
        return [
            {
                "role": "system",
                "_requires_reply_sentinel": True,
                "_target_id": "peer-1",
                "_thread_id": "t1",
            },
        ]

    def test_suspend_false_does_not_call_suspend_agent_wait(self) -> None:
        msgs = self._pending_msgs()
        with unittest.mock.patch("agent_v4.live_state.CONVERSATIONS", {"me": msgs}):
            with unittest.mock.patch(
                "agent_v4.agent_core._ensure_conversation_loaded",
                lambda _cid: None,
            ):
                with unittest.mock.patch(
                    "agent_v4.live_state.suspend_agent_wait",
                ) as mock_suspend:
                    r = session_wait.agent_main(
                        target_ids=["peer-1"],
                        thread_id="t1",
                        suspend=False,
                        conversation_id="me",
                    )
        self.assertTrue(r.get("ok"))
        data = r.get("data") or {}
        self.assertTrue(data.get("pending"))
        self.assertIs(data.get("suspend"), False)
        mock_suspend.assert_not_called()

    def test_default_suspend_calls_suspend_agent_wait(self) -> None:
        msgs = self._pending_msgs()
        with unittest.mock.patch("agent_v4.live_state.CONVERSATIONS", {"me": msgs}):
            with unittest.mock.patch(
                "agent_v4.agent_core._ensure_conversation_loaded",
                lambda _cid: None,
            ):
                with unittest.mock.patch(
                    "agent_v4.live_state.suspend_agent_wait",
                    return_value={"wait_id": "w1"},
                ) as mock_suspend:
                    r = session_wait.agent_main(
                        target_ids=["peer-1"],
                        thread_id="t1",
                        conversation_id="me",
                    )
        self.assertTrue(r.get("ok"))
        data = r.get("data") or {}
        self.assertIs(data.get("suspend"), True)
        mock_suspend.assert_called_once()


if __name__ == "__main__":
    unittest.main()
