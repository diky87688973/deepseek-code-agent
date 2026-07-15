# -*- coding: utf-8 -*-
"""管道式读写：read_file 的输出在进程内直接作为 write_file 的输入，避免经模型传递大段正文。"""

from __future__ import annotations

from typing import Optional

import agent_common as ac
from read_file import agent_main as _read_main
from write_file import agent_main as _write_main


def agent_main(
    *,
    source_path: str = "",
    dest_path: str = "",
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
    confirm_id: str = "",
) -> dict:
    """
    语义等同 shell 管道：read 侧得到的正文原样写入 dest_path。
    max_chars=0 表示读侧不截断（大文件注意内存）；否则传给 read_file 的 max_chars。
    encoding_write 省略则与 encoding 相同。
    """
    try:
        rt = str(run_type or "").strip().lower()
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}
        if want_write and not confirm_id:
            return {"ok": False, "data": None, "error": {"type": "ConfirmIdRequired", "message": "dry_run=false 必须传 confirm_id：请先 dry_run=true 预览，再用返回的 confirm_id 提交。"}}

        # ── confirm_id 模式：从预览存储恢复参数 ──
        if confirm_id:
            from agent_v4.live_state import consume_confirm_id
            stored = consume_confirm_id(str(confirm_id).strip())
            if stored is None:
                return {"ok": False, "data": None, "error": {"type": "InvalidConfirmId", "message": "confirm_id 无效或已过期，请重新 dry_run=true 预览"}}
            sp = stored.get("params", {})
            source_path = sp.get("source_path", source_path)
            dest_path = sp.get("dest_path", dest_path)
            encoding = sp.get("encoding", encoding)
            encoding_write = sp.get("encoding_write", encoding_write)
            line_start = sp.get("line_start", line_start)
            line_end = sp.get("line_end", line_end)
            start_column = sp.get("start_column", start_column)
            end_column = sp.get("end_column", end_column)
            char_start = sp.get("char_start", char_start)
            char_end = sp.get("char_end", char_end)
            max_chars = sp.get("max_chars", max_chars)
            create_only = sp.get("create_only", create_only)
            restrict_to_workspace = sp.get("restrict_to_workspace", restrict_to_workspace)
            dry_run = False  # confirm_id 隐含用户已审核预览，强制写入

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

        fp = ac.resolve_path(dest_path, allow_outside_workspace=not restrict_to_workspace)
        existed = fp.is_file()
        if create_only and existed:
            raise FileExistsError(f"create_only：文件已存在 {fp}")

        if dry_run:
            # 预览：用 _write_main 生成 diff，但不落盘
            w = _write_main(
                path=dest_path,
                content=content,
                encoding=enc_w,
                dry_run=True,
                create_only=create_only,
                restrict_to_workspace=restrict_to_workspace,
                run_type="",
            )
            if not w.get("ok"):
                return w
            data_out = w.get("data") or {}

            # 生成 confirm_id 供后续免参数提交
            _confirm_id = ""
            from agent_v4.live_state import create_confirm_id, mark_confirmed
            _confirm_id = create_confirm_id("read_write", {
                "source_path": source_path,
                "dest_path": dest_path,
                "encoding": encoding,
                "encoding_write": enc_w,
                "line_start": line_start,
                "line_end": line_end,
                "start_column": start_column,
                "end_column": end_column,
                "char_start": char_start,
                "char_end": char_end,
                "max_chars": max_chars,
                "create_only": create_only,
                "restrict_to_workspace": restrict_to_workspace,
            })
            mark_confirmed(_confirm_id)

            return ac.ok({
                "source_path": str(data_in.get("path", source_path)),
                "dest_path": data_out.get("path", dest_path),
                "encoding_read": encoding,
                "encoding_write": enc_w,
                "char_count": len(content),
                "truncated_read": bool(data_in.get("truncated")),
                "slice": data_in.get("slice"),
                "dry_run": True,
                "written": False,
                "existed_dest_before": bool(data_out.get("existed_before", existed)),
                "byte_length_approx": data_out.get("byte_length_approx"),
                "confirm_id": _confirm_id,
            })

        # 真写：直接落盘，不调 _write_main（避免 ConfirmIdRequired 冲突）
        ac.write_unicode_file(fp, content, encoding=enc_w)
        return ac.ok({
            "source_path": str(data_in.get("path", source_path)),
            "dest_path": str(fp),
            "encoding_read": encoding,
            "encoding_write": enc_w,
            "char_count": len(content),
            "truncated_read": bool(data_in.get("truncated")),
            "slice": data_in.get("slice"),
            "dry_run": False,
            "written": True,
            "existed_dest_before": existed,
            "byte_length_approx": len(content.encode(enc_w, errors="replace")),
        })
    except Exception as e:
        return ac.err(e)
