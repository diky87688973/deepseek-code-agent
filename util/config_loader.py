#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置加载器 — 优先级：配置文件 > 环境变量 > 内置默认值

配置项映射：
  config.json 键名               →  环境变量名           →  代码默认值
  CODE_WEB_AGENT_CHAT_API_BASE_URL →  CHAT_API_BASE_URL   →  https://api.deepseek.com
  CODE_WEB_AGENT_CHAT_API_KEY      →  CHAT_API_KEY        →  (空字符串)
  CODE_WEB_AGENT_SERVER_PORT       →  PORT                →  (必须配置，无默认值)
  CODE_WEB_AGENT_WORKSPACE_DIR     →  WORKSPACE_DIR       →  用户桌面（跨平台）

搜索路径（按优先级）：
  1. 可执行文件同级目录（PyInstaller 打包后）
  2. 项目根目录（源码运行）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# ── 跨平台默认路径 ──
def _default_workspace() -> str:
    """返回用户桌面路径，兼容 Windows 和 macOS"""
    return str(Path.home() / "Desktop")


def _default_data_root() -> str:
    """返回用户目录下的 AI_DATA_ROOT，跨平台兼容"""
    return str(Path.home() / "AI_DATA_ROOT")

# 配置键 → 环境变量名映射
CONFIG_TO_ENV_MAP = {
    "CODE_WEB_AGENT_CHAT_API_BASE_URL": "CHAT_API_BASE_URL",
    "CODE_WEB_AGENT_CHAT_API_KEY": "CHAT_API_KEY",
    "CODE_WEB_AGENT_SERVER_PORT": "PORT",
    "CODE_WEB_AGENT_WORKSPACE_DIR": "WORKSPACE_DIR",
    "CODE_WEB_AGENT_DATA_DIR": "CODE_WEB_AGENT_DATA_DIR",
    "UNLOCK_CODE_UPDATE": "UNLOCK_CODE_UPDATE",
    "KNOWLEDGE_BASE_DIR": "KNOWLEDGE_BASE_DIR",
}

# 仅应用内部读取，不写入环境变量，避免把应用配置扩散到进程环境。
CONFIG_ONLY_DEFAULTS = {
    "CODE_WEB_AGENT_SESSION_ENCRYPTION": "auto",
    "CODE_WEB_AGENT_SESSION_KEY_FILE": "",
}

# 配置项默认值（当配置文件和环境变量均未设置时使用）
DEFAULT_VALUES = {
    "CODE_WEB_AGENT_CHAT_API_BASE_URL": "https://api.deepseek.com",
    "CODE_WEB_AGENT_CHAT_API_KEY": "",
    "CODE_WEB_AGENT_WORKSPACE_DIR": _default_workspace(),
    "CODE_WEB_AGENT_DATA_DIR": _default_data_root(),
    "UNLOCK_CODE_UPDATE": False,
    "KNOWLEDGE_BASE_DIR": "",
}

# 对应环境变量的默认值（代码中的硬编码默认值）
ENV_DEFAULTS = {
    "CHAT_API_BASE_URL": "https://api.deepseek.com",
    "CHAT_API_KEY": "",
    "WORKSPACE_DIR": _default_workspace(),
}


def _find_config_file() -> Optional[Path]:
    """查找 config.json（按优先级）"""
    candidates = []

    # 1) 可执行文件/脚本所在目录
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后：exe 同级目录
        candidates.append(Path(sys.executable).resolve().parent / "config.json")
    else:
        # 源码运行：main_tray.py 或 code_web_agent.py 同级
        main_script = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else None
        if main_script and main_script.is_dir():
            candidates.append(main_script / "config.json")

    # 2) 当前工作目录
    candidates.append(Path.cwd() / "config.json")

    # 3) AGENT_ROOT（项目根）
    try:
        from pathlib import Path as _P
        _root = _P(__file__).resolve().parent.parent
        candidates.append(_root / "config.json")
    except Exception:
        pass

    # 去重并返回第一个存在的
    seen = set()
    for p in candidates:
        if p and p not in seen:
            seen.add(p)
            if p.is_file():
                return p
    return None


def _read_config_file(path: Path) -> dict:
    """读取并解析 config.json"""
    try:
        raw = path.read_text(encoding="utf-8")
        cfg = json.loads(raw)
        if not isinstance(cfg, dict):
            print(f"[config_loader] WARNING: config.json 不是 JSON 对象，已忽略", file=sys.stderr, flush=True)
            return {}
        return cfg
    except json.JSONDecodeError as e:
        print(f"[config_loader] WARNING: config.json 解析失败: {e}，已忽略", file=sys.stderr, flush=True)
        return {}
    except Exception as e:
        print(f"[config_loader] WARNING: 读取 config.json 失败: {e}", file=sys.stderr, flush=True)
        return {}


def load_config(verbose: bool = True) -> dict:
    """
    加载配置，按优先级覆盖环境变量。
    返回合并后的完整配置字典（含默认值）。
    """
    # 1) 先从已有的环境变量读取
    result = {}
    for cfg_key, env_key in CONFIG_TO_ENV_MAP.items():
        env_val = os.environ.get(env_key)
        if env_val is not None:
            result[cfg_key] = env_val
        else:
            default = DEFAULT_VALUES.get(cfg_key)
            result[cfg_key] = default
    for cfg_key, default in CONFIG_ONLY_DEFAULTS.items():
        result[cfg_key] = default

    # 2) 从配置文件读取（覆盖环境变量）
    config_path = _find_config_file()
    if config_path:
        file_cfg = _read_config_file(config_path)
        for cfg_key in CONFIG_TO_ENV_MAP:
            if cfg_key in file_cfg:
                val = file_cfg[cfg_key]
                # WORKSPACE_DIR 允许空字符串（使用默认桌面）
                if cfg_key == "CODE_WEB_AGENT_WORKSPACE_DIR":
                    if val is not None and str(val).strip():
                        result[cfg_key] = str(val).strip()
                else:
                    if val is not None and val != "":
                        result[cfg_key] = val
        for cfg_key in CONFIG_ONLY_DEFAULTS:
            if cfg_key in file_cfg:
                val = file_cfg[cfg_key]
                if val is not None and val != "":
                    result[cfg_key] = val
        if verbose:
            print(f"[config_loader] 已加载配置文件: {config_path}", flush=True)
    else:
        if verbose:
            print(f"[config_loader] 未找到 config.json，使用环境变量/默认值", flush=True)

    # 3) 将最终值写入环境变量（确保后续 os.environ.get() 能读到）
    for cfg_key, env_key in CONFIG_TO_ENV_MAP.items():
        val = result.get(cfg_key)
        if val is not None and str(val).strip():
            os.environ.setdefault(env_key, str(val))
            # 如果配置文件中显式指定（非空），强制覆盖 env
            if config_path:
                file_cfg = _read_config_file(config_path) if config_path else {}
                if cfg_key in file_cfg and file_cfg[cfg_key] is not None and str(file_cfg[cfg_key]).strip():
                    os.environ[env_key] = str(file_cfg[cfg_key])

    # 4) 最终状态记录
    if verbose:
        print(f"[config_loader] 配置状态：", flush=True)
        for cfg_key, env_key in CONFIG_TO_ENV_MAP.items():
            final_val = result.get(cfg_key)
            if final_val is None:
                final_val = os.environ.get(env_key, "(未设置)")
            shown = str(final_val)
            if "KEY" in env_key or "key" in env_key:
                # API Key 脱敏显示
                if len(shown) > 8:
                    shown = shown[:4] + "****" + shown[-4:]
                elif shown:
                    shown = "****"
            print(f"    {cfg_key} → env[{env_key}] = {shown}", flush=True)

    return result


if __name__ == "__main__":
    # 命令行直接运行：测试配置加载
    cfg = load_config(verbose=True)
    print(f"\n配置摘要：")
    print(f"  BASE_URL: {cfg.get('CODE_WEB_AGENT_CHAT_API_BASE_URL')}")
    has_key = bool(cfg.get('CODE_WEB_AGENT_CHAT_API_KEY'))
    print(f"  API_KEY: {'已设置 ✓' if has_key else '未设置 ✗'}")
    print(f"  PORT: {cfg.get('CODE_WEB_AGENT_SERVER_PORT')}")
    print(f"  WORKSPACE_DIR: {cfg.get('CODE_WEB_AGENT_WORKSPACE_DIR')}")
