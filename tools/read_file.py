# -*- coding: utf-8 -*-
"""读取文件内容（整文件 / 行闭区间 / 行列矩形 / 字符半开区间）。

- **agent_main**：仅接受 Python 原生类型（str、int、bool、None 等），禁止将数组/对象以 JSON 字符串传入。
- **main()**：仅供人工调试，解析 argv 后调用 agent_main；`build_parser()` 供宿主在失败时捕获等效 `--help` 文本。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import agent_common as ac


def agent_main(
    *,
    path: str,
    encoding: str = "utf-8",
    line_start: Optional[int] = None,
    line_end: Optional[int] = None,
    start_column: Optional[int] = None,
    end_column: Optional[int] = None,
    char_start: Optional[int] = None,
    char_end: Optional[int] = None,
    max_chars: int = 500_000,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    """
    读取文本文件。

    - **整文件**：不传行/列/字符区间（或仅 max_chars 截断）。
    - **行区间**：`line_start` / `line_end`（1-based 闭区间，与旧 extract `lines` 一致）。
    - **行列矩形**：同时提供 `line_start`、`line_end`、`start_column`、`end_column`
      （列 1-based，`end_column` 为开区间右界，负数从行末倒推，与 replace_in_file 行列矩形一致）。
    - **字符半开区间**：`char_start` / `char_end`（0-based [char_start, char_end)），`char_end` 可为负表示从文末倒推。

    行类与字符类区间不要混用；行列模式不要与 `char_*` 混用。

    run_type 占位，供宿主 Plan/Execute 使用（本工具只读）。
    """
    _ = run_type
    try:
        fp = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        if not fp.is_file():
            raise FileNotFoundError(f"不是已存在文件: {fp}")

        has_cols = start_column is not None or end_column is not None
        has_lines_pair = line_start is not None or line_end is not None
        has_chars = char_start is not None or char_end is not None

        if has_cols:
            if not has_lines_pair:
                raise ValueError("行列矩形须同时提供行号与列号区间")

        if int(has_cols) + int(has_lines_pair and not has_cols) + int(has_chars) > 1:
            raise ValueError("不要混用：行列矩形 / 纯行区间 / 字符区间 只能选一种")

        if has_cols:
            if line_start is None or line_end is None or start_column is None or end_column is None:
                raise ValueError("行列矩形须同时提供 line_start、line_end、start_column、end_column")
            ls = int(line_start)
            le = int(line_end)
            sc = int(start_column)
            ec = int(end_column)
        elif has_lines_pair:
            ls = int(line_start) if line_start is not None else 1
            le = int(line_end) if line_end is not None else ls
        else:
            ls = le = 0

        full = ac.read_file_text(fp, encoding)
        lines_keepends = full.splitlines(keepends=True)

        slice_mode: Optional[str] = None
        resolved_char: Optional[Tuple[int, int]] = None
        out_lines: Optional[List[Optional[int]]] = None
        out_cols: Optional[List[Optional[int]]] = None

        if has_cols:
            slice_mode = "lines_columns"
            chunk = ac.text_slice_lines_columns(
                full, lines_keepends, ls, sc, le, ec
            )
            out_lines = [line_start, line_end]
            out_cols = [start_column, end_column]
        elif has_lines_pair:
            slice_mode = "lines"
            if ls < 1 or le < ls:
                raise ValueError("line 区间非法：要求 start>=1 且 end>=start")
            chunk = ac.text_slice_by_lines(lines_keepends, ls, le)
            out_lines = [line_start, line_end]
            out_cols = None
        elif has_chars:
            slice_mode = "offsets"
            cs = int(char_start) if char_start is not None else 0
            ce_raw = len(full) if char_end is None else int(char_end)
            chunk, sp, ep = ac.text_slice_offsets(full, cs, ce_raw)
            resolved_char = (sp, ep)
        else:
            slice_mode = None
            chunk = full

        truncated = False
        if max_chars > 0 and len(chunk) > max_chars:
            chunk = chunk[:max_chars]
            truncated = True

        line_count = chunk.count("\n") + (1 if chunk and not chunk.endswith("\n") else 0)
        if has_lines_pair and not has_cols and chunk and not chunk.endswith("\n"):
            line_count = max(1, chunk.count("\n") + 1)

        data: dict = {
            "path": str(fp),
            "encoding_hint": encoding,
            "content": chunk,
            "total_chars_returned": len(chunk),
            "truncated": truncated,
            "line_count_est": line_count,
            "slice_mode": slice_mode,
            "slice": {
                "lines": out_lines if has_lines_pair or has_cols else None,
                "columns": out_cols,
                "chars": (
                    [char_start, char_end]
                    if has_chars
                    else None
                ),
                "resolved_chars": list(resolved_char) if resolved_char else None,
            },
        }
        return ac.ok(data)
    except Exception as e:
        return ac.err(e)


def build_parser() -> argparse.ArgumentParser:
    import argparse

    p = argparse.ArgumentParser(description="read_file：人工调试入口 → agent_main（仅 Python 类型）")
    p.add_argument("--path", required=True)
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--line_start", type=int, default=None)
    p.add_argument("--line_end", type=int, default=None)
    p.add_argument("--start_column", type=int, default=None)
    p.add_argument("--end_column", type=int, default=None)
    p.add_argument("--char_start", type=int, default=None)
    p.add_argument("--char_end", type=int, default=None)
    p.add_argument("--max_chars", type=int, default=500_000)
    p.add_argument(
        "--restrict_to_workspace",
        action="store_true",
        help="将 path 限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--run_type", default="", help="占位，与清单一致；只读工具不拦截")
    p.add_argument("--json_out", action="store_true")
    return p


def main() -> None:
    import json
    import sys

    args = build_parser().parse_args()
    r = agent_main(
        path=args.path,
        encoding=args.encoding,
        line_start=args.line_start,
        line_end=args.line_end,
        start_column=args.start_column,
        end_column=args.end_column,
        char_start=args.char_start,
        char_end=args.char_end,
        max_chars=args.max_chars,
        restrict_to_workspace=bool(args.restrict_to_workspace),
        run_type=str(args.run_type or ""),
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok") and isinstance(r.get("data"), dict):
            print(r["data"].get("content", ""), end="")
        else:
            print((r.get("error") or {}).get("message", ""), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
