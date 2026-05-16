# -*- coding: utf-8 -*-
"""用户分岔确认：仅 Agent 宿主驱动 UI。进程内入口 agent_main（扁平参数）。"""

from __future__ import annotations

import json
import re
import sys
from typing import List, Optional

import agent_common as ac

_PENDING_ERROR: dict = {
    "code": "E_USER_CONFIRM_REQUIRED",
    "type": "UserConfirmRequired",
    "message": "需由宿主展示选项并由用户确认；确认后携带 confirm 再次调用",
    "hint": "解析 data 中 title、confirms 渲染 UI；回填 confirm",
    "retryable": False,
}


def _interactive_resolve(title: str, confirms: list[str]) -> str:
    print(title, file=sys.stderr)
    for i, c in enumerate(confirms, start=1):
        print(f"  [{i}] {c}", file=sys.stderr)
    print("输入编号或自由输入：", file=sys.stderr)
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


def _normalize_confirms(raw: object) -> list[str]:
    if not isinstance(raw, list) or len(raw) < 1:
        raise ValueError("confirms 须为非空字符串数组")
    out: list[str] = []
    for i, x in enumerate(raw):
        if not isinstance(x, str) or not x.strip():
            raise ValueError(f"confirms[{i}] 须为非空字符串")
        out.append(x)
    return out


def agent_main(
    *,
    title: str = "",
    confirms: Optional[List[str]] = None,
    confirm: Optional[str] = None,
    multi: bool = False,
    custom_option_index: Optional[int] = None,
    interactive: bool = False,
    confirm_id: str = "",
) -> dict:
    try:
        if confirm is not None:
            # 有待确认的 confirm_id 时自动标记
            if confirm_id:
                try:
                    from agent_v2.live_state import kling_mark_confirmed
                    kling_mark_confirmed(confirm_id)
                except Exception:
                    pass
            _result = {"confirm": str(confirm)}
            if confirm_id:
                _result["confirm_id"] = confirm_id
            return ac.ok(_result)

        if confirms is None:
            raise ValueError("须提供 confirms（非空字符串数组）")
        cl = _normalize_confirms(confirms)

        if custom_option_index is not None:
            cix = int(custom_option_index)
            if cix < 0 or cix >= len(cl):
                raise ValueError("custom_option_index 须满足 0 <= i < len(confirms)")

        t = (title or "").strip()
        if not t:
            raise ValueError("首次发起须提供 title")

        if sys.stdin.isatty() and interactive:
            resolved = _interactive_resolve(t, cl)
            return ac.ok({"confirm": resolved})

        pending: dict[str, object] = {"title": t, "confirms": cl}
        if multi:
            pending["multi"] = True
        if custom_option_index is not None:
            pending["custom_option_index"] = int(custom_option_index)
        return {"ok": False, "data": pending, "error": dict(_PENDING_ERROR)}
    except Exception as e:
        return ac.err(e)


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="user_confirm（本地调试；宿主用 agent_main）")
    p.add_argument("--title", default="", help="标题")
    p.add_argument(
        "--confirms",
        required=True,
        help="JSON 数组字符串",
    )
    p.add_argument("--confirm", default=None)
    p.add_argument("--multi", action="store_true")
    p.add_argument("--custom_option_index", type=int, default=None)
    p.add_argument("--interactive", action="store_true")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    try:
        cl = _normalize_confirms(json.loads(args.confirms))
    except (json.JSONDecodeError, ValueError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    r = agent_main(
        title=args.title,
        confirms=cl,
        confirm=args.confirm,
        multi=bool(args.multi),
        custom_option_index=args.custom_option_index,
        interactive=bool(args.interactive),
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok") and isinstance(r.get("data"), dict):
            print(r["data"].get("confirm", ""), end="")
        else:
            print((r.get("error") or {}).get("message", ""), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
