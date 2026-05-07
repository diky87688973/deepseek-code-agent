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


def test_parse_unified_diff_single_hunk():
    mod = _load_module("cli_patch_apply.py")
    patch = (
        "--- a.txt\n"
        "+++ a.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_changed\n"
    )
    files = mod._parse_unified_diff(patch)
    assert len(files) == 1
    assert files[0]["old_path"] == "a.txt"
    assert files[0]["new_path"] == "a.txt"
    assert files[0]["hunks"][0]["lines"][1] == ("-", "line2")


def test_match_and_find_hunk_position():
    mod = _load_module("cli_patch_apply.py")
    lines = ["a", "b", "c"]
    hunk_lines = [(" ", "b"), ("-", "c"), ("+", "cc")]
    assert mod._match_hunk_at(lines, 1, hunk_lines) is True
    assert mod._find_hunk_position(lines, hunk_lines, 0) == 1


def test_apply_hunk_replaces_expected_region():
    mod = _load_module("cli_patch_apply.py")
    lines = ["a", "b", "c"]
    hunk_lines = [(" ", "b"), ("-", "c"), ("+", "cc")]
    out = mod._apply_hunk(lines, hunk_lines, 1)
    assert out == ["a", "b", "cc"]
