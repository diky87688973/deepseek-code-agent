# -*- coding: utf-8 -*-
import unittest

from agent_v4.core.tool_sets import (
    PREVIEW_REQUIRED_SCRIPTS,
    QUALITY_WRITE_PATH_SCRIPTS,
    WRITE_GATED_SCRIPTS,
    WRITE_TOOL_SCRIPTS,
    assert_tool_sets_consistent,
)


class TestToolSets(unittest.TestCase):
    def test_consistent(self):
        assert_tool_sets_consistent()
        self.assertEqual(WRITE_TOOL_SCRIPTS, WRITE_GATED_SCRIPTS)
        self.assertTrue(PREVIEW_REQUIRED_SCRIPTS <= WRITE_GATED_SCRIPTS)
        self.assertEqual(QUALITY_WRITE_PATH_SCRIPTS, PREVIEW_REQUIRED_SCRIPTS)

    def test_shared_state_reexports(self):
        from agent_v4.core import shared_state as ss
        from agent_v4.runtime import host_policy as hp
        from agent_v4.core import host_quality as hq

        self.assertEqual(ss.WRITE_TOOL_SCRIPTS, WRITE_GATED_SCRIPTS)
        self.assertEqual(hp._PREVIEW_PATH_SCRIPTS, PREVIEW_REQUIRED_SCRIPTS)
        self.assertEqual(hq._WRITE_PATH_SCRIPTS, QUALITY_WRITE_PATH_SCRIPTS)


if __name__ == "__main__":
    unittest.main()
