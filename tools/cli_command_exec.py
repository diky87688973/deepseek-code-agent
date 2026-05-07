# -*- coding: utf-8 -*-
"""
CLI 命令执行工具
================

用途
----
在本地执行 shell 命令，捕获 stdout/stderr，以统一 JSON 输出。
适合 test / lint / build 或任意可脚本化命令；供 agent 或其它编排：命令字符串 -> 退出码 -> 结构化结果。
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
from typing import Optional, Union, Tuple
import json
import os
import re
import shutil
import subprocess

from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser


_SAFE_BLOCK_RE = re.compile(r"[;&|`$><]")

# ── 命令黑名单：真删除/毁灭性操作 ──
# 无论 safeMode=true/false，均拦截。用户应改用 cli_file_ops（逻辑删除移至回收站）。
_CMD_BLACKLIST: set[str] = {
    # Windows CMD
    "del", "erase",
    "rmdir", "rd",
    "deltree",
    # PowerShell
    "remove-item", "ri",
    "remove-itemproperty", "rp",
    "clear-item", "cli",
    # Unix / Git Bash / WSL
    "rm", "rmdir", "unlink",
    "shred", "wipe", "srm",
    # 格式化 / 分区 / 磁盘覆写
    "format", "mkfs", "fdisk", "mkswap",
    "dd", "diskpart",
    # 注册表破坏
    "reg", "regdelete", "reg delete",
    # 权限/ACL 修改（防止绕过 AGENT_ROOT 只读锁）
    "icacls", "cacls", "xcacls",
    "takeown", "attrib",
    "subinacl", "setacl",
    "powershell set-acl",
}

# 映射：黑命名单命令 → 建议替代工具
_BLACKLIST_ADVICE: dict[str, str] = {
    "del": "请用 cli_file_ops action=delete（移动到回收站）",
    "erase": "请用 cli_file_ops action=delete（移动到回收站）",
    "rmdir": "请用 cli_file_ops action=delete（移动到回收站）",
    "rd": "请用 cli_file_ops action=delete（移动到回收站）",
    "deltree": "请用 cli_file_ops action=delete（移动到回收站）",
    "remove-item": "请用 cli_file_ops action=delete（移动到回收站）",
    "ri": "请用 cli_file_ops action=delete（移动到回收站）",
    "rm": "请用 cli_file_ops action=delete（移动到回收站）",
    "unlink": "请用 cli_file_ops action=delete（移动到回收站）",
    "shred": "请用 cli_file_ops action=delete（移动到回收站）",
    "wipe": "请用 cli_file_ops action=delete（移动到回收站）",
    "srm": "请用 cli_file_ops action=delete（移动到回收站）",
    "format": "禁止在命令中格式化磁盘，请用系统磁盘管理工具",
    "mkfs": "禁止在命令中格式化文件系统",
    "fdisk": "禁止在命令中操作分区表",
    "mkswap": "禁止在命令中创建交换分区",
    "dd": "禁止在命令中使用磁盘覆写工具",
    "diskpart": "禁止在命令中操作磁盘分区",
    "reg": "禁止在命令中直接操作注册表",
    "regdelete": "禁止在命令中直接操作注册表",
    "reg delete": "禁止在命令中直接操作注册表",
    "icacls": "禁止在命令中修改 ACL 权限（系统安全策略）",
    "cacls": "禁止在命令中修改 ACL 权限（系统安全策略）",
    "xcacls": "禁止在命令中修改 ACL 权限（系统安全策略）",
    "takeown": "禁止在命令中夺取文件所有权（系统安全策略）",
    "attrib": "禁止在命令中修改文件属性（可绕过只读保护）",
    "subinacl": "禁止在命令中修改 ACL 权限（系统安全策略）",
    "setacl": "禁止在命令中修改 ACL 权限（系统安全策略）",
    "powershell set-acl": "禁止在命令中修改 ACL 权限（系统安全策略）",
}


def _extract_base_command(command: str) -> str:
    """从完整命令字符串中提取基础命令名（小写，去掉路径和参数）。

    例：
      'rm -rf /path'          → 'rm'
      'del /f /s C:\\*'       → 'del'
      'C:\\Windows\\rmdir.exe /s /q' → 'rmdir'
      'powershell Remove-Item -Path X' → 'remove-item'
      '/usr/bin/rm -rf /'     → 'rm'
    """
    cmd = command.strip()
    if not cmd:
        return ""
    # 去掉开头引号
    cmd = cmd.lstrip('\"\'')
    first_word = cmd.split()[0].lower() if cmd.split() else ""
    # 去掉 exe 扩展名
    base = first_word
    if base.endswith('.exe'):
        base = base[:-4]
    # 如果包含路径分隔符，取最后一段
    base = base.replace('\\', '/').split('/')[-1]
    return base


def _check_command_blacklist(command: str) -> str | None:
    """
    检查命令是否在黑名单中。
    返回 None 表示通过；返回字符串则说明被拦截的原因（建议信息）。
    """
    base = _extract_base_command(command)
    if not base:
        return None
    # 精确匹配
    if base in _CMD_BLACKLIST:
        advice = _BLACKLIST_ADVICE.get(base, "禁止使用此命令")
        return (
            f"命令黑名单拦截：'{base}' 是禁止的删除/毁灭性命令。\n"
            f"{advice}\n"
            f"如需删除文件/目录，请使用 cli_file_ops action=delete (逻辑删除移至回收站)。"
        )
    # 对带空格的多词命令检查开头
    for blk in sorted(_CMD_BLACKLIST, key=len, reverse=True):
        if ' ' in blk and command.strip().lower().startswith(blk):
            advice = _BLACKLIST_ADVICE.get(blk, "禁止使用此命令")
            return (
                f"命令黑名单拦截：'{blk}' 是禁止的删除/毁灭性命令。\n"
                f"{advice}"
            )
    return None


def build_parser():
    p = _HelpFulParser(description="执行命令并输出结构化结果")
    p.add_argument("--command", required=True, help="要执行的命令字符串（由 shell 解释）")
    p.add_argument("--cwd", help="工作目录")
    p.add_argument("--timeoutSec", type=int, default=300, help="超时秒数，默认 300")
    p.add_argument("--shell", choices=["auto", "cmd", "powershell"], default="auto", help="显式选择 shell，默认 auto")
    p.add_argument("--jsonOut", action="store_true", help="向 stdout 输出 JSON")
    p.add_argument("--outFile", help="同时将 JSON 结果写入该文件")
    p.add_argument(
        "--safeMode",
        dest="safeMode",
        action="store_true",
        help="默认 true：拒绝包含高危 shell 元字符的命令；--no-safeMode 关闭危险字符检查（注意：删除/毁灭性命令黑名单不受 safeMode 控制，永远拦截）",
    )
    p.add_argument("--no-safeMode", dest="safeMode", action="store_false", help=argparse.SUPPRESS)
    p.set_defaults(safeMode=True)
    p.add_argument("--runType", choices=["auto", "plan", "execute"], default="", help="当前运行模式；plan 时操作被拒绝")
    return p


def _build_error(*, code: str, message: str, exit_code: Optional[int], hint: str, retryable: bool) -> dict:
    return {
        "code": code,
        "type": "CommandError",
        "message": message,
        "exitCode": exit_code,
        "hint": hint,
        "retryable": retryable,
    }


def _error_message_with_help(parser: argparse.ArgumentParser, message: str) -> str:
    if "\n--help:\n" in message or "usage:" in message.lower():
        return message
    h = _capture_help(parser)
    if not h:
        return message
    return f"{message}\n\n--help:\n{h}"


def _decode_output(raw: Union[bytes, str, None]) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "gb18030", "gbk", "cp936"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _validate_safe_command(command: str) -> None:
    m = _SAFE_BLOCK_RE.search(command)
    if m is None:
        return
    raise ValueError(f"safeMode：命令包含禁止字符: {m.group(0)}")


def _resolve_shell_executable(shell_mode: str, command: str) -> Tuple[str, Optional[str]]:
    mode = str(shell_mode or "auto").lower()
    cmd_exe = shutil.which("cmd.exe") or shutil.which("cmd")
    ps_exe = shutil.which("powershell.exe") or shutil.which("powershell")

    if mode == "cmd":
        if not cmd_exe:
            raise ValueError("未找到 cmd.exe")
        return "cmd", cmd_exe
    if mode == "powershell":
        if not ps_exe:
            raise ValueError("未找到 powershell.exe")
        return "powershell", ps_exe
    if mode != "auto":
        raise ValueError(f"不支持的 shell 模式: {shell_mode}")

    if os.name == "nt":
        c = str(command or "").lstrip().lower()
        if c.startswith("powershell ") or c.startswith("powershell.exe ") or c.startswith("pwsh ") or c.startswith("pwsh.exe "):
            if ps_exe:
                return "auto->powershell", ps_exe
        if cmd_exe:
            return "auto->cmd", cmd_exe
    return "auto", None


def _emit_envelope_file_and_stdout(args: argparse.Namespace, envelope: dict) -> None:
    if args.outFile:
        out = Path(args.outFile)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.jsonOut or not args.outFile:
        print(json.dumps(envelope, ensure_ascii=False))


def agent_main(
    *,
    command: str,
    cwd: str | None = None,
    timeout_sec: int = 300,
    shell: str = "auto",
    safe_mode: bool = True,
    run_type: str = "",
    parser_for_help: argparse.ArgumentParser | None = None,
) -> dict:
    """进程内入口；返回与 CLI 打印一致的外层 JSON 结构。"""
    try:
        rt = str(run_type or "").strip().lower()
        if rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许执行命令"}}
        # ── 黑名单检查（不受 safeMode 控制，永远拦截）──
        block_reason = _check_command_blacklist(command)
        if block_reason is not None:
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "CommandBlacklisted",
                    "message": block_reason,
                },
            }
        if safe_mode:
            _validate_safe_command(command)
        selected_shell, shell_executable = _resolve_shell_executable(shell, command)
        cp = subprocess.run(
            command,
            shell=True,
            executable=shell_executable,
            cwd=cwd,
            text=False,
            capture_output=True,
            timeout=timeout_sec,
        )
        sub_ok = cp.returncode == 0
        data = {
            "ok": sub_ok,
            "exitCode": cp.returncode,
            "stdout": _decode_output(cp.stdout),
            "stderr": _decode_output(cp.stderr),
            "timeout": False,
            "shell": shell,
            "selectedShell": selected_shell,
            "shellExecutable": shell_executable or "auto",
        }
        error = None
        if not sub_ok:
            msg = _error_message_with_help(parser_for_help, "command failed") if parser_for_help else "command failed"
            error = _build_error(
                code="E_COMMAND_FAILED",
                message=msg,
                exit_code=cp.returncode,
                hint="查看 stderr，检查 --command 与 --cwd 是否正确",
                retryable=False,
            )
        return {"ok": sub_ok, "data": data, "error": error}
    except subprocess.TimeoutExpired as e:
        data = {
            "ok": False,
            "exitCode": -1,
            "stdout": _decode_output(e.stdout),
            "stderr": _decode_output(e.stderr),
            "timeout": True,
            "shell": shell,
            "selectedShell": "auto",
            "shellExecutable": "auto",
        }
        msg = _error_message_with_help(parser_for_help, "command timed out") if parser_for_help else "command timed out"
        error = _build_error(
            code="E_TIMEOUT",
            message=msg,
            exit_code=-1,
            hint="可增大 --timeoutSec，或拆分长时间任务",
            retryable=True,
        )
        return {"ok": False, "data": data, "error": error}
    except Exception as e:
        ex_msg = str(e) + ("\n\n--help:\n" + _capture_help(parser_for_help) if parser_for_help else "")
        data = {
            "ok": False,
            "exitCode": None,
            "stdout": "",
            "stderr": ex_msg,
            "timeout": False,
            "shell": shell,
            "selectedShell": "auto",
            "shellExecutable": "auto",
        }
        error = _build_error(
            code="E_INVALID_COMMAND",
            message=ex_msg,
            exit_code=None,
            hint="黑名单命令永远拦截；safeMode 下请避免复杂 shell，若确认安全可用 --no-safeMode（自担风险）",
            retryable=False,
        )
        return {"ok": False, "data": data, "error": error}


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.timeoutSec <= 0:
        raise ValueError("timeoutSec 必须 > 0")
    cwd = None
    if args.cwd:
        cwd = Path(args.cwd)
        if not cwd.exists() or not cwd.is_dir():
            raise ValueError(f"cwd 无效: {cwd}")
        cwd = str(cwd)
    env = agent_main(
        command=args.command,
        cwd=cwd,
        timeout_sec=args.timeoutSec,
        shell=args.shell,
        safe_mode=bool(args.safeMode),
        parser_for_help=parser,
    )
    _emit_envelope_file_and_stdout(args, env)


if __name__ == "__main__":
    main()
