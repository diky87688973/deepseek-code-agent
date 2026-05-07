#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''用户分岔确认：模型在概念含混时发起选项，由用户选择或自由输入后回传 confirm。'''
from __future__ import annotations

import argparse
import json
import re
import sys

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

from cli_help_share import _capture_help, _HelpFulParser

BUILTIN_TITLE = "对于示例任务有多种实现，你更倾向哪一种？"
BUILTIN_CONFIRMS_JSON = '["使用A方案","使用B方案","其他"]'
BUILTIN_CONFIRM = ""
BUILTIN_INTERACTIVE = True


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="用户分岔确认：title + 选项列表；成功后 data.confirm 为用户选择或补充说明")
    p.add_argument(
        "--payload",
        help='可选 JSON 对象字符串：含 title、confirms 数组与可选 confirm；可选 multi（多选）、customOptionIndex（整数下标，和该选项同行的单行自定义输入）；与 --title/--confirms 二选一（推荐 agent 用本参数）',
    )
    p.add_argument("--title", help="向用户展示的确认标题/问题")
    p.add_argument(
        "--confirms",
        help='选项列表的 JSON 数组字符串，如 ["方案A","方案B","其他"]',
    )
    p.add_argument(
        "--confirm",
        help="用户选定后的结论（宿主回填或交互结束后得到）；若已提供则直接成功返回",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="在 TTY 下对 stderr 打印编号菜单，从 stdin 读取一行（测试或无参右键运行）",
    )
    p.add_argument("--jsonOut", action="store_true", help="向 stdout 输出统一 JSON")
    return p


def _emit(ok: bool, data: dict | None, error: dict | None) -> None:
    print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))


def _parse_confirms(raw: str) -> list[str]:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"confirms 须为 JSON 数组字符串: {e}") from e
    if not isinstance(obj, list) or len(obj) < 1:
        raise ValueError("confirms 须为非空 JSON 数组")
    out: list[str] = []
    for i, x in enumerate(obj):
        if not isinstance(x, str) or not x.strip():
            raise ValueError(f"confirms[{i}] 须为非空字符串")
        out.append(x)
    return out


def _interactive_resolve(title: str, confirms: list[str]) -> str:
    print(title, file=sys.stderr)
    for i, c in enumerate(confirms, start=1):
        print(f"  [{i}] {c}", file=sys.stderr)
    print("输入编号选择，或直接输入自定义说明：", file=sys.stderr)
    line = sys.stdin.readline()
    if not line:
        raise ValueError("未读取到用户输入")
    s = line.strip()
    if not s:
        raise ValueError("输入为空")
    if re.fullmatch(r"\d+", s):
        idx = int(s)
        if 1 <= idx <= len(confirms):
            return confirms[idx - 1]
    return s


def _user_confirm_envelope(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict:
    payload_obj = None
    multi_flag = False
    custom_for_pending = None
    confirm_in = None
    if getattr(args, "payload", None):
        try:
            payload_obj = json.loads(args.payload)
        except json.JSONDecodeError as e:
            raise ValueError(f"payload 须为 JSON 对象: {e}") from e
        if not isinstance(payload_obj, dict):
            raise ValueError("payload 须为 JSON 对象")
    if payload_obj is not None:
        title = (str(payload_obj.get("title") or "").strip() or None)
        c_raw = payload_obj.get("confirms")
        if isinstance(c_raw, list):
            confirms_raw = json.dumps(c_raw, ensure_ascii=False)
        elif isinstance(c_raw, str):
            confirms_raw = c_raw.strip()
        else:
            raise ValueError("payload.confirms 须为非空数组或 JSON 数组字符串")
        ci = payload_obj.get("confirm")
        confirm_in = str(ci).strip() if ci is not None and str(ci).strip() else None
        multi_flag = bool(payload_obj.get("multi"))
        _ci = payload_obj.get("customOptionIndex")
        if _ci is not None:
            if isinstance(_ci, int):
                custom_for_pending = _ci
            elif isinstance(_ci, str) and _ci.strip() != "":
                try:
                    custom_for_pending = int(_ci.strip())
                except ValueError as _e:
                    raise ValueError("customOptionIndex 须为可解析为整数的字符串") from _e
            else:
                raise ValueError("customOptionIndex 须为整数或可解析为整数的非空字符串")
    else:
        title = (args.title or "").strip() or (BUILTIN_TITLE.strip() or None)
        confirms_src = args.confirms if args.confirms is not None else BUILTIN_CONFIRMS_JSON
        confirms_raw = (confirms_src or "").strip()
        confirm_in = (args.confirm or "").strip() or (BUILTIN_CONFIRM.strip() or None)
    builtin_no_cli = not (args.title or args.confirms or args.confirm or getattr(args, "payload", None))
    use_interactive = bool(args.interactive) or (bool(BUILTIN_INTERACTIVE) and builtin_no_cli)

    if not title:
        raise ValueError("必须提供 --payload 或 --title（或 BUILTIN_TITLE）")
    if not confirms_raw:
        raise ValueError("必须提供 payload.confirms 或 --confirms（或 BUILTIN_CONFIRMS_JSON）")
    confirms = _parse_confirms(confirms_raw)

    if custom_for_pending is not None:
        if custom_for_pending < 0 or custom_for_pending >= len(confirms):
            raise ValueError("customOptionIndex 须满足 0 <= customOptionIndex < len(confirms)")

    if payload_obj is not None and "confirm" in payload_obj:
        raw_c = payload_obj.get("confirm")
        resolved = "" if raw_c is None else str(raw_c).strip()
        return {"ok": True, "data": {"confirm": resolved}, "error": None}

    if confirm_in:
        return {"ok": True, "data": {"confirm": confirm_in}, "error": None}

    if sys.stdin.isatty() and use_interactive:
        resolved = _interactive_resolve(title, confirms)
        return {"ok": True, "data": {"confirm": resolved}, "error": None}

    _pending: dict[str, object] = {"title": title, "confirms": confirms}
    if multi_flag:
        _pending["multi"] = True
    if custom_for_pending is not None:
        _pending["customOptionIndex"] = custom_for_pending
    return {
        "ok": False,
        "data": _pending,
        "error": {
            "code": "E_USER_CONFIRM_REQUIRED",
            "type": "UserConfirmRequired",
            "message": "需由宿主展示选项并由用户确认；确认后使用 --confirm 携带用户结论再次调用本工具",
            "hint": "非 TTY 或未加 --interactive 时不读终端。",
            "retryable": False,
        },
    }


def agent_main(
    *,
    payload: str | dict | None = None,
    title: str | None = None,
    confirms: str | list | None = None,
    confirm: str | None = None,
    interactive: bool = False,
) -> dict:
    parser = build_parser()
    if isinstance(payload, dict):
        payload_s = json.dumps(payload, ensure_ascii=False)
    else:
        payload_s = payload
    if isinstance(confirms, list):
        confirms_s = json.dumps(confirms, ensure_ascii=False)
    else:
        confirms_s = confirms
    args = argparse.Namespace(
        payload=payload_s,
        title=title,
        confirms=confirms_s,
        confirm=confirm if confirm is not None else "",
        interactive=interactive,
    )
    try:
        return _user_confirm_envelope(parser, args)
    except ValueError as e:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "E_INVALID_ARGS",
                "type": "ValueError",
                "message": str(e),
                "hint": "检查 --payload / --title / --confirms（JSON 数组字符串）",
                "retryable": False,
            },
        }
    except Exception as e:
        msg = str(e) + "\n\n--help:\n" + _capture_help(parser)
        return {
            "ok": False,
            "data": None,
            "error": {"code": "E_INTERNAL", "type": e.__class__.__name__, "message": msg, "hint": "", "retryable": False},
        }


def main() -> None:
    parser = build_parser()
    try:
        args = parser.parse_args()
        out = _user_confirm_envelope(parser, args)
        _emit(out["ok"], out["data"], out["error"])
        if not out["ok"] and out["error"] and out["error"].get("code") in ("E_INVALID_ARGS", "E_INTERNAL"):
            sys.exit(1)
    except SystemExit:
        raise
    except ValueError as e:
        _emit(
            False,
            None,
            {
                "code": "E_INVALID_ARGS",
                "type": "ValueError",
                "message": str(e),
                "hint": "检查 --payload / --title / --confirms（JSON 数组字符串）",
                "retryable": False,
            },
        )
        sys.exit(1)
    except Exception as e:
        msg = str(e) + "\n\n--help:\n" + _capture_help(parser)
        _emit(
            False,
            None,
            {
                "code": "E_INTERNAL",
                "type": e.__class__.__name__,
                "message": msg,
                "hint": "",
                "retryable": False,
            },
        )
        sys.exit(1)


if __name__ == "__main__":
    main()