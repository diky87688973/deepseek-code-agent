# -*- coding: utf-8 -*-
"""命令执行共用：黑名单、safeMode 字符检查、shell 选择与输出解码。"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional, Tuple, Union

_SAFE_BLOCK_RE = re.compile(r"[;&|`$><]")

# ── 命令黑名单：真删除/毁灭性操作 ──
# 无论 safeMode=true/false，均拦截。应改用 delete_file（逻辑删除移至回收站）。
_CMD_BLACKLIST: set[str] = {
    # Windows CMD
    "del",
    "erase",
    "rmdir",
    "rd",
    "deltree",
    # PowerShell
    "remove-item",
    "ri",
    "remove-itemproperty",
    "rp",
    "clear-item",
    "cli",
    # Unix / Git Bash / WSL
    "rm",
    "rmdir",
    "unlink",
    "shred",
    "wipe",
    "srm",
    # 格式化 / 分区 / 磁盘覆写
    "format",
    "mkfs",
    "fdisk",
    "mkswap",
    "dd",
    "diskpart",
    # 注册表破坏
    "reg",
    "regdelete",
    "reg delete",
    # 权限/ACL 修改（防止绕过 AGENT_ROOT 只读锁）
    "icacls",
    "cacls",
    "xcacls",
    "takeown",
    "attrib",
    "subinacl",
    "setacl",
    "powershell set-acl",
}

# 映射：黑命名单命令 → 建议替代工具
_BLACKLIST_ADVICE: dict[str, str] = {
    "del": "请使用 delete_file 工具（移动到回收站）",
    "erase": "请使用 delete_file 工具（移动到回收站）",
    "rmdir": "请使用 delete_file 工具（移动到回收站）",
    "rd": "请使用 delete_file 工具（移动到回收站）",
    "deltree": "请使用 delete_file 工具（移动到回收站）",
    "remove-item": "请使用 delete_file 工具（移动到回收站）",
    "ri": "请使用 delete_file 工具（移动到回收站）",
    "rm": "请使用 delete_file 工具（移动到回收站）",
    "unlink": "请使用 delete_file 工具（移动到回收站）",
    "shred": "请使用 delete_file 工具（移动到回收站）",
    "wipe": "请使用 delete_file 工具（移动到回收站）",
    "srm": "请使用 delete_file 工具（移动到回收站）",
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
    """从完整命令字符串中提取基础命令名（小写，去掉路径和参数）。"""
    cmd = command.strip()
    if not cmd:
        return ""
    cmd = cmd.lstrip("\"'")
    first_word = cmd.split()[0].lower() if cmd.split() else ""
    base = first_word
    if base.endswith(".exe"):
        base = base[:-4]
    base = base.replace("\\", "/").split("/")[-1]
    return base


def _check_command_blacklist(command: str) -> str | None:
    """返回 None 表示通过；否则返回拦截原因（含建议）。"""
    base = _extract_base_command(command)
    if not base:
        return None
    if base in _CMD_BLACKLIST:
        advice = _BLACKLIST_ADVICE.get(base, "禁止使用此命令")
        return (
            f"命令黑名单拦截：'{base}' 是禁止的删除/毁灭性命令。\n"
            f"{advice}\n"
            f"如需删除文件/目录，请使用 delete_file 工具（逻辑删除移至回收站）。"
        )
    for blk in sorted(_CMD_BLACKLIST, key=len, reverse=True):
        if " " in blk and command.strip().lower().startswith(blk):
            advice = _BLACKLIST_ADVICE.get(blk, "禁止使用此命令")
            return f"命令黑名单拦截：'{blk}' 是禁止的删除/毁灭性命令。\n{advice}"
    return None


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
