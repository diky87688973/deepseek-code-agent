# -*- coding: utf-8 -*-
from __future__ import annotations
"""
CLI 正则定位工具
================

用途
----
在单文件或目录中执行正则检索，输出命中位置（索引/行列）与预览文本。
可作为 agent 的「定位 -> 编辑」中间工具。
"""

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import re

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


def line_col(text: str, idx: int) -> tuple[int, int]:
    line = text.count("\n", 0, idx) + 1
    last_nl = text.rfind("\n", 0, idx)
    col = idx + 1 if last_nl < 0 else idx - last_nl
    return line, col


def collect_files(target: Path, recursive: bool, glob_pattern: str) -> list[Path]:
    if target.is_file():
        return [target]
    if not target.is_dir():
        raise ValueError(f"target 既不是文件也不是目录: {target}")
    it = target.rglob(glob_pattern) if recursive else target.glob(glob_pattern)
    return [p for p in it if p.is_file()]


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="正则定位：返回匹配位置与预览")
    p.add_argument("--target", required=True, help="目标文件或目录")
    p.add_argument("--pattern", required=True, help="正则表达式")
    p.add_argument("--ignoreCase", action="store_true", help="忽略大小写")
    p.add_argument("--multiline", action="store_true", help="开启 re.S + re.M")
    p.add_argument("--recursive", action="store_true", help="目录模式是否递归")
    p.add_argument("--glob", default="*", help='目录模式过滤，如 "*.py"')
    p.add_argument("--encoding", default="utf-8", help="文件编码，默认 utf-8，可选 auto")
    p.add_argument("--limit", type=int, default=200, help="最多返回命中数")
    p.add_argument("--rangesOut", help="将匹配区间输出为 masks JSON 文件（可直接给 delete_segments.masks）")
    p.add_argument("--jsonOut", action="store_true", help="JSON 输出")
    return p


def agent_main(
    *,
    target: str,
    pattern: str,
    ignore_case: bool = False,
    multiline: bool = False,
    recursive: bool = False,
    glob_pattern: str = "*",
    encoding: str = "utf-8",
    limit: int = 200,
    ranges_out: str | None = None,
) -> dict:
    """进程内入口；返回与 CLI --jsonOut 一致的外层结构。"""
    try:
        if limit <= 0:
            raise ValueError("limit 必须 > 0")
        tp = Path(target)
        if not tp.exists():
            raise FileNotFoundError(f"target 不存在: {tp}")

        flags = 0
        if ignore_case:
            flags |= re.IGNORECASE
        if multiline:
            flags |= re.MULTILINE | re.DOTALL
        regex = re.compile(pattern, flags)

        files = collect_files(tp, recursive, glob_pattern)
        out: list[dict] = []
        for fp in files:
            text = read_text_auto(fp, encoding)
            for m in regex.finditer(text):
                s, e = m.span()
                line, col = line_col(text, s)
                out.append(
                    {
                        "file": str(fp),
                        "start": s,
                        "end": e,
                        "line": line,
                        "column": col,
                        "match": m.group(0),
                    }
                )
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break

        if ranges_out:
            out_path = Path(ranges_out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            masks = [{"start": x["start"], "end": x["end"]} for x in out]
            out_path.write_text(json.dumps(masks, ensure_ascii=False, indent=2), encoding="utf-8")

        data = {"count": len(out), "items": out, "rangesOut": ranges_out}
        return {"ok": True, "data": data, "error": None}
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e)}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    res = agent_main(
        target=args.target,
        pattern=args.pattern,
        ignore_case=bool(args.ignoreCase),
        multiline=bool(args.multiline),
        recursive=bool(args.recursive),
        glob_pattern=args.glob,
        encoding=args.encoding,
        limit=int(args.limit),
        ranges_out=args.rangesOut,
    )
    if res["ok"]:
        data = res["data"]
        assert data is not None
        if args.jsonOut:
            print(json.dumps(res, ensure_ascii=False))
        else:
            for item in data["items"]:
                print(
                    f"{item['file']}:{item['line']}:{item['column']} "
                    f"[{item['start']},{item['end']}) {item['match']}"
                )
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
