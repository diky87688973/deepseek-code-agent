# -*- coding: utf-8 -*-
"""
CLI 测试报告工具
================

用途
----
在仓库根目录运行 pytest，生成 JUnit XML 并解析为统一 JSON，便于 agent 读取失败用例与行号。

说明
----
- 依赖本机 `pytest` 在 PATH 中。
- 通过 `--junitxml=` 写临时文件，解析后删除（可用 `--keepJunit` 保留）。
- 顶层 `ok`：pytest 退出码为 0 且无 failure/error 用例时为 true。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser


BUILTIN_PYTEST_TIMEOUT = 600


def _emit_json(ok: bool, data=None, error=None) -> None:
    print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))


def _parse_junit(path: Path) -> dict:
    tree = ET.parse(str(path))
    root = tree.getroot()
    if root.tag not in ("testsuites", "testsuite"):
        raise ValueError(f"无法识别的 JUnit 根节点: {root.tag}")

    cases_out: list[dict] = []
    for c in root.iter("testcase"):
        classname = c.get("classname") or ""
        name = c.get("name") or ""
        file_attr = c.get("file") or ""
        line_attr = c.get("line")
        line_int = int(line_attr) if line_attr and str(line_attr).isdigit() else None

        status = "passed"
        message = ""
        details = ""
        if c.find("failure") is not None:
            f = c.find("failure")
            status = "failure"
            message = (f.get("message") or "").strip()
            details = (f.text or "").strip()
        elif c.find("error") is not None:
            f = c.find("error")
            status = "error"
            message = (f.get("message") or "").strip()
            details = (f.text or "").strip()
        elif c.find("skipped") is not None:
            status = "skipped"
            sk = c.find("skipped")
            message = (sk.get("message") or "").strip() if sk is not None else ""

        cases_out.append(
            {
                "classname": classname,
                "name": name,
                "file": file_attr,
                "line": line_int,
                "status": status,
                "message": message,
                "details": (details[:8000] if details else ""),
            }
        )

    failures = sum(1 for x in cases_out if x["status"] == "failure")
    errors = sum(1 for x in cases_out if x["status"] == "error")
    skipped = sum(1 for x in cases_out if x["status"] == "skipped")
    tests = len(cases_out)
    logical_ok = failures == 0 and errors == 0

    return {
        "summary": {"tests": tests, "failures": failures, "errors": errors, "skipped": skipped},
        "cases": cases_out,
        "junitLogicalOk": logical_ok,
    }


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="pytest + JUnit 解析为统一 JSON")
    p.add_argument("--root", required=True, help="pytest 工作目录（通常即仓库根）")
    p.add_argument(
        "--pytestArgs",
        default="",
        help='附加 pytest 参数（shell 分词），例如 -q 或 tests/unit',
    )
    p.add_argument("--timeoutSec", type=int, default=BUILTIN_PYTEST_TIMEOUT, help="pytest 超时秒数")
    p.add_argument("--keepJunit", action="store_true", help="保留 junit 文件并在 data.junitPath 返回路径")
    p.add_argument("--jsonOut", action="store_true", help="输出 {ok,data,error} JSON")
    return p


def _test_report_envelope(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict:
    if args.timeoutSec <= 0:
        raise ValueError("timeoutSec 必须 > 0")

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"root 不是目录: {root}")

    pytest_exe = shutil.which("pytest")
    if not pytest_exe:
        return {
            "ok": False,
            "data": {
                "root": str(root),
                "skipped": True,
                "reason": "pytest 不在 PATH",
                "hint": "代码走查/语法与风格请用 cli_unified_diagnose；仅当用户明确要执行测试用例时才需要 pytest 与本工具。",
            },
            "error": {
                "type": "PytestNotFound",
                "message": "未找到 pytest。若只需静态诊断，请改用 cli_unified_diagnose，不必安装 pytest。",
            },
            "_pytest_missing": True,
        }

    fd, junit_tmp = tempfile.mkstemp(suffix=".xml", prefix="pytest_junit_")
    os.close(fd)
    junit_path = Path(junit_tmp)

    try:
        extra = shlex.split(args.pytestArgs.strip(), posix=os.name != "nt") if args.pytestArgs.strip() else []
        cmd = [pytest_exe, f"--junitxml={junit_path}", *extra]
        cp = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeoutSec,
        )

        if not junit_path.exists() or junit_path.stat().st_size == 0:
            raise RuntimeError("未生成 JUnit 文件（pytest 可能未执行到用例或异常退出）")

        parsed = _parse_junit(junit_path)
        logical_ok = bool(parsed.get("junitLogicalOk"))
        exit_ok = cp.returncode == 0
        top_ok = logical_ok and exit_ok

        data = {
            "root": str(root),
            "pytestCommand": " ".join(cmd),
            "pytestExitCode": cp.returncode,
            "pytestStdout": (cp.stdout or "")[-200_000:],
            "pytestStderr": (cp.stderr or "")[-200_000:],
            "junitPath": str(junit_path) if args.keepJunit else None,
            **parsed,
        }

        err = None if top_ok else {"type": "PytestFailed", "message": "存在失败/错误用例或 pytest 非零退出码"}
        return {"ok": top_ok, "data": data, "error": err, "_text_report": _format_pytest_text(cp, parsed)}
    finally:
        if junit_path.exists() and not args.keepJunit:
            try:
                junit_path.unlink()
            except OSError:
                pass


def _format_pytest_text(cp: subprocess.CompletedProcess, parsed: dict) -> list[str]:
    s = parsed["summary"]
    lines = [f"pytest exit={cp.returncode} tests={s['tests']} failures={s['failures']} errors={s['errors']}"]
    for c in parsed["cases"]:
        if c["status"] not in ("passed", "skipped"):
            loc = f"{c.get('file')}:{c.get('line')}" if c.get("file") else ""
            lines.append(f"[{c['status']}] {loc} {c.get('classname')}.{c.get('name')} {c.get('message')}")
    return lines


def agent_main(
    *,
    root: str,
    pytest_args: str = "",
    timeout_sec: int | None = None,
    keep_junit: bool = False,
) -> dict:
    parser = build_parser()
    args = argparse.Namespace(
        root=root,
        pytestArgs=pytest_args,
        timeoutSec=timeout_sec if timeout_sec is not None else BUILTIN_PYTEST_TIMEOUT,
        keepJunit=keep_junit,
        jsonOut=True,
    )
    try:
        out = _test_report_envelope(parser, args)
        out.pop("_text_report", None)
        out.pop("_pytest_missing", None)
        return {"ok": out["ok"], "data": out["data"], "error": out["error"]}
    except Exception as e:
        msg = str(e) + "\n\n--help:\n" + _capture_help(parser)
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": msg}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        out = _test_report_envelope(parser, args)
        text_lines = out.pop("_text_report", None)
        if out.pop("_pytest_missing", False):
            if args.jsonOut:
                _emit_json(out["ok"], data=out["data"], error=out["error"])
            else:
                print(
                    "未找到 pytest。代码走查请用 cli_unified_diagnose。",
                    file=sys.stderr,
                )
            return
        if args.jsonOut:
            _emit_json(out["ok"], data=out["data"], error=out["error"])
        else:
            for line in text_lines or []:
                print(line)
    except Exception as e:
        e.args = (str(e) + "\n\n--help:\n" + _capture_help(parser),)
        if args.jsonOut:
            _emit_json(False, data=None, error={"type": e.__class__.__name__, "message": str(e)})
        else:
            print(str(e), file=sys.stderr)
            raise


if __name__ == "__main__":
    main()
