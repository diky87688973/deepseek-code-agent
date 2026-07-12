# -*- coding: utf-8 -*-
"""catalog 参数策略：禁止同义别名、只读工具精简 schema。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestCatalogParamPolicy(unittest.TestCase):
    def test_readonly_tools_no_restrict_in_openai_schema(self) -> None:
        from agent_v4 import agent_core

        cat = json.loads((ROOT / "tools" / "tool_list_agent.json").read_text(encoding="utf-8"))
        tools, _ = agent_core.catalog_to_openai_tools(cat)
        ro = {"read_file", "grep_files", "glob_files", "data_table", "image_ocr"}
        for t in tools:
            name = t["function"]["name"]
            if name not in ro:
                continue
            props = (t["function"].get("parameters") or {}).get("properties") or {}
            self.assertNotIn("restrict_to_workspace", props, name)
            self.assertNotIn("run_type", props, name)

    def test_data_table_rejects_source_alias(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "tools"))
        import data_table

        r = data_table.agent_main(action="preview", source="x.csv", path="")
        self.assertFalse(r.get("ok"))
        self.assertIn("source", str((r.get("error") or {}).get("message", "")))

    def test_glob_files_rejects_pattern_alias(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "tools"))
        import glob_files

        r = glob_files.agent_main(path=".", pattern="*.py")
        self.assertFalse(r.get("ok"))
        self.assertIn("pattern", str((r.get("error") or {}).get("message", "")))


if __name__ == "__main__":
    unittest.main()
