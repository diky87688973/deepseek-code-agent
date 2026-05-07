# -*- coding: utf-8 -*-
"""
CLI 跨文件搜索替换工具
======================

用途
----
在目录/文件中按字面字符串或正则搜索并替换。

用法
----
  cli_find_replace.py --target <dir|file> --pattern <old> --replacement <new>
    [--glob "*.py"] [--recursive] [--preview | --execute] [--backup]

流程
----
  --preview（默认）：列出所有匹配文件、命中位置，不修改
  --execute：实际执行替换
  --backup：替换前对每个文件创建 .bak 备份

输出（JSON）
-----------
  {ok, data: {..., details, diffMarkdown?}}
  diffMarkdown：供宿主拼接 assistant_markdown 的 fenced diff；由本次扫描内存中的改前/改后文本算出，与写盘与否无关。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8
_stdio_utf8.install_stdio_utf8()

import argparse
import difflib
import json
import re

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser

# 与 code_web_agent._CHAT_DIFF_BODY_MAX 对齐：控制 diff 正文体积，避免 JSON 与聊天侧暴涨
_DIFF_MARKDOWN_BODY_MAX = 16000


def read_text_auto(path: Path, encoding: str) -> str:
    if encoding != "auto":
        return path.read_text(encoding=encoding, errors="replace")
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def collect_files(target: Path, recursive: bool, glob_pattern: str) -> list[Path]:
    if target.is_file():
        return [target]
    if not target.is_dir():
        raise ValueError(f"target 不是文件也不是目录: {target}")
    it = target.rglob(glob_pattern) if recursive else target.glob(glob_pattern)
    return sorted(p for p in it if p.is_file())


def build_parser() -> argparse.ArgumentParser:
    epilog = (
        "安全设计：\n"
        "  默认 --preview 模式（只报告不改写），需显式 --execute 才执行写入。\n"
        "  --backup 可在执行前备份原文件为 .bak。\n"
    )
    p = _HelpFulParser(
        description="跨文件搜索替换：字面字符串或正则模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    p.add_argument("--target", required=True, help="目标文件或目录")
    p.add_argument("--pattern", required=True, help="搜索模式（字面字符串）")
    p.add_argument("--replacement", required=True, help="替换文本")
    p.add_argument("--glob", default="*", help='文件过滤，如 "*.py"（默认 "*"）')
    p.add_argument("--recursive", action="store_true", help="递归子目录")
    p.add_argument("--preview", action="store_true", help="仅预览（未传 --execute 时默认预览）")
    p.add_argument("--execute", action="store_true", help="实际执行替换")
    p.add_argument("--backup", action="store_true", help="替换前备份 .bak")
    p.add_argument("--encoding", default="utf-8", help="编码，默认 utf-8，可选 auto")
    p.add_argument("--ignoreCase", action="store_true", help="忽略大小写")
    p.add_argument("--jsonOut", action="store_true", help="JSON 输出")
    return p


def agent_main(
    *,
    target: str,
    pattern: str,
    replacement: str,
    glob_pattern: str = "*",
    recursive: bool = False,
    preview: bool = False,
    execute: bool = False,
    backup: bool = False,
    encoding: str = "utf-8",
    ignore_case: bool = False,
) -> dict:
    """进程内入口；返回 {ok, data, error}。"""
    try:
        if execute:
            preview = False
        elif not preview:
            preview = True

        tp = Path(target)
        if not tp.exists():
            raise FileNotFoundError(f"target 不存在: {tp}")

        files = collect_files(tp, recursive, glob_pattern)
        details = []
        diff_chunks: list[str] = []
        total_replacements = 0
        files_modified = 0

        for fp in files:
            text = read_text_auto(fp, encoding)
            count = text.count(pattern)

            if count == 0:
                continue

            new_text = text.replace(pattern, replacement)
            if new_text != text:
                dl = list(
                    difflib.unified_diff(
                        text.splitlines(),
                        new_text.splitlines(),
                        fromfile=str(fp),
                        tofile=str(fp),
                        lineterm="",
                        n=3,
                    )
                )
                if dl:
                    diff_chunks.append("\n".join(dl))

            if execute and new_text != text:
                if backup:
                    bak = fp.with_suffix(fp.suffix + ".bak")
                    bak.write_text(text, encoding="utf-8" if encoding == "auto" else encoding)
                fp.write_text(new_text, encoding="utf-8" if encoding == "auto" else encoding)
                files_modified += 1

            total_replacements += count
            details.append({
                "file": str(fp),
                "count": count,
                "modified": bool(execute and new_text != text),
            })

        diff_body = "\n\n".join(diff_chunks)
        if len(diff_body) > _DIFF_MARKDOWN_BODY_MAX:
            diff_body = diff_body[:_DIFF_MARKDOWN_BODY_MAX] + "\n…"
        diff_markdown: str | None
        if diff_body.strip():
            diff_markdown = "```diff\n" + diff_body + "\n```"
        else:
            diff_markdown = None

        data = {
            "filesScanned": len(files),
            "filesModified": files_modified,
            "totalReplacements": total_replacements,
            "mode": "execute" if execute else "preview",
            "details": details,
            "diffMarkdown": diff_markdown,
        }
        return {"ok": True, "data": data, "error": None}
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e)}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    res = agent_main(
        target=args.target,
        pattern=args.pattern,
        replacement=args.replacement,
        glob_pattern=args.glob,
        recursive=bool(args.recursive),
        preview=bool(args.preview),
        execute=bool(args.execute),
        backup=bool(args.backup),
        encoding=args.encoding,
        ignore_case=bool(args.ignoreCase),
    )
    if args.jsonOut:
        print(json.dumps(res, ensure_ascii=False))
    else:
        if res["ok"]:
            d = res["data"]; assert d is not None
            print(f"模式: {d['mode']}")
            print(f"扫描文件: {d['filesScanned']} | 修改文件: {d['filesModified']} | 总替换数: {d['totalReplacements']}")
            for item in d["details"]:
                tag = "✓" if item["modified"] else "·"
                print(f"  {tag} {item['file']} ({item['count']} 处)")
        else:
            err = res.get("error") or {}
            print(str(err.get("message", "")), file=__import__('sys').stderr)
            raise RuntimeError(str(err.get("message", "")))


if __name__ == "__main__":
    main()
