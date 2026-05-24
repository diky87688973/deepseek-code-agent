# -*- coding: utf-8 -*-
import unittest

from util.agent_tool_budget import (
    apply_turn_tool_budget_to_result,
    attach_tool_call_budget,
    tool_call_budget_fields,
    tool_call_limit_reached_result,
    turn_tool_budget_exhausted,
)


class TestAgentToolBudget(unittest.TestCase):
    def test_budget_fields_remaining(self) -> None:
        f = tool_call_budget_fields(used=5, limit=30)
        self.assertEqual(f["tool_calls_limit"], 30)
        self.assertEqual(f["tool_calls_used"], 5)
        self.assertEqual(f["tool_calls_remaining"], 25)

    def test_limit_reached_envelope(self) -> None:
        env = tool_call_limit_reached_result(used=30, limit=30)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["type"], "ToolCallLimitReached")
        self.assertEqual(env["data"]["tool_calls_remaining"], 0)

    def test_apply_increments_used(self) -> None:
        base = {"ok": True, "data": {"x": 1}, "error": None}
        out, used = apply_turn_tool_budget_to_result(
            base,
            turn_tool_invocations_used=2,
            limit=30,
            limit_blocked=False,
        )
        self.assertEqual(used, 3)
        self.assertEqual(out["data"]["tool_calls_used"], 3)
        self.assertEqual(out["data"]["tool_calls_remaining"], 27)
        self.assertEqual(out["data"]["x"], 1)

    def test_apply_limit_blocked_no_increment(self) -> None:
        env = tool_call_limit_reached_result(used=30, limit=30)
        out, used = apply_turn_tool_budget_to_result(
            env,
            turn_tool_invocations_used=30,
            limit=30,
            limit_blocked=True,
        )
        self.assertEqual(used, 30)
        self.assertFalse(out["ok"])

    def test_exhausted(self) -> None:
        self.assertFalse(turn_tool_budget_exhausted(29, 30))
        self.assertTrue(turn_tool_budget_exhausted(30, 30))

    def test_attach_preserves_ok_false(self) -> None:
        env = attach_tool_call_budget(
            {"ok": False, "data": None, "error": {"type": "X", "message": "m"}},
            used=10,
            limit=30,
        )
        self.assertEqual(env["data"]["tool_calls_remaining"], 20)


if __name__ == "__main__":
    unittest.main()
