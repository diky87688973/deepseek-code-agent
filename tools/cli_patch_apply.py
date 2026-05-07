# -*- coding: utf-8 -*-
"""
CLI 补丁应用工具
================

用途
----
按 unified diff（---/+++/@@）对一个或多个文件执行结构化修改。
适用于代码批量替换、插入、删除等高精度编辑场景。

统一语义
--------
- 本工具不直接用 start/end 索引，而是使用 patch hunk 上下文定位。
- 与工具库统一原则一致：若需要区间，推荐使用 0-based + [start, end) 半开区间。

输入方式（三选一）
-----------------
- --patchText: 直接传入 patch 文本
- --patchFile: 从文件读取 patch 文本
- --patchStdin: 从 stdin 读取 patch 文本

安全策略
--------
- 默认只允许修改 --root 目录内文件
- 仅支持 Update File（必须目标文件已存在）
- 不支持 Add/Delete/Rename（避免小模型误操作）
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import sys

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser


def _load_patch(
    patch_text: str | None,
    patch_file: str | None,
    patch_stdin_body: str | None,
) -> str:
    count = int(patch_text is not None) + int(patch_file is not None) + int(patch_stdin_body is not None)
    if count != 1:
        raise ValueError("patch 输入必须且只能一个：--patchText / --patchFile / --patchStdin")
    if patch_text is not None:
        return patch_text
    if patch_file is not None:
        return Path(patch_file).read_text(encoding="utf-8", errors="replace")
    assert patch_stdin_body is not None
    return patch_stdin_body


def _norm_path(raw: str) -> str:
    s = raw.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s


def _parse_unified_diff(patch_text: str) -> list[dict]:
    lines = patch_text.splitlines()
    i = 0
    files: list[dict] = []
    while i < len(lines):
        line = lines[i]
        if not line.startswith("--- "):
            i += 1
            continue
        if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
            raise ValueError("非法 unified diff：缺少 +++ 行")
        old_path = _norm_path(lines[i][4:].strip())
        new_path = _norm_path(lines[i + 1][4:].strip())
        i += 2
        hunks: list[dict] = []
        while i < len(lines) and lines[i].startswith("@@"):
            header = lines[i]
            i += 1
            hunk_lines: list[tuple[str, str]] = []
            while i < len(lines):
                s = lines[i]
                if s.startswith("@@") or s.startswith("--- "):
                    break
                if s.startswith("\\ No newline at end of file"):
                    i += 1
                    continue
                if not s:
                    prefix = " "
                    text = ""
                else:
                    prefix = s[0]
                    text = s[1:] if prefix in (" ", "+", "-") else s
                    if prefix not in (" ", "+", "-"):
                        raise ValueError(f"非法 hunk 行: {s}")
                hunk_lines.append((prefix, text))
                i += 1
            hunks.append({"header": header, "lines": hunk_lines})
        files.append({"old_path": old_path, "new_path": new_path, "hunks": hunks})
    if not files:
        raise ValueError("未解析到任何文件补丁")
    return files


def _match_hunk_at(content_lines: list[str], start: int, hunk_lines: list[tuple[str, str]]) -> bool:
    idx = start
    for prefix, text in hunk_lines:
        if prefix in (" ", "-"):
            if idx >= len(content_lines):
                return False
            if content_lines[idx] != text:
                return False
            idx += 1
        elif prefix == "+":
            continue
    return True


def _find_hunk_position(content_lines: list[str], hunk_lines: list[tuple[str, str]], search_start: int) -> int:
    if _match_hunk_at(content_lines, search_start, hunk_lines):
        return search_start
    for p in range(0, len(content_lines) + 1):
        if _match_hunk_at(content_lines, p, hunk_lines):
            return p
    raise ValueError("hunk 上下文未匹配，无法安全应用补丁")


def _apply_hunk(content_lines: list[str], hunk_lines: list[tuple[str, str]], pos: int) -> list[str]:
    out = content_lines[:pos]
    idx = pos
    for prefix, text in hunk_lines:
        if prefix == " ":
            out.append(content_lines[idx])
            idx += 1
        elif prefix == "-":
            idx += 1
        elif prefix == "+":
            out.append(text)
    out.extend(content_lines[idx:])
    return out


def _apply_file_patch(file_path: Path, hunks: list[dict]) -> str:
    original = file_path.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines()
    cursor = 0
    for h in hunks:
        pos = _find_hunk_position(lines, h["lines"], cursor)
        lines = _apply_hunk(lines, h["lines"], pos)
        cursor = pos
    return "\n".join(lines) + ("\n" if original.endswith("\n") and lines else "")


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="按 unified diff 应用补丁（仅 Update File）")
    p.add_argument("--root", required=True, help="允许修改的根目录")
    p.add_argument("--patchText", help="直接传 patch 文本")
    p.add_argument("--patchFile", help="从文件读取 patch 文本")
    p.add_argument("--patchStdin", action="store_true", help="从 stdin 读取 patch 文本")
    p.add_argument("--dryRun", action="store_true", help="仅校验，不写盘")
    p.add_argument("--runType", choices=["auto", "plan", "execute"], default="", help="当前运行模式；plan 时操作被拒绝")
    p.add_argument("--jsonOut", action="store_true", help="JSON 输出")
    return p


def agent_main(
    *,
    root: str,
    patch_text: str | None = None,
    patch_file: str | None = None,
    patch_stdin_body: str | None = None,
    dry_run: bool = False,
    run_type: str = "",
    parser_for_help: argparse.ArgumentParser | None = None,
) -> dict:
    """进程内入口；stdin 模式请传入已读取的 patch 文本到 patch_stdin_body。"""
    try:
        rt = str(run_type or "").strip().lower()
        if rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写操作"}}
        r = Path(root).resolve()
        if not r.exists() or not r.is_dir():
            raise ValueError(f"root 非法: {r}")

        patch_text_res = _load_patch(patch_text, patch_file, patch_stdin_body)
        file_patches = _parse_unified_diff(patch_text_res)

        changed: list[str] = []
        for fp in file_patches:
            if fp["old_path"] != fp["new_path"]:
                raise ValueError("当前版本不支持 rename，请保持 old/new 路径一致")
            rel = Path(fp["new_path"])
            abs_path = (r / rel).resolve()
            if not str(abs_path).startswith(str(r)):
                raise ValueError(f"越界路径: {abs_path}")
            if not abs_path.exists():
                raise FileNotFoundError(f"仅支持更新已存在文件: {abs_path}")
            new_content = _apply_file_patch(abs_path, fp["hunks"])
            if not dry_run:
                abs_path.write_text(new_content, encoding="utf-8")
            changed.append(str(abs_path))

        return {"ok": True, "data": {"dryRun": dry_run, "changedFiles": changed}, "error": None}
    except Exception as e:
        msg = str(e) + ("\n\n--help:\n" + _capture_help(parser_for_help) if parser_for_help else "")
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": msg}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    stdin_body = sys.stdin.read() if args.patchStdin else None
    env = agent_main(
        root=args.root,
        patch_text=args.patchText,
        patch_file=args.patchFile,
        patch_stdin_body=stdin_body,
        dry_run=bool(args.dryRun),
        parser_for_help=parser,
    )
    if env["ok"]:
        if args.jsonOut:
            print(json.dumps(env, ensure_ascii=False))
        else:
            print("ok")
            for p in env["data"]["changedFiles"]:
                print(p)
    else:
        err = env.get("error") or {}
        if args.jsonOut:
            print(json.dumps({"ok": False, "data": None, "error": err}, ensure_ascii=False))
        else:
            raise RuntimeError(err.get("message", ""))


if __name__ == "__main__":
    main()
