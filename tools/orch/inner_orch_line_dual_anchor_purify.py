# -*- coding: utf-8 -*-
"""
编排：抽取单行 → 可选字面删除 → 双锚截取 → 可选尾截断（子进程调用 cli_structured_edit / cli_regex_locate）。
供 agent：必须提供 --request-file，或同时提供 --source-file、--line、--out-file；其余可由 request 或命令行补充。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

_TOOLS_ROOT = Path(__file__).resolve().parent.parent
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))
from cli_help_share import _capture_help

_REQUEST_KEYS = frozenset(
    {
        "source_file",
        "line",
        "out_file",
        "encoding",
        "json_out",
        "drop_phrases",
        "left_pattern",
        "right_pattern",
        "ignore_case",
        "multiline",
        "allow_no_left",
        "allow_no_right",
        "left_use_end_of_match",
        "right_bound_is_match_end",
        "tail_cut_pattern",
    }
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inner_orch_common import (  # noqa: E402
    emit,
    run_regex_json,
    run_slice_between_regex,
    run_structured_json,
)


def _defaults() -> dict:
    return {
        "source_file": None,
        "line": None,
        "out_file": None,
        "encoding": "utf-8",
        "json_out": False,
        "drop_phrases": [],
        "left_pattern": None,
        "right_pattern": None,
        "ignore_case": False,
        "multiline": False,
        "allow_no_left": False,
        "allow_no_right": False,
        "left_use_end_of_match": False,
        "right_bound_is_match_end": False,
        "tail_cut_pattern": None,
    }


def _load_request(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(raw, dict):
        raise ValueError("request-file 根必须是 JSON 对象")
    bad = set(raw) - _REQUEST_KEYS
    if bad:
        raise ValueError(f"request-file 未知字段: {sorted(bad)}")
    return raw


def _merge_cfg(args: argparse.Namespace) -> dict:
    cfg = _defaults()
    if args.request_file:
        cfg.update(_load_request(Path(args.request_file)))
    if args.source_file is not None:
        cfg["source_file"] = args.source_file
    if args.line is not None:
        cfg["line"] = int(args.line)
    if args.out_file is not None:
        cfg["out_file"] = args.out_file
    if args.encoding is not None:
        cfg["encoding"] = args.encoding
    if args.drop_phrase is not None:
        cfg["drop_phrases"] = [str(x) for x in args.drop_phrase if str(x)]
    if args.left_pattern is not None:
        cfg["left_pattern"] = args.left_pattern
    if args.right_pattern is not None:
        cfg["right_pattern"] = args.right_pattern
    if args.tail_cut_pattern is not None:
        v = args.tail_cut_pattern.strip()
        cfg["tail_cut_pattern"] = v if v else None
    if args.ignore_case:
        cfg["ignore_case"] = True
    if args.multiline:
        cfg["multiline"] = True
    if args.allow_no_left:
        cfg["allow_no_left"] = True
    if args.allow_no_right:
        cfg["allow_no_right"] = True
    if args.left_use_end_of_match:
        cfg["left_use_end_of_match"] = True
    if args.right_bound_is_match_end:
        cfg["right_bound_is_match_end"] = True
    if args.json_out:
        cfg["json_out"] = True
    return cfg


def _validate(cfg: dict) -> None:
    if not cfg.get("source_file") or not str(cfg["source_file"]).strip():
        raise ValueError("缺少 source_file（--request-file 或 --source-file）")
    if cfg.get("line") is None:
        raise ValueError("缺少 line（--request-file 或 --line）")
    if not cfg.get("out_file") or not str(cfg["out_file"]).strip():
        raise ValueError("缺少 out_file（--request-file 或 --out-file）")


def _run_pipeline(cfg: dict) -> None:
    json_out = bool(cfg["json_out"])
    steps: list[str] = []
    ln = int(cfg["line"])
    out_final = Path(cfg["out_file"])
    out_final.parent.mkdir(parents=True, exist_ok=True)
    enc = str(cfg["encoding"])

    d = tempfile.mkdtemp(prefix="orch_line_", dir=str(out_final.parent))
    try:
        p_line = Path(d) / "_line.txt"
        pl0 = {
            "type": "extract",
            "mode": "lines",
            "startLine": ln,
            "endLine": ln,
            "outFile": str(p_line),
        }
        r0 = run_structured_json(payload=pl0, file=cfg["source_file"], encoding=enc)
        steps.append("extract_line")
        if not r0.get("ok"):
            emit(False, {"steps": steps, "last": r0}, r0.get("error"), json_out=json_out)
            sys.exit(1)

        cur = p_line
        phrases = [str(x) for x in (cfg.get("drop_phrases") or []) if str(x)]
        if phrases:
            p_drop = Path(d) / "_after_drop.txt"
            r1 = run_structured_json(
                payload={
                    "type": "delete_segments",
                    "dropPhrases": phrases,
                    "outFile": str(p_drop),
                },
                file=str(cur),
                encoding=enc,
            )
            steps.append("drop_phrases")
            if not r1.get("ok"):
                emit(False, {"steps": steps, "last": r1}, r1.get("error"), json_out=json_out)
                sys.exit(1)
            cur = p_drop

        p_mid = Path(d) / "_slice.txt"
        lp = cfg.get("left_pattern")
        rp = cfg.get("right_pattern")
        left = str(lp) if lp else None
        right = str(rp) if rp else None
        r2 = run_slice_between_regex(
            source_file=cur,
            out_file=p_mid,
            left_pattern=left,
            right_pattern=right,
            encoding=enc,
            ignore_case=bool(cfg.get("ignore_case")),
            multiline=bool(cfg.get("multiline")),
            allow_no_left=bool(cfg.get("allow_no_left")),
            allow_no_right=bool(cfg.get("allow_no_right")),
            left_use_end_of_match=bool(cfg.get("left_use_end_of_match")),
            right_bound_is_match_end=bool(cfg.get("right_bound_is_match_end")),
        )
        steps.append("slice_between")
        if not r2.get("ok"):
            emit(False, {"steps": steps, "last": r2}, r2.get("error"), json_out=json_out)
            sys.exit(1)

        cur = p_mid
        tail_pat = cfg.get("tail_cut_pattern")
        if tail_pat and str(tail_pat).strip():
            rj = run_regex_json(
                target=cur,
                pattern=str(tail_pat),
                encoding=enc,
                ignore_case=bool(cfg.get("ignore_case")),
                multiline=bool(cfg.get("multiline")),
            )
            steps.append("tail_regex")
            if not rj.get("ok"):
                emit(False, {"steps": steps, "last": rj}, rj.get("error"), json_out=json_out)
                sys.exit(1)
            items = (rj.get("data") or {}).get("items") or []
            if not items:
                emit(
                    False,
                    {"steps": steps},
                    {"type": "ValueError", "message": "尾截断正则未命中"},
                    json_out=json_out,
                )
                sys.exit(1)
            ts = int(items[0]["start"])
            r3 = run_structured_json(
                payload={
                    "type": "extract",
                    "mode": "offsets",
                    "start": 0,
                    "end": ts,
                    "outFile": str(out_final),
                },
                file=str(cur),
                encoding=enc,
            )
            steps.append("tail_cut")
            if not r3.get("ok"):
                emit(False, {"steps": steps, "last": r3}, r3.get("error"), json_out=json_out)
                sys.exit(1)
        else:
            out_final.write_text(
                cur.read_text(encoding=enc, errors="replace"),
                encoding=enc,
                errors="replace",
            )
            steps.append("copy_final")

        emit(True, {"steps": steps, "out_file": str(out_final)}, None, json_out=json_out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="编排：行号 + 字面清理 + 双锚 + 可选尾截断（agent：--request-file 或必填路径参数）"
    )
    p.add_argument("--request-file", help="JSON 配置，字段见仓库 示例_request_行号双锚.json")
    p.add_argument("--source-file", help="源文本文件")
    p.add_argument("--line", type=int, help="行号（1-based）")
    p.add_argument("--out-file", help="输出路径")
    p.add_argument("--encoding", default=None, help="编码；省略则用 request 或 utf-8")
    p.add_argument("--drop-phrase", action="append", default=None, dest="drop_phrase", metavar="T")
    p.add_argument("--left-pattern", default=None)
    p.add_argument("--right-pattern", default=None)
    p.add_argument("--tail-cut-pattern", default=None)
    p.add_argument("--ignore-case", action="store_true")
    p.add_argument("--multiline", action="store_true")
    p.add_argument("--allow-no-left", action="store_true")
    p.add_argument("--allow-no-right", action="store_true")
    p.add_argument("--left-use-end-of-match", action="store_true")
    p.add_argument("--right-bound-is-match-end", action="store_true")
    p.add_argument("--json-out", action="store_true")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.request_file and (args.source_file is None or args.line is None or args.out_file is None):
        print(
            "必须提供 --request-file，或同时提供 --source-file、--line、--out-file",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        cfg = _merge_cfg(args)
        _validate(cfg)
        _run_pipeline(cfg)
    except Exception as e:
        e.args = (str(e) + "\n\n--help:\n" + _capture_help(parser),)
        emit(False, None, {"type": e.__class__.__name__, "message": str(e)}, json_out=bool(args.json_out))
        sys.exit(1)


if __name__ == "__main__":
    main()
