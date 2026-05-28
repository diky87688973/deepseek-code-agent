#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layer 0 一键门禁：回归文档要求 Agent 读文档后必须执行本脚本（或等价四条命令）。

用法（仓库根目录）：
  python scripts/run_layer0.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STEPS = (
    ("0.1 unittest", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
    ("0.2 check_symbols", [sys.executable, str(ROOT / "agent_v3" / "check_symbols.py")]),
    ("0.3 health", [sys.executable, str(ROOT / "scripts" / "check_agent_v3_health.py")]),
    (
        "0.4 compileall",
        [sys.executable, "-m", "compileall", "agent_v3", "tools", "util", "-q"],
    ),
)


def main() -> int:
    print("=== Layer 0 自动化门禁（agent_v3 全栈）===\n")
    failed = False
    for label, cmd in STEPS:
        print(f"[RUN] {label}")
        print(f"      {' '.join(cmd)}")
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print(f"[FAIL] {label} exit={r.returncode}\n")
            failed = True
            break
        print(f"[OK]   {label}\n")
    if failed:
        print("Layer 0: FAILED — 停止，勿进入 Layer 1")
        return 1
    print("Layer 0: OK — 可进入 Layer 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
