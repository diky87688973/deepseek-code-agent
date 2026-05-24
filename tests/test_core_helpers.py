# -*- coding: utf-8 -*-
"""核心辅助逻辑最小回归测试。"""
from __future__ import annotations

import unittest

from agent_v2.agent_core import _find_pending_requires_reply_peer_message
from agent_v2.live_state import reset_peer_turn_chain, try_acquire_peer_turn_slot
from tools.agent_common import utf8_preview
from util.agent_model_dispatch import model_max_context_tokens


class TestUtf8Preview(unittest.TestCase):
    def test_no_split_multibyte(self) -> None:
        text = "中文测试" * 30
        preview = utf8_preview(text, 20)
        self.assertTrue(len(preview.encode("utf-8")) <= 20)
        preview.encode("utf-8")


class TestRequiresReplyPending(unittest.TestCase):
    def test_requires_agent_peer_flag(self) -> None:
        msgs = [
            {
                "role": "user",
                "content": "hi",
                "_requires_reply": True,
                "_sender": "peer-1",
            }
        ]
        self.assertIsNone(_find_pending_requires_reply_peer_message(msgs))
        msgs[0]["_agent_peer_message"] = True
        self.assertIsNotNone(_find_pending_requires_reply_peer_message(msgs))


class TestPeerTurnLimit(unittest.TestCase):
    def test_consecutive_limit(self) -> None:
        cid = "test-peer-limit-cid"
        reset_peer_turn_chain(cid)
        ok1, _ = try_acquire_peer_turn_slot(cid, max_consecutive=2, min_interval_sec=0)
        ok2, _ = try_acquire_peer_turn_slot(cid, max_consecutive=2, min_interval_sec=0)
        ok3, reason = try_acquire_peer_turn_slot(cid, max_consecutive=2, min_interval_sec=0)
        self.assertTrue(ok1 and ok2)
        self.assertFalse(ok3)
        self.assertIn("上限", reason)
        reset_peer_turn_chain(cid)


class TestModelContextTokens(unittest.TestCase):
    def test_known_model(self) -> None:
        cap = model_max_context_tokens("deepseek-v4-flash")
        self.assertGreater(cap, 0)


if __name__ == "__main__":
    unittest.main()
