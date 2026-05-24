# -*- coding: utf-8 -*-
"""unified diff 解析与应用（供 `apply_patch` 使用，非独立 Agent 工具）。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple


def norm_path(raw: str) -> str:
    s = raw.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s


def _strip_git_ab_prefix(path: str) -> str:
    """去掉 unified diff 的 a/、b/ 前缀；Windows 绝对路径如 a/E:/x 须变为 E:/x。"""
    s = norm_path(path)
    if len(s) >= 2 and s[0] in "ab" and s[1] == "/":
        return s[2:]
    return s


def parse_diff_path_header(line_body: str) -> str:
    """解析 --- / +++ 行路径（去掉时间戳与 a|b 前缀）。"""
    raw = str(line_body or "").strip()
    if "\t" in raw:
        raw = raw.split("\t", 1)[0].strip()
    if raw in ("/dev/null", "dev/null"):
        return "/dev/null"
    return _strip_git_ab_prefix(raw)


def parse_unified_diff(patch_text: str) -> List[dict]:
    lines = patch_text.splitlines()
    i = 0
    files: List[dict] = []
    while i < len(lines):
        line = lines[i]
        if not line.startswith("--- "):
            i += 1
            continue
        if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
            raise ValueError("非法 unified diff：缺少 +++ 行")
        old_path = parse_diff_path_header(lines[i][4:])
        new_path = parse_diff_path_header(lines[i + 1][4:])
        i += 2
        hunks: List[dict] = []
        while i < len(lines) and lines[i].startswith("@@"):
            i += 1
            hunk_lines: List[Tuple[str, str]] = []
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
            hunks.append({"lines": hunk_lines})
        files.append({"old_path": old_path, "new_path": new_path, "hunks": hunks})
    if not files:
        raise ValueError("未解析到任何文件补丁")
    return files


def match_hunk_at(content_lines: List[str], start: int, hunk_lines: List[Tuple[str, str]]) -> bool:
    idx = start
    for prefix, text in hunk_lines:
        if prefix in (" ", "-"):
            if idx >= len(content_lines):
                return False
            if content_lines[idx] != text:
                return False
            idx += 1
    return True


def find_hunk_position(content_lines: List[str], hunk_lines: List[Tuple[str, str]], search_start: int) -> int:
    if match_hunk_at(content_lines, search_start, hunk_lines):
        return search_start
    for p in range(0, len(content_lines) + 1):
        if match_hunk_at(content_lines, p, hunk_lines):
            return p
    raise ValueError("hunk 上下文未匹配，无法安全应用补丁")


def apply_hunk(content_lines: List[str], hunk_lines: List[Tuple[str, str]], pos: int) -> List[str]:
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


def apply_file_patch(file_path: Path, hunks: List[dict]) -> str:
    original = file_path.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines()
    cursor = 0
    for h in hunks:
        pos = find_hunk_position(lines, h["lines"], cursor)
        lines = apply_hunk(lines, h["lines"], pos)
        cursor = pos
    return "\n".join(lines) + ("\n" if original.endswith("\n") and lines else "")


def load_patch_text(*, patch_text: Optional[str], patch_file: Optional[str]) -> str:
    n = int(patch_text is not None) + int(patch_file is not None)
    if n != 1:
        raise ValueError("patch_text 与 patch_file 必须且只能提供一个")
    if patch_text is not None:
        return patch_text
    return Path(patch_file).expanduser().read_text(encoding="utf-8", errors="replace")
