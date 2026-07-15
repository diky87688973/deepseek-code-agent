# -*- coding: utf-8 -*-
"""命令执行共用：黑名单、safe_mode 字符检查、shell 选择与输出解码。"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import threading
from typing import Dict, List, Optional, Set, Tuple, Union

# 命令/内联脚本：实时 tool_progress 与 tool_end.preview 共用展示上限（保持一致）
STREAM_OUTPUT_TAIL_MAX_CHARS = 12000
STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS = 4000

# run_command 当前 shell 子进程（供超时后 taskkill /T 强杀，避免 Windows 下只杀 cmd 不杀 winget）
_ACTIVE_SHELL_PID_LOCK = threading.Lock()
_ACTIVE_SHELL_PIDS: Dict[str, int] = {}
_DEFAULT_SHELL_SCOPE = "__default__"

_SAFE_BLOCK_RE = re.compile(r"[;&|`$><]")
_ANSI_ESC_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x08|\r")

# ── 命令黑名单：真删除/毁灭性操作 ──
# 无论 safe_mode=true/false，均拦截。应改用 delete_file（逻辑删除移至回收站）。
_CMD_BLACKLIST: Set[str] = {
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
_BLACKLIST_ADVICE: Dict[str, str] = {
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


def _check_command_blacklist(command: str) -> Optional[str]:
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
    # 内容层面拦截：base64 编码（模型常用来嵌入图片，浪费 token 且导致对话中断）
    _base64_inline_patterns = ["import base64", "from base64"]
    for _pat in _base64_inline_patterns:
        if _pat in command.lower():
            return (
                f"命令黑名单拦截：命令中包含 '{_pat}'。\n"
                f"禁止在命令/代码中使用 base64 编码嵌入图片/文件内容。\n"
                f"请使用专用工具（如 read_file、write_file、read_write、web_fetch）处理文件内容。\n"
                f"图片预览请用 `![图片](url)` 格式，不要用 data URI/base64。"
            )
    # 内容层面拦截：绕过 kling_generate 工具直接调可灵 API（必须走确认拦截流程）
    _kling_inline_patterns = ["api-beijing.klingai.com", "klingai.com", "AGENT_KLING_API_KEY", "AGENT_KLING_SECRET_KEY", "KLING_API_KEY", "KLING_SECRET_KEY"]
    for _pat in _kling_inline_patterns:
        if _pat in command.lower():
            return (
                f"命令黑名单拦截：命令中包含 '{_pat}'。\n"
                f"禁止通过 run_command/python_inline 直接调用可灵 API。\n"
                f"请使用 kling_generate 工具并走用户确认流程后生成。"
            )
    # 内容层面拦截：禁止用命令编辑项目源码文件（echo/sed/awk/tee 等定向写入）
    _SRC_EXTS = r"\.(py|json|md|ts|js|html|css|java|txt|ini|cfg|toml|ya?ml|xml|bat|sh|ps1)"
    if re.search(r"[|>]\s*\S*" + _SRC_EXTS, command):
        return (
            "命令黑名单拦截：命令中包含对源码文件的写入操作。\n"
            "禁止通过 run_command 用 echo/sed/awk/tee 等命令直接修改项目源代码文件。\n"
            "代码编辑必须走 write_file / replace_in_file / apply_patch 工具。"
        )
    if re.search(r"\bsed\s+-i", command) and re.search(_SRC_EXTS, command):
        return (
            "命令黑名单拦截：禁止用 sed -i 直接修改源码文件。\n"
            "请使用 replace_in_file 工具做文件内替换。"
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
    raise ValueError(f"safe_mode：命令包含禁止字符: {m.group(0)}")


def register_shell_process(pid: int, scope: str = _DEFAULT_SHELL_SCOPE) -> None:
    key = str(scope or _DEFAULT_SHELL_SCOPE).strip() or _DEFAULT_SHELL_SCOPE
    with _ACTIVE_SHELL_PID_LOCK:
        _ACTIVE_SHELL_PIDS[key] = int(pid)


def unregister_shell_process(scope: str = _DEFAULT_SHELL_SCOPE) -> None:
    key = str(scope or _DEFAULT_SHELL_SCOPE).strip() or _DEFAULT_SHELL_SCOPE
    with _ACTIVE_SHELL_PID_LOCK:
        _ACTIVE_SHELL_PIDS.pop(key, None)


def kill_shell_process_tree(pid: Optional[int] = None, scope: str = _DEFAULT_SHELL_SCOPE) -> None:
    """结束进程及其子进程（Windows: taskkill /T）。"""
    with _ACTIVE_SHELL_PID_LOCK:
        if pid is not None:
            target = int(pid)
        else:
            key = str(scope or _DEFAULT_SHELL_SCOPE).strip() or _DEFAULT_SHELL_SCOPE
            target = int(_ACTIVE_SHELL_PIDS.get(key) or 0)
    if target <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    else:
        import signal

        try:
            os.killpg(os.getpgid(target), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            try:
                os.kill(target, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass


def force_kill_active_shell_process(scope: str = "") -> bool:
    """宿主硬超时：强杀 run_command 进程树（可按 scope 限定会话）。"""
    key = str(scope or "").strip()
    with _ACTIVE_SHELL_PID_LOCK:
        if key:
            pids = [int(_ACTIVE_SHELL_PIDS[key])] if key in _ACTIVE_SHELL_PIDS else []
        else:
            pids = list(_ACTIVE_SHELL_PIDS.values())
    if not pids:
        return False
    for pid in pids:
        kill_shell_process_tree(pid)
    return True


def sanitize_command_output_for_display(
    text: str, *, max_chars: int = STREAM_OUTPUT_TAIL_MAX_CHARS
) -> str:
    """去掉 ANSI/回车覆盖行，压缩空行，供 SSE 预览与聊天卡片展示。"""
    if not text:
        return ""
    t = _ANSI_ESC_RE.sub("", str(text))
    lines = t.splitlines()
    compact: List[str] = []
    empty_run = 0
    for line in lines:
        if not line.strip():
            empty_run += 1
            if empty_run <= 2:
                compact.append(line)
            continue
        empty_run = 0
        compact.append(line)
    t = "\n".join(compact)
    if len(t) > max_chars:
        return t[:max_chars] + "\n…(输出已截断)"
    return t


atexit.register(force_kill_active_shell_process)


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
