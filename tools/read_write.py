# -*- coding: utf-8 -*-
"""管道式读写：read_file 的输出在进程内直接作为 write_file 的输入，避免经模型传递大段正文。"""

from __future__ import annotations

from typing import Optional

import agent_common as ac
from read_file import agent_main as _read_main
from write_file import agent_main as _write_main


def agent_main(
    *,
    source_path: str,
    dest_path: str,
    encoding: str = "utf-8",
    encoding_write: Optional[str] = None,
    line_start: Optional[int] = None,
    line_end: Optional[int] = None,
    start_column: Optional[int] = None,
    end_column: Optional[int] = None,
    char_start: Optional[int] = None,
    char_end: Optional[int] = None,
    max_chars: int = 0,
    dry_run: bool = True,
    create_only: bool = False,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    """
    语义等同 shell 管道：read 侧得到的正文原样写入 dest_path。
    max_chars=0 表示读侧不截断（大文件注意内存）；否则传给 read_file 的 max_chars。
    encoding_write 省略则与 encoding 相同。
    """
    enc_w = (encoding_write or encoding).strip() or "utf-8"
    r = _read_main(
        path=source_path,
        encoding=encoding,
        line_start=line_start,
        line_end=line_end,
        start_column=start_column,
        end_column=end_column,
        char_start=char_start,
        char_end=char_end,
        max_chars=max_chars,
        restrict_to_workspace=restrict_to_workspace,
        run_type="",
    )
    if not r.get("ok"):
        return r
    data_in = r.get("data")
    if not isinstance(data_in, dict):
        return {"ok": False, "data": None, "error": {"type": "ToolError", "message": "read_file 返回缺少 data"}}
    content = data_in.get("content")
    if not isinstance(content, str):
        return {"ok": False, "data": None, "error": {"type": "ToolError", "message": "read_file 未返回 content 字符串"}}

    w = _write_main(
        path=dest_path,
        content=content,
        encoding=enc_w,
        dry_run=dry_run,
        create_only=create_only,
        restrict_to_workspace=restrict_to_workspace,
        run_type=run_type,
    )
    if not w.get("ok"):
        return w
    data_out = w.get("data")
    if not isinstance(data_out, dict):
        return {"ok": False, "data": None, "error": {"type": "ToolError", "message": "write_file 返回异常"}}

    return ac.ok(
        {
            "source_path": str(data_in.get("path", source_path)),
            "dest_path": data_out.get("path", dest_path),
            "encoding_read": encoding,
            "encoding_write": enc_w,
            "char_count": len(content),
            "truncated_read": bool(data_in.get("truncated")),
            "slice": data_in.get("slice"),
            "dry_run": bool(data_out.get("dry_run", dry_run)),
            "written": bool(data_out.get("written", False)),
            "existed_dest_before": bool(data_out.get("existed_before", False)),
            "byte_length_approx": data_out.get("byte_length_approx"),
        }
    )




