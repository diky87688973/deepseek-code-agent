# -*- coding: utf-8 -*-
"""在单个文件内定位字面或正则，返回与 read_file/replace_in_file 一致的字符半开区间 [region_start, region_end)。"""

from __future__ import annotations
from typing import List, Tuple

import re

import agent_common as ac


def _collect_spans(
    full: str,
    pattern: str,
    *,
    regex: bool,
    ignore_case: bool,
) -> List[Tuple[int, int, str]]:
    spans: List[Tuple[int, int, str]] = []
    if not regex:
        sub = pattern
        if not sub:
            raise ValueError("pattern 不能为空")
        flags = re.IGNORECASE if ignore_case else 0
        # 字面模式：| 分隔多个子串，逐个转义后用 OR 连接
        parts = [re.escape(p) for p in sub.split("|") if p]
        if parts:
            rx = re.compile("|".join(parts), flags)
        else:
            rx = re.compile(re.escape(sub), flags)
        for m in rx.finditer(full):
            s, e = m.span()
            spans.append((s, e, full[s:e]))
        return spans

    flags = re.MULTILINE
    if ignore_case:
        flags |= re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error as ex:
        raise ValueError(f"正则无效: {ex}") from ex
    for m in rx.finditer(full):
        s, e = m.span()
        spans.append((s, e, full[s:e]))
    return spans


def agent_main(
    *,
    path: str,
    pattern: str,
    regex: bool = False,
    ignore_case: bool = False,
    occurrence: int = 0,
    encoding: str = "utf-8",
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    """
    在**单文件**全文（与 read_file 同一字节序列）上查找 pattern 的每次命中，
    返回第 occurrence 次命中的 region_start / region_end（0-based 半开），可直接用于 replace_in_file。

    典型流程：find_in_file →（可选 read_file 核对）→ replace_in_file(region_*)。
    """
    _ = run_type
    try:
        fp = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        if not fp.is_file():
            raise FileNotFoundError(f"不是已存在文件: {fp}")

        full = ac.read_file_text(fp, encoding)
        spans = _collect_spans(full, pattern, regex=regex, ignore_case=ignore_case)
        total = len(spans)
        if total == 0:
            return ac.ok(
                {
                    "path": str(fp),
                    "found": False,
                    "pattern": pattern,
                    "regex": regex,
                    "total_matches": 0,
                    "occurrence": int(occurrence),
                    "region_start": None,
                    "region_end": None,
                    "matched_text": None,
                    "hint": "未命中：缩小/调整 pattern，或先 grep 再改 occurrence；目录搜索请用 grep_files。",
                }
            )

        occ = int(occurrence)
        if occ < 0 or occ >= total:
            raise ValueError(f"occurrence 越界: 请求 {occ}，当前共 {total} 处命中（0-based）")

        rs, re_, matched = spans[occ]
        sl, sc, el, ec = ac.span_region_rowcols(full, rs, re_)

        return ac.ok(
            {
                "path": str(fp),
                "found": True,
                "pattern": pattern,
                "regex": regex,
                "total_matches": total,
                "occurrence": occ,
                "region_start": rs,
                "region_end": re_,
                "matched_text": matched,
                "matched_length": re_ - rs,
                "start_line": sl,
                "start_column": sc,
                "end_line": el,
                "end_column": ec,
                "hint": "将 region_start、region_end 原样传入 replace_in_file；end_line/end_column 与行列矩形替换一致（end_column 为开区间）。",
            }
        )
    except Exception as e:
        return ac.err(e)






