# -*- coding: utf-8 -*-
"""
编排 Agent：按 spec 或 preset 调度 `工具库/文本工具` 下 CLI（含编排脚本），子进程继承标准输出/错误输出，
便于在 Cursor 终端里看到过程；在可识别为 `cli_structured_edit` + `replace_range` / `replace_literal` 的步骤前，
自动插入「区间说明 + unified diff」预览（调用 `cli_text_diff.py`），减少人工 review。
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import os
import subprocess
import tempfile

from cli_help_share import _capture_help


def _tools_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _orch_line_script() -> Path:
    return Path(__file__).resolve().parent / "inner_orch_line_dual_anchor_purify.py"


def _cli(name: str) -> Path:
    return _tools_dir() / name


def _parse_kv_argv(argv: list[str], key: str) -> str | None:
    try:
        i = argv.index(key)
    except ValueError:
        return None
    if i + 1 >= len(argv):
        return None
    return argv[i + 1]


def _run_structured_capture(*, file_path: str, payload: dict, encoding: str) -> dict:
    """进程内调用 cli_structured_edit，不启动子进程"""
    from inner_orch_common import run_structured_json
    return run_structured_json(payload=payload, file=file_path, encoding=encoding)




def _read_full_text_via_cli(file_path: str, encoding: str) -> str:
    r = _run_structured_capture(
        file_path=file_path,
        payload={"type": "extract", "mode": "offsets", "start": 0, "end": -1},
        encoding=encoding,
    )
    if not r.get("ok"):
        raise RuntimeError(f"extract 全文件失败: {r.get('error')}")
    return str((r.get("data") or {}).get("text", ""))


def _emit_diff(left: str, right: str, *, encoding: str, context: int, title: str) -> None:
    """进程内调用 cli_text_diff，不启动子进程"""
    from inner_orch_common import _run_tool_inline
    import tempfile, shutil
    d = tempfile.mkdtemp(prefix="orch_agent_diff_")
    try:
        lp = Path(d) / "left.txt"
        rp = Path(d) / "right.txt"
        lp.write_text(left, encoding="utf-8", errors="replace")
        rp.write_text(right, encoding="utf-8", errors="replace")
        diff_cli = str(_cli("cli_text_diff.py"))
        cmd = [
            sys.executable,
            diff_cli,
            "--leftFile",
            str(lp),
            "--rightFile",
            str(rp),
            "--encoding",
            encoding,
            "--context",
            str(context),
        ]
        print(f"\n{'=' * 72}\n{title}\n{'=' * 72}", flush=True)
        _run_tool_inline(cmd)
    finally:
        shutil.rmtree(d, ignore_errors=True)


        try:
            os.rmdir(d)
        except OSError:
            pass


def maybe_preview_structured_edit(
    argv: list[str],
    *,
    encoding_default: str,
    diff_context: int,
) -> None:
    """若 argv 为 cli_structured_edit + --payloadFile，且 payload 为 replace_range / replace_literal，则打印预览。"""
    joined = " ".join(argv).lower()
    if "cli_structured_edit" not in joined and "cli_structured_edit.py" not in joined:
        return
    fp = _parse_kv_argv(argv, "--file")
    pp = _parse_kv_argv(argv, "--payloadFile")
    if not fp or not pp:
        return
    ppath = Path(pp)
    if not ppath.is_file():
        return
    enc = _parse_kv_argv(argv, "--encoding") or encoding_default
    try:
        pl = json.loads(ppath.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return
    if not isinstance(pl, dict):
        return
    t = pl.get("type")
    if t == "replace_range":
        s = int(pl["start"])
        e = int(pl["end"])
        new_t = str(pl.get("text", ""))
        r = _run_structured_capture(
            file_path=fp,
            payload={"type": "extract", "mode": "offsets", "start": s, "end": e},
            encoding=enc,
        )
        if not r.get("ok"):
            print(f"[编排_agent] 预览 extract 失败: {r.get('error')}", flush=True, file=sys.stderr)
            return
        old_slice = (r.get("data") or {}).get("text", "")
        full = _read_full_text_via_cli(fp, enc)
        merged = full[:s] + new_t + full[e:]
        _emit_diff(
            full,
            merged,
            encoding=enc,
            context=diff_context,
            title=f"replace_range 全文件预览 diff（区间 [{s},{e}) 将被替换）",
        )
        _emit_diff(
            old_slice,
            new_t,
            encoding=enc,
            context=diff_context,
            title=f"replace_range 区间内旧文 vs 新文（[{s},{e})）",
        )
    elif t == "replace_literal":
        old_t = str(pl.get("oldText", ""))
        new_t = str(pl.get("newText", ""))
        full = _read_full_text_via_cli(fp, enc)
        cnt = pl.get("count", -1)
        if cnt == 0:
            return
        if cnt < 0:
            after = full.replace(old_t, new_t)
        else:
            after = full.replace(old_t, new_t, int(cnt))
        _emit_diff(
            full,
            after,
            encoding=enc,
            context=diff_context,
            title="replace_literal 全文件预览 diff（字面替换）",
        )
        _emit_diff(old_t, new_t, encoding=enc, context=3, title="replace_literal 旧字面 vs 新字面")


def expand_preset(name: str, params: dict) -> list[dict]:
    name = (name or "").strip()
    if name == "line_dual_anchor":
        rf = params.get("request_file")
        if not rf:
            raise ValueError("preset line_dual_anchor 需要 params.request_file")
        exe = sys.executable  # kept for cmd structure; actual execution uses _run_tool_inline
        script = str(_orch_line_script())
        cmd = [exe, script, "--request-file", str(rf)]
        if params.get("json_out"):
            cmd.append("--json-out")
        return [{"label": "preset:line_dual_anchor", "run": cmd, "preview_structured": False}]
    raise ValueError(f"未知 preset: {name}")


def load_spec(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return json.loads(raw)


def run_steps(spec: dict) -> int:
    verbose = bool(spec.get("verbose", True))
    diff_ctx = int(spec.get("diff_context", 3))
    enc_def = str(spec.get("encoding_default", "utf-8"))
    no_preview_glob = bool(spec.get("no_preview", False))

    if "preset" in spec:
        steps = expand_preset(str(spec["preset"]), dict(spec.get("params") or {}))
    else:
        steps = list(spec.get("steps") or [])
        if not steps:
            raise ValueError("spec 缺少 steps 或 preset")

    for i, step in enumerate(steps):
        label = str(step.get("label", f"step_{i}"))
        argv = step.get("run") or step.get("argv")
        if not argv or not isinstance(argv, list):
            raise ValueError(f"step {i} 缺少 run/argv 数组")
        argv = [str(x) for x in argv]
        if verbose:
            print(f"\n{'#' * 72}\n# [{i}] {label}\n# 命令: {' '.join(argv)}\n{'#' * 72}\n", flush=True)
        prev = not no_preview_glob and not step.get("no_preview", False) and step.get("preview_structured", True)
        if prev:
            maybe_preview_structured_edit(argv, encoding_default=enc_def, diff_context=diff_ctx)
        from inner_orch_common import _run_tool_inline
        try:
            _run_tool_inline(argv)
            code = 0
        except Exception as e:
            print(f"[\u7f16\u6392_agent] \u6b65\u9aa4 {i} \u5185\u8054调用失败: {e}", flush=True, file=sys.stderr)
            code = 1
        if code != 0:
            print(f"[编排_agent] 步骤 {i} 退出码 {code}", flush=True, file=sys.stderr)
            return code
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="编排 Agent：按 spec/preset 调度工具库 CLI，stdout 直通")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--spec-file", help="JSON 编排说明，见同目录 示例_spec_编排agent.json")
    g.add_argument("--preset", help="内置预设名，如 line_dual_anchor")
    p.add_argument("--params-file", help="与 --preset 同用：JSON 对象作为 params")
    p.add_argument("--no-verbose", action="store_true", help="少打印步骤横幅")
    p.add_argument("--no-preview", action="store_true", help="全局关闭结构化写盘前 diff 预览")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.spec_file:
            spec = load_spec(Path(args.spec_file))
        else:
            if not args.params_file:
                print("与 --preset 同时需要 --params-file", file=sys.stderr)
                return 2
            params = json.loads(Path(args.params_file).read_text(encoding="utf-8", errors="replace"))
            if not isinstance(params, dict):
                raise ValueError("params-file 根须为 JSON 对象")
            spec = {"preset": args.preset, "params": params}
        if args.no_verbose:
            spec["verbose"] = False
        if args.no_preview:
            spec["no_preview"] = True
        return run_steps(spec)
    except Exception as e:
        e.args = (str(e) + "\n\n--help:\n" + _capture_help(parser),)
        print(json.dumps({"ok": False, "error": {"type": e.__class__.__name__, "message": str(e)}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
