# -*- coding: utf-8 -*-
"""管道式读写：read_file 的输出在进程内直接作为 write_file 的输入，避免经模型传递大段正文。"""

from __future__ import annotations

import agent_common as ac
from read_file import agent_main as _read_main
from write_file import agent_main as _write_main


def agent_main(
    *,
    source_path: str,
    dest_path: str,
    encoding: str = "utf-8",
    encoding_write: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    start_column: int | None = None,
    end_column: int | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
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
            "sourcePath": str(data_in.get("path", source_path)),
            "destPath": data_out.get("path", dest_path),
            "encodingRead": encoding,
            "encodingWrite": enc_w,
            "charCount": len(content),
            "truncatedRead": bool(data_in.get("truncated")),
            "slice": data_in.get("slice"),
            "dryRun": bool(data_out.get("dryRun", dry_run)),
            "written": bool(data_out.get("written", False)),
            "existedDestBefore": bool(data_out.get("existedBefore", False)),
            "byteLengthApprox": data_out.get("byteLengthApprox"),
        }
    )


def main() -> None:
    import argparse
    import json

    p = argparse.ArgumentParser(description="read_write：管道对接 read→write")
    p.add_argument("--sourcePath", required=True)
    p.add_argument("--destPath", required=True)
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--encodingWrite", default=None)
    p.add_argument("--lineStart", type=int, default=None)
    p.add_argument("--lineEnd", type=int, default=None)
    p.add_argument("--startColumn", type=int, default=None)
    p.add_argument("--endColumn", type=int, default=None)
    p.add_argument("--charStart", type=int, default=None)
    p.add_argument("--charEnd", type=int, default=None)
    p.add_argument("--maxChars", type=int, default=0)
    p.add_argument("--dryRun", action="store_true", default=True)
    p.add_argument("--commit", action="store_false", dest="dryRun")
    p.add_argument("--createOnly", action="store_true")
    p.add_argument(
        "--restrictToWorkspace",
        action="store_true",
        help="读写两侧路径均限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--runType", default="")
    p.add_argument("--jsonOut", action="store_true")
    args = p.parse_args()
    r = agent_main(
        source_path=args.sourcePath,
        dest_path=args.destPath,
        encoding=args.encoding,
        encoding_write=args.encodingWrite,
        line_start=args.lineStart,
        line_end=args.lineEnd,
        start_column=args.startColumn,
        end_column=args.endColumn,
        char_start=args.charStart,
        char_end=args.charEnd,
        max_chars=int(args.maxChars),
        dry_run=args.dryRun,
        create_only=args.createOnly,
        restrict_to_workspace=bool(getattr(args, "restrictToWorkspace", False)),
        run_type=str(args.runType or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
