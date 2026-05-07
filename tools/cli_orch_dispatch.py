# -*- coding: utf-8 -*-
"""
编排调度入口：将 orch/inner_orch_agent.py 暴露为标准 cli_* 工具，便于 LLM 作为 function tool 调用。
- 对写操作自动补充预览步骤（只读步骤不补）
- 输出结构化变更卡片（编辑/新增/删除）供侧栏与对话框渲染
"""

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import subprocess
from cli_help_share import _capture_help, _HelpFulParser
import sys
import tempfile
import threading
from pathlib import Path

MUTATE_TYPES = {"append", "append_line", "insert", "replace_range", "replace_literal", "delete_segments"}
_ORCH_INLINE_LOCK = threading.RLock()


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="调度 orch_agent 进行多步编排执行")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--specFile", help="编排 spec JSON 文件")
    g.add_argument("--preset", help="内置预设名（如 line_dual_anchor）")
    p.add_argument("--paramsJson", help="与 --preset 同用：JSON 对象字符串")
    p.add_argument("--paramsFile", help="与 --preset 同用：JSON 文件")
    p.add_argument("--noVerbose", action="store_true", help="少打印步骤横幅")
    p.add_argument("--noPreview", action="store_true", help="关闭结构化写盘前 diff 预览")
    p.add_argument("--jsonOut", action="store_true", help="输出统一 JSON")
    return p


def _emit(ok: bool, data: dict, error: dict | None) -> None:
    print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))


def _clip(s: str, limit: int = 6000) -> str:
    t = str(s or "")
    return t if len(t) <= limit else t[:limit] + "\n…"


def _parse_payload_from_argv(argv: list[str]) -> dict | None:
    for i, a in enumerate(argv):
        if a == "--payload" and i + 1 < len(argv):
            try:
                o = json.loads(argv[i + 1])
                return o if isinstance(o, dict) else None
            except Exception:
                return None
        if a == "--payloadFile" and i + 1 < len(argv):
            try:
                o = json.loads(Path(argv[i + 1]).read_text(encoding="utf-8", errors="replace"))
                return o if isinstance(o, dict) else None
            except Exception:
                return None
    return None


def _get_flag(argv: list[str], name: str) -> str:
    try:
        i = argv.index(name)
    except ValueError:
        return ""
    return argv[i + 1] if i + 1 < len(argv) else ""


def _collect_write_targets_from_spec(spec: dict) -> list[dict]:
    out: list[dict] = []
    for idx, step in enumerate(list(spec.get("steps") or [])):
        run = step.get("run") or step.get("argv")
        if not isinstance(run, list) or not run:
            continue
        argv = [str(x) for x in run]
        joined = " ".join(argv).lower()
        if "cli_structured_edit" in joined:
            payload = _parse_payload_from_argv(argv)
            f = _get_flag(argv, "--file")
            if isinstance(payload, dict) and str(payload.get("type") or "").lower() in MUTATE_TYPES and f:
                out.append({"kind": "file", "path": f, "label": f"step_{idx}:structured_edit"})
        elif "cli_file_ops" in joined:
            src = _get_flag(argv, "--source")
            dst = _get_flag(argv, "--dest")
            action = _get_flag(argv, "--action").lower()
            if action == "delete" and src:
                out.append({"kind": "file", "path": src, "label": f"step_{idx}:file_ops_delete"})
            elif action in {"copy", "move", "rename"}:
                if src:
                    out.append({"kind": "file", "path": src, "label": f"step_{idx}:file_ops_src"})
                if dst:
                    out.append({"kind": "file", "path": dst, "label": f"step_{idx}:file_ops_dst"})
    dedup = {}
    for x in out:
        dedup[str(Path(x["path"]))] = x
    return list(dedup.values())


def _snap_file(path: str) -> dict:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return {"exists": False, "text": ""}
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        txt = ""
    return {"exists": True, "text": txt}


def _make_cards(before: dict[str, dict], after: dict[str, dict]) -> list[dict]:
    cards: list[dict] = []
    for path, b in before.items():
        a = after.get(path, {"exists": False, "text": ""})
        if not b.get("exists") and a.get("exists"):
            cards.append({
                "kind": "create",
                "file": path,
                "title": "新增文件",
                "previews": [{"title": "新增内容", "text": _clip(a.get("text", ""))}],
            })
        elif b.get("exists") and not a.get("exists"):
            cards.append({
                "kind": "delete",
                "file": path,
                "title": "删除文件",
                "previews": [{"title": "删除前内容", "text": _clip(b.get("text", ""))}],
            })
        elif b.get("exists") and a.get("exists") and b.get("text") != a.get("text"):
            cards.append({
                "kind": "edit",
                "file": path,
                "title": "编辑文件",
                "previews": [
                    {"title": "变更前", "text": _clip(b.get("text", ""))},
                    {"title": "变更后", "text": _clip(a.get("text", ""))},
                ],
            })
    return cards


def _augment_spec_with_preview_steps(spec: dict, tools_dir: Path) -> tuple[dict, int]:
    steps = list(spec.get("steps") or [])
    if not steps:
        return spec, 0
    exe = sys.executable
    prev_cli = str(tools_dir / "cli_preview_render.py")
    out_steps = []
    injected = 0
    for i, step in enumerate(steps):
        out_steps.append(step)
        run = step.get("run") or step.get("argv")
        if not isinstance(run, list) or not run:
            continue
        argv = [str(x) for x in run]
        joined = " ".join(argv).lower()

        # structured_edit write => preview after write
        if "cli_structured_edit" in joined:
            payload = _parse_payload_from_argv(argv)
            f = _get_flag(argv, "--file")
            if isinstance(payload, dict) and str(payload.get("type") or "").lower() in MUTATE_TYPES and f:
                out_steps.append({
                    "label": f"auto_preview_after_write_{i}",
                    "preview_structured": False,
                    "run": [exe, prev_cli, "--file", f, "--label", "编排写后预览", "--jsonOut"],
                })
                injected += 1
            continue

        # file_ops delete => preview before delete; create/edit ops => preview after
        if "cli_file_ops" in joined:
            action = _get_flag(argv, "--action").lower()
            src = _get_flag(argv, "--source")
            dst = _get_flag(argv, "--dest")
            if action == "delete" and src:
                out_steps.insert(len(out_steps)-1, {
                    "label": f"auto_preview_before_delete_{i}",
                    "preview_structured": False,
                    "run": [exe, prev_cli, "--file", src, "--label", "编排删前预览", "--jsonOut"],
                })
                injected += 1
            elif action in {"copy", "move", "rename"} and dst:
                out_steps.append({
                    "label": f"auto_preview_after_fileops_{i}",
                    "preview_structured": False,
                    "run": [exe, prev_cli, "--file", dst, "--label", "编排写后预览", "--jsonOut"],
                })
                injected += 1

    spec2 = dict(spec)
    spec2["steps"] = out_steps
    return spec2, injected


def _load_spec_for_snapshot(args: argparse.Namespace, orch_dir: Path) -> tuple[dict | None, str | None, str | None]:
    if args.specFile:
        try:
            spec = json.loads(Path(args.specFile).read_text(encoding="utf-8", errors="replace"))
            if isinstance(spec, dict):
                return spec, None, None
        except Exception:
            return None, None, None
    if not args.preset:
        return None, None, None
    if args.paramsFile:
        try:
            params = json.loads(Path(args.paramsFile).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            params = None
    elif args.paramsJson:
        try:
            params = json.loads(args.paramsJson)
        except Exception:
            params = None
    else:
        params = None
    if not isinstance(params, dict):
        return None, None, None
    if args.preset == "line_dual_anchor":
        req = str(params.get("request_file") or "")
        if req:
            try:
                req_obj = json.loads(Path(req).read_text(encoding="utf-8", errors="replace"))
                out_file = str((req_obj or {}).get("out_file") or "")
                if out_file:
                    return {"steps": [{"run": [sys.executable, str(orch_dir / "inner_orch_line_dual_anchor_purify.py"), "--request-file", req]}]}, out_file, req
            except Exception:
                return None, None, None
    return None, None, None


def _orch_dispatch_envelope(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict:
    tools_dir = Path(__file__).resolve().parent
    orch = tools_dir / "orch" / "inner_orch_agent.py"
    if not orch.exists():
        return {"ok": False, "data": {}, "error": {"type": "ToolNotFound", "message": f"missing {orch}"}}

    cmd = [sys.executable, str(orch)]
    tmp_params_path = None
    tmp_spec_path = None

    spec_for_snap, out_file_hint, req_hint = _load_spec_for_snapshot(args, tools_dir / "orch")

    injected = 0
    if args.specFile:
        spec_raw = json.loads(Path(args.specFile).read_text(encoding="utf-8", errors="replace"))
        if isinstance(spec_raw, dict):
            spec_aug, injected = _augment_spec_with_preview_steps(spec_raw, tools_dir)
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".json") as tf:
                tf.write(json.dumps(spec_aug, ensure_ascii=False, indent=2))
                tmp_spec_path = tf.name
            cmd += ["--spec-file", tmp_spec_path]
        else:
            cmd += ["--spec-file", str(args.specFile)]
    else:
        cmd += ["--preset", str(args.preset)]
        if args.paramsFile:
            cmd += ["--params-file", str(args.paramsFile)]
        elif args.paramsJson:
            try:
                obj = json.loads(args.paramsJson)
                if not isinstance(obj, dict):
                    raise ValueError("paramsJson must be JSON object")
            except Exception as e:
                e.args = (str(e) + "\n\n--help:\n" + _capture_help(parser),)
                return {"ok": False, "data": {}, "error": {"type": "ValueError", "message": f"invalid paramsJson: {e}"}}
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".json") as tf:
                tf.write(json.dumps(obj, ensure_ascii=False, indent=2))
                tmp_params_path = tf.name
            cmd += ["--params-file", tmp_params_path]

    if args.noVerbose:
        cmd.append("--no-verbose")
    if args.noPreview:
        cmd.append("--no-preview")

    write_targets = _collect_write_targets_from_spec(spec_for_snap or {}) if spec_for_snap else []
    if out_file_hint:
        write_targets.append({"kind": "file", "path": out_file_hint, "label": "preset_out_file"})
    ded: dict[str, dict] = {}
    for x in write_targets:
        ded[str(Path(x["path"]))] = x
    write_targets = list(ded.values())

    before = {str(Path(x["path"])): _snap_file(x["path"]) for x in write_targets}

    try:
        # 进程内调用 inner_orch_agent，替代 subprocess.run
        import importlib as _il, io as _io
        orch_mod = _il.import_module("orch.inner_orch_agent")
        with _ORCH_INLINE_LOCK:
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            old_argv = sys.argv
            captured_out = _io.StringIO()
            captured_err = _io.StringIO()
            try:
                sys.stdout = captured_out
                sys.stderr = captured_err
                sys.argv = [str(orch)]
                exit_code = orch_mod.main()
            except SystemExit as _se:
                exit_code = _se.code if isinstance(_se.code, int) else 1
            except Exception as _e:
                captured_err.write(f"\n{type(_e).__name__}: {_e}")
                exit_code = 1
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
                sys.argv = old_argv
        after = {k: _snap_file(k) for k in before.keys()}
        cards = _make_cards(before, after)
        data = {
            "exitCode": exit_code,
            "stdout": captured_out.getvalue(),
            "stderr": captured_err.getvalue(),
            "command": cmd,
            "cards": cards,
            "writeTargets": list(before.keys()),
            "injectedPreviewSteps": injected,
        }
        if req_hint:
            data["requestFile"] = req_hint
        if exit_code == 0:
            return {"ok": True, "data": data, "error": None}
        return {"ok": False, "data": data, "error": {"type": "CommandError", "message": "orch_agent failed", "exitCode": exit_code}}
    except Exception as e:
        e.args = (str(e) + "\n\n--help:\n" + _capture_help(parser),)
        return {"ok": False, "data": {"command": cmd}, "error": {"type": e.__class__.__name__, "message": str(e)}}
    finally:
        for t in (tmp_params_path, tmp_spec_path):
            if t:
                try:
                    Path(t).unlink(missing_ok=True)
                except OSError:
                    pass


def agent_main(
    *,
    spec_file: str | None = None,
    preset: str | None = None,
    params_json: str | None = None,
    params_file: str | None = None,
    no_verbose: bool = False,
    no_preview: bool = False,
) -> dict:
    parser = build_parser()
    has_spec = bool(spec_file and str(spec_file).strip())
    has_pre = bool(preset and str(preset).strip())
    if has_spec == has_pre:
        return {
            "ok": False,
            "data": {},
            "error": {"type": "ValueError", "message": "须且仅能指定 spec_file 或 preset 之一"},
        }
    args = argparse.Namespace(
        specFile=spec_file,
        preset=preset,
        paramsJson=params_json,
        paramsFile=params_file,
        noVerbose=no_verbose,
        noPreview=no_preview,
        jsonOut=True,
    )
    return _orch_dispatch_envelope(parser, args)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    out = _orch_dispatch_envelope(parser, args)
    _emit(out["ok"], out["data"], out["error"])


if __name__ == "__main__":
    main()
