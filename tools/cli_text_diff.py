# -*- coding: utf-8 -*-
"""
CLI 文本对比工具
================

用途
----
对两段文本或两个文件做差异对比，输出 unified diff 与统计摘要；--jsonOut 时 data.diffMarkdown 为可直接粘贴的 Markdown fenced diff。
适合 agent 在编辑后做“变更核对”。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import difflib
import json

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser


def read_text_auto(path: Path, encoding: str) -> str:
    if encoding != "auto":
        return path.read_text(encoding=encoding, errors="replace")
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def get_text(file_arg, text_arg, encoding):
    has_file = file_arg is not None
    has_text = text_arg is not None
    if int(has_file) + int(has_text) != 1:
        raise ValueError("每一侧输入必须且只能一个：file 或 text")
    if has_file:
        fp = Path(file_arg)
        if not fp.exists():
            raise FileNotFoundError(f"文件不存在: {fp}")
        return read_text_auto(fp, encoding)
    return text_arg


def build_parser():
    p = _HelpFulParser(description="文本对比：输出 unified diff + 摘要")
    p.add_argument("--leftFile", help="左侧文件")
    p.add_argument("--leftText", help="左侧文本")
    p.add_argument("--rightFile", help="右侧文件")
    p.add_argument("--rightText", help="右侧文本")
    p.add_argument("--encoding", default="utf-8", help="文件编码，默认 utf-8，可选 auto")
    p.add_argument("--context", type=int, default=3, help="diff 上下文行数")
    p.add_argument("--jsonOut", action="store_true", help="JSON 输出")
    return p


def agent_main(
    *,
    left_file: str | None = None,
    left_text: str | None = None,
    right_file: str | None = None,
    right_text: str | None = None,
    encoding: str = "utf-8",
    context: int = 3,
) -> dict:
    """进程内入口；返回与 CLI --jsonOut 一致的外层结构。"""
    try:
        left = get_text(left_file, left_text, encoding)
        right = get_text(right_file, right_text, encoding)

        left_lines = left.splitlines()
        right_lines = right.splitlines()
        diff_lines = list(
            difflib.unified_diff(
                left_lines,
                right_lines,
                fromfile=left_file or "leftText",
                tofile=right_file or "rightText",
                lineterm="",
                n=context,
            )
        )
        add_cnt = sum(1 for x in diff_lines if x.startswith("+") and not x.startswith("+++"))
        del_cnt = sum(1 for x in diff_lines if x.startswith("-") and not x.startswith("---"))
        summary = {
            "same": left == right,
            "leftChars": len(left),
            "rightChars": len(right),
            "addedLines": add_cnt,
            "deletedLines": del_cnt,
        }
        diff_body = "\n".join(diff_lines)
        diff_md = "```diff\n" + diff_body + "\n```" if diff_lines else "```diff\n```"
        return {"ok": True, "data": {"summary": summary, "diff": diff_lines, "diffMarkdown": diff_md}, "error": None}
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e)}}


def main():
    parser = build_parser()
    args = parser.parse_args()
    res = agent_main(
        left_file=args.leftFile,
        left_text=args.leftText,
        right_file=args.rightFile,
        right_text=args.rightText,
        encoding=args.encoding,
        context=int(args.context),
    )
    if res["ok"]:
        data = res["data"]
        assert data is not None
        if args.jsonOut:
            print(json.dumps(res, ensure_ascii=False))
        else:
            print(json.dumps(data["summary"], ensure_ascii=False))
            if data["diff"]:
                print("\n".join(data["diff"]))
        return
    err = res.get("error") or {}
    msg = str(err.get("message", ""))
    full_msg = msg + "\n\n--help:\n" + _capture_help(parser)
    if args.jsonOut:
        print(
            json.dumps(
                {"ok": False, "data": None, "error": {**err, "message": full_msg}},
                ensure_ascii=False,
            )
        )
    else:
        raise RuntimeError(full_msg)


if __name__ == "__main__":
    main()

