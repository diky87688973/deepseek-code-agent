# -*- coding: utf-8 -*-
"""读取文件内容（整文件 / 行闭区间 / 行列矩形 / 字符半开区间）。

- **agent_main**：仅接受 Python 原生类型（str、int、bool、None 等），禁止将数组/对象以 JSON 字符串传入。
- **main()**：CLI 防腐层，解析 argv 后调用 agent_main；`build_parser()` 供宿主在失败时捕获等效 `--help` 文本。
"""

from __future__ import annotations

import agent_common as ac


def agent_main(
    *,
    path: str,
    encoding: str = "utf-8",
    line_start: int | None = None,
    line_end: int | None = None,
    start_column: int | None = None,
    end_column: int | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
    max_chars: int = 500_000,
    allow_outside_workspace: bool = False,
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
        fp = ac.resolve_path(path, allow_outside_workspace=allow_outside_workspace)
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

        slice_mode: str | None = None
        resolved_char: tuple[int, int] | None = None
        out_lines: list[int | None] | None = None
        out_cols: list[int | None] | None = None

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
            "encodingHint": encoding,
            "content": chunk,
            "totalCharsReturned": len(chunk),
            "truncated": truncated,
            "lineCountEst": line_count,
            "sliceMode": slice_mode,
            "slice": {
                "lines": out_lines if has_lines_pair or has_cols else None,
                "columns": out_cols,
                "chars": (
                    [char_start, char_end]
                    if has_chars
                    else None
                ),
                "resolvedChars": list(resolved_char) if resolved_char else None,
            },
        }
        return ac.ok(data)
    except Exception as e:
        return ac.err(e)


def build_parser() -> argparse.ArgumentParser:
    import argparse

    p = argparse.ArgumentParser(description="read_file：CLI 防腐层 → agent_main（仅 Python 类型）")
    p.add_argument("--path", required=True)
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--lineStart", type=int, default=None)
    p.add_argument("--lineEnd", type=int, default=None)
    p.add_argument("--startColumn", type=int, default=None)
    p.add_argument("--endColumn", type=int, default=None)
    p.add_argument("--charStart", type=int, default=None)
    p.add_argument("--charEnd", type=int, default=None)
    p.add_argument("--maxChars", type=int, default=500_000)
    p.add_argument("--allowOutsideWorkspace", action="store_true")
    p.add_argument("--runType", default="", help="占位，与清单一致；只读工具不拦截")
    p.add_argument("--jsonOut", action="store_true")
    return p


def main() -> None:
    import json
    import sys

    args = build_parser().parse_args()
    r = agent_main(
        path=args.path,
        encoding=args.encoding,
        line_start=args.lineStart,
        line_end=args.lineEnd,
        start_column=args.startColumn,
        end_column=args.endColumn,
        char_start=args.charStart,
        char_end=args.charEnd,
        max_chars=args.maxChars,
        allow_outside_workspace=bool(args.allowOutsideWorkspace),
        run_type=str(args.runType or ""),
    )
    if args.jsonOut:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok") and isinstance(r.get("data"), dict):
            print(r["data"].get("content", ""), end="")
        else:
            print((r.get("error") or {}).get("message", ""), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
