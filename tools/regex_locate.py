# -*- coding: utf-8 -*-
"""在单文件或目录下用正则检索，每条命中返回 region_start/region_end（与 replace_in_file 一致）及行列。"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import List, Optional

import agent_common as ac


def agent_main(
    *,
    path: str,
    pattern: str,
    ignore_case: bool = False,
    dotall: bool = False,
    recursive: bool = True,
    glob_pattern: str = "",
    encoding: str = "utf-8",
    limit: int = 200,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    _progress_dict: Optional[dict] = None,
) -> dict:
    """
    对目标文件或目录下各文件全文做 regex.finditer，每条命中返回：
    - region_start / region_end：0-based 半开，可直接传入 replace_in_file；
    - line / column：起点 1-based；
    - end_line / end_column：终点开区间（与 replace 行列模式一致）；
    - match：匹配的子串。

    正则标志：始终含 re.MULTILINE（^/$ 按行）；dotall=true 时另加 re.DOTALL（. 匹配换行）。
    """
    _ = run_type
    try:
        if limit <= 0:
            raise ValueError("limit 必须 > 0")

        root = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        if not root.exists():
            raise FileNotFoundError(f"路径不存在: {root}")

        flags = re.MULTILINE
        if ignore_case:
            flags |= re.IGNORECASE
        if dotall:
            flags |= re.DOTALL
        try:
            rx = re.compile(pattern, flags)
        except re.error as ex:
            raise ValueError(f"正则无效: {ex}") from ex

        files = ac.collect_source_files(root, glob_pattern, recursive=recursive)
        items: List[dict] = []
        scanned = 0
        _last_prog = 0.0
        if _progress_dict is not None:
            _progress_dict.update({"scanned": 0, "current_file": "", "phase": "regex"})

        for fp in files:
            if ac.progress_abort_requested(_progress_dict):
                return {"ok": False, "data": None, "error": {"type": "Aborted", "message": "用户已停止搜索"}}
            if len(items) >= limit:
                break
            scanned += 1
            if _progress_dict is not None:
                now = time.time()
                if scanned == 1 or scanned % 50 == 0 or now - _last_prog >= 1.0:
                    _progress_dict.update({"scanned": scanned, "current_file": fp.name, "phase": "regex"})
                    _last_prog = now
            try:
                text = ac.read_file_text(fp, encoding)
            except OSError:
                continue
            _fi = 0
            for m in rx.finditer(text):
                _fi += 1
                if _fi % 200 == 0 and ac.progress_abort_requested(_progress_dict):
                    return {"ok": False, "data": None, "error": {"type": "Aborted", "message": "用户已停止搜索"}}
                if len(items) >= limit:
                    break
                s, e = m.span()
                sl, sc, el, ec = ac.span_region_rowcols(text, s, e)
                mt = m.group(0)
                items.append(
                    {
                        "file": str(fp),
                        "region_start": s,
                        "region_end": e,
                        "line": sl,
                        "column": sc,
                        "end_line": el,
                        "end_column": ec,
                        "match": mt,
                    }
                )

        return ac.ok(
            {
                "count": len(items),
                "items": items,
                "truncated": len(items) >= limit,
                "hint": "单文件改写给 replace_in_file 时复制对应条目的 region_start、region_end；与 find_in_file 语义一致。",
            }
        )
    except Exception as e:
        return ac.err(e)




