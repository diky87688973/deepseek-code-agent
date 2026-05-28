#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性对齐 tool_list_agent.json：P2 restrict、只读去 run_type、path 唯一命名。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT_PATH = ROOT / "tools" / "tool_list_agent.json"

KEEP_RESTRICT = frozenset(
    {
        "write_file.py",
        "read_write.py",
        "delete_file.py",
        "file_ops.py",
        "replace_in_file.py",
        "apply_patch.py",
        "archive.py",
        "run_command.py",
        "python_inline.py",
    }
)

NO_RUN_TYPE = frozenset(
    {
        "read_file.py",
        "glob_files.py",
        "grep_files.py",
        "find_in_file.py",
        "regex_locate.py",
        "file_search.py",
        "git_workspace.py",
        "web_fetch.py",
        "web_fetch_render.py",
        "unified_diagnose.py",
        "env_probe.py",
        "ip_geolocate.py",
        "open_meteo_weather.py",
        "data_table.py",
        "text_diff.py",
        "session_send.py",
        "session_multisend.py",
        "session_broadcast.py",
        "session_wait.py",
        "session_list.py",
        "session_create.py",
    }
)

PATH_ONLY_TOOLS = frozenset({"data_table.py", "image_ocr.py"})
REMOVE_GLOB_PATTERN_ALIAS = frozenset({"glob_files.py"})


def _rename_example_keys(ex: object, tool_name: str) -> object:
    if not isinstance(ex, dict):
        return ex
    args = ex.get("args")
    if not isinstance(args, dict):
        return ex
    new_args = dict(args)
    if tool_name in PATH_ONLY_TOOLS and "source" in new_args and "path" not in new_args:
        new_args["path"] = new_args.pop("source")
    new_args.pop("restrict_to_workspace", None)
    return {**ex, "args": new_args}


def main() -> int:
    cat = json.loads(CAT_PATH.read_text(encoding="utf-8"))
    hints = cat.setdefault("agent_hints", {})
    hints["workspace_safety"] = (
        "默认可访问 WORKSPACE_DIR 内外的绝对路径。"
        "restrict_to_workspace 仅用于写盘/命令类：write_file、read_write、replace_in_file、apply_patch、"
        "delete_file、file_ops、archive、run_command、python_inline；只读工具（read_file、grep_files、glob_files 等）不传。"
    )
    hints["param_naming"] = (
        "同义语义只保留一个参数名，禁止别名：目录/单文件路径用 path（--path）；"
        "文件名通配用 glob_pattern（glob_files 勿用 pattern 别名）；内容匹配用 pattern（grep_files 等）。"
        "data_table、image_ocr 的表格/图片文件用 path，勿用 source。"
        "file_ops/archive 的 source 与 dest 表示来源/目标路径对，不是 path 的别名。"
        "会话模式 run_type 仅写盘/命令类工具 schema 出现；只读工具无 run_type。"
        "context_lines、no_gitignore、timeout_sec、encoding 等见各 tool 说明。"
    )
    hints["tool_help_on_failure"] = (
        "ok=false 时读 error.tool_help。路径/参数/门控类错误为简短说明；"
        "复杂未知错误才附 argparse --help 与 catalog 摘要。"
    )

    for t in cat.get("tools") or []:
        name = str(t.get("name") or "")
        new_args = []
        for a in t.get("args") or []:
            if not isinstance(a, dict):
                continue
            flag = str(a.get("flag") or "")
            if flag == "--restrict_to_workspace" and name not in KEEP_RESTRICT:
                continue
            if flag == "--run_type" and name in NO_RUN_TYPE:
                continue
            if flag == "--pattern" and name in REMOVE_GLOB_PATTERN_ALIAS:
                continue
            arg = dict(a)
            if flag == "--source" and name in PATH_ONLY_TOOLS:
                arg["flag"] = "--path"
                if "表格" in str(arg.get("description") or ""):
                    arg["description"] = "表格文件路径（CSV/Excel/TSV）。"
                elif "图片" in str(arg.get("description") or "") or name == "image_ocr.py":
                    arg["description"] = "图片文件路径。"
            new_args.append(arg)
        t["args"] = new_args

        if name == "glob_files.py":
            t["purpose"] = (
                "glob_files：在 path 目录下列出路径（不读内容）。匹配用 glob_pattern；"
                "entry_type 控制 file/dir/all；可选 no_gitignore。"
            )
        if name == "image_ocr.py":
            t["purpose"] = (
                "image_ocr：对 path 指定图片做 OCR，engine 可选 pytesseract 或 easyocr。"
            )
        if name == "read_file.py":
            for ex in t.get("examples") or []:
                if isinstance(ex, dict) and "restrict_to_workspace" in str(ex.get("note") or ""):
                    ex["note"] = "auto 编码探测；默认可访问工作区外绝对路径。"

        t["examples"] = [
            _rename_example_keys(ex, name) for ex in (t.get("examples") or [])
        ]

    CAT_PATH.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("OK: updated", CAT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
