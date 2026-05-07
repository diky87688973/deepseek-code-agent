from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module(file_name: str):
    base = Path(__file__).resolve().parents[1]
    mod_path = base / file_name
    spec = importlib.util.spec_from_file_location(file_name, mod_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_merge_intervals_handles_overlap_and_touch():
    mod = _load_module("cli_structured_edit.py")
    merged = mod._merge_intervals([(1, 3), (2, 5), (5, 8), (10, 11)])
    assert merged == [(1, 8), (10, 11)]





def test_replace_markers_replaces_between_anchors(tmp_path):
    mod = _load_module("cli_structured_edit.py")
    p = tmp_path / "t.txt"
    p.write_text("AAA<!--X-->OLD<!--Y-->BBB", encoding="utf-8")
    fb, fa = mod._replace_markers(
        p,
        start_marker="<!--X-->",
        end_marker="<!--Y-->",
        replacement="NEW",
        encoding="utf-8",
        search_from=0,
    )
    assert "OLD" not in fa
    assert fa == "AAANEW<!--Y-->BBB"
    assert "OLD" in fb

def test_apply_delete_segments_removes_merged_ranges():
    mod = _load_module("cli_structured_edit.py")
    text = "abcdefghij"
    result = mod._apply_delete_segments(text, [(1, 3), (2, 5), (7, 9)])
    assert result == "afgj"
