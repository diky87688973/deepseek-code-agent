# -*- coding: utf-8 -*-
"""生成两段文本的 unified diff（只读，不落盘）。支持文件/内联文本两两组合。"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Optional

import agent_common as ac
import stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()



def read_text_auto(path: Path, encoding: str) -> str:
    if encoding != "auto":
        return path.read_text(encoding=encoding, errors="replace")
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _side_from_file_or_text(
    file_arg: Optional[str],
    text_arg: Optional[str],
    *,
    encoding: str,
    restrict_to_workspace: bool,
) -> str:
    has_file = file_arg is not None and str(file_arg).strip() != ""
    has_text = text_arg is not None
    if int(has_file) + int(has_text) != 1:
        raise ValueError("每一侧必须且只能提供一个：文件路径或文本")
    if has_file:
        fp = ac.resolve_path(str(file_arg).strip(), allow_outside_workspace=not restrict_to_workspace)
        if not fp.is_file():
            raise FileNotFoundError(f"不是文件或不存在: {fp}")
        return read_text_auto(fp, encoding)
    assert text_arg is not None
    return text_arg


def _label_for_side(file_arg: Optional[str], kind: str) -> str:
    if file_arg and str(file_arg).strip():
        try:
            return Path(str(file_arg).strip()).name
        except ValueError:
            return str(file_arg)
    return kind




def agent_main(
    *,
    left_file: Optional[str] = None,
    left_text: Optional[str] = None,
    right_file: Optional[str] = None,
    right_text: Optional[str] = None,
    encoding: str = "utf-8",
    context: int = 3,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    """返回 data：summary、diff（行列表）、diff_markdown。"""
    _ = run_type
    try:
        if context < 0:
            raise ValueError("context 必须 >= 0")

        left = _side_from_file_or_text(
            left_file, left_text, encoding=encoding, restrict_to_workspace=restrict_to_workspace
        )
        right = _side_from_file_or_text(
            right_file, right_text, encoding=encoding, restrict_to_workspace=restrict_to_workspace
        )

        left_lines = left.splitlines()
        right_lines = right.splitlines()
        from_label = _label_for_side(left_file, "left_text")
        to_label = _label_for_side(right_file, "right_text")
        diff_lines = list(
            difflib.unified_diff(
                left_lines,
                right_lines,
                fromfile=from_label,
                tofile=to_label,
                lineterm="",
                n=context,
            )
        )
        add_cnt = sum(1 for x in diff_lines if x.startswith("+") and not x.startswith("+++"))
        del_cnt = sum(1 for x in diff_lines if x.startswith("-") and not x.startswith("---"))
        summary = {
            "same": left == right,
            "left_chars": len(left),
            "right_chars": len(right),
            "added_lines": add_cnt,
            "deleted_lines": del_cnt,
        }
        diff_body = "\n".join(diff_lines)
        diff_md = "```diff\n" + diff_body + "\n```" if diff_lines else "```diff\n```"
        return ac.ok(
            {
                "summary": summary,
                "diff": diff_lines,
                "diff_markdown": diff_md,
            }
        )
    except Exception as e:
        return ac.err(e)




