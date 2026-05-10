#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置加载器 — 优先级：config.ini > 环境变量 > 内置默认值

搜索路径（按优先级）：
  1. 可执行文件同级目录（PyInstaller 打包后）
  2. 项目根目录（源码运行）
  3. 当前工作目录

格式：config.ini（支持 # / ; 注释，Python configparser 原生解析）
INI 键名使用全小写+下划线，自动映射为内部 AGENT_* 大写命名。
"""
from __future__ import annotations

import os
import sys
from configparser import ConfigParser
from pathlib import Path
from typing import Any, Optional


# ── 跨平台默认路径 ──
def _default_workspace() -> str:
    return str(Path.home() / "Desktop")


def _default_data_root() -> str:
    return str(Path.home() / "AI_DATA_ROOT")


# ── INI section.key → 内部 AGENT_* 键名映射 ──
INI_TO_AGENT_MAP: dict[str, str] = {
    "model.api_base_url": "AGENT_MODEL_API_BASE_URL",
    "model.api_key": "AGENT_MODEL_API_KEY",
    "server.port": "AGENT_SERVER_PORT",
    "workspace.dir": "AGENT_WORKSPACE_DIR",
    "workspace.data_root": "AGENT_DATA_ROOT_DIR",
    "knowledge_base.dir": "AGENT_KNOWLEDGE_BASE_DIR",
    "knowledge_base.max_file_size": "AGENT_KB_MAX_FILE_SIZE",
    "session.encryption": "AGENT_SESSION_ENCRYPTION",
    "session.key_file": "AGENT_SESSION_KEY_FILE",
    "context.full_user_rounds": "AGENT_CONTEXT_FULL_USER_ROUNDS",
    "context.pure_user_rounds": "AGENT_CONTEXT_PURE_USER_ROUNDS",
    "context.token_method": "AGENT_CONTEXT_TOKEN_METHOD",
    "context.token_estimate_en_per_char": "AGENT_TOKEN_ESTIMATE_EN_PER_CHAR",
    "context.token_estimate_zh_per_char": "AGENT_TOKEN_ESTIMATE_ZH_PER_CHAR",
    "context.layout_budget_tokens": "AGENT_CONTEXT_LAYOUT_BUDGET_TOKENS",
    "context.summary_ttl_sec": "AGENT_SUMMARY_IN_PROGRESS_TTL_SEC",
    "context.summary_token_threshold": "AGENT_CONTEXT_SUMMARY_TOKEN_THRESHOLD",
    "context.max_tool_rounds": "AGENT_MAX_TOOL_ROUNDS",
    "ui.restore_max_tabs": "AGENT_UI_RESTORE_MAX_TABS",
    "ui.restore_max_chat_items": "AGENT_UI_RESTORE_MAX_CHAT_ITEMS",
    "ui.preview_intent_keys": "AGENT_PREVIEW_INTENT_KEYS",
    "misc.reasoning_effort": "AGENT_REASONING_EFFORT",
    "misc.at_message_file_prefetch": "AGENT_AT_MESSAGE_FILE_PREFETCH",
    "misc.unlock_code_update": "UNLOCK_CODE_UPDATE",
}

# AGENT_* → INI section.key 反向映射
AGENT_TO_INI_MAP: dict[str, str] = {v: k for k, v in INI_TO_AGENT_MAP.items()}

# 配置键 → 环境变量名映射
CONFIG_TO_ENV_MAP: dict[str, str] = {
    "AGENT_MODEL_API_BASE_URL": "CHAT_API_BASE_URL",
    "AGENT_MODEL_API_KEY": "CHAT_API_KEY",
    "AGENT_SERVER_PORT": "PORT",
    "AGENT_WORKSPACE_DIR": "WORKSPACE_DIR",
    "AGENT_DATA_ROOT_DIR": "AGENT_DATA_ROOT_DIR",
    "UNLOCK_CODE_UPDATE": "UNLOCK_CODE_UPDATE",
    "AGENT_KNOWLEDGE_BASE_DIR": "AGENT_KNOWLEDGE_BASE_DIR",
    "AGENT_KB_MAX_FILE_SIZE": "AGENT_KB_MAX_FILE_SIZE",
    "AGENT_REASONING_EFFORT": "REASONING_EFFORT",
}

# 仅应用内部读取，不写入环境变量
CONFIG_ONLY_DEFAULTS: dict[str, Any] = {
    "AGENT_SESSION_ENCRYPTION": "auto",
    "AGENT_SESSION_KEY_FILE": "",
    "AGENT_CONTEXT_FULL_USER_ROUNDS": 5,
    "AGENT_CONTEXT_PURE_USER_ROUNDS": 0,
    "AGENT_CONTEXT_TOKEN_METHOD": "estimate",
    "AGENT_TOKEN_ESTIMATE_EN_PER_CHAR": 0.3,
    "AGENT_TOKEN_ESTIMATE_ZH_PER_CHAR": 0.6,
    "AGENT_CONTEXT_LAYOUT_BUDGET_TOKENS": 131072,
    "AGENT_CONTEXT_SUMMARY_TOKEN_THRESHOLD": 200000,
    "AGENT_SUMMARY_IN_PROGRESS_TTL_SEC": 300.0,
    "AGENT_MAX_TOOL_ROUNDS": 10000,
    "AGENT_UI_RESTORE_MAX_TABS": 8,
    "AGENT_UI_RESTORE_MAX_CHAT_ITEMS": 40,
    "AGENT_PREVIEW_INTENT_KEYS": ["预览", "原文", "全文", "完整内容", "原始内容", "显示文件", "打开", "查看", "读取", "给我看看", "看一下", "看一看"],
    "AGENT_AT_MESSAGE_FILE_PREFETCH": False,
}

# 配置项默认值（配置文件和环境变量均未设置时使用）
DEFAULT_VALUES: dict[str, Any] = {
    "AGENT_MODEL_API_BASE_URL": "https://api.deepseek.com",
    "AGENT_MODEL_API_KEY": "",
    "AGENT_WORKSPACE_DIR": _default_workspace(),
    "AGENT_DATA_ROOT_DIR": _default_data_root(),
    "UNLOCK_CODE_UPDATE": False,
    "AGENT_KNOWLEDGE_BASE_DIR": "",
    "AGENT_KB_MAX_FILE_SIZE": 200000,
    "AGENT_REASONING_EFFORT": "high",
}

# 环境变量默认值
ENV_DEFAULTS: dict[str, str] = {
    "CHAT_API_BASE_URL": "https://api.deepseek.com",
    "CHAT_API_KEY": "",
    "WORKSPACE_DIR": _default_workspace(),
    "REASONING_EFFORT": "high",
}


def _find_config_ini() -> Optional[Path]:
    """按优先级查找 config.ini"""
    candidates: list[str] = []

    # 1) 可执行文件/脚本所在目录
    base_dir: Optional[Path] = None
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        main_script = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else None
        if main_script and main_script.is_dir():
            base_dir = main_script

    if base_dir:
        candidates.append(str(base_dir / "config.ini"))

    # 2) 项目根目录（config_loader.py 所在目录的父级）
    try:
        root = Path(__file__).resolve().parent.parent
        candidates.append(str(root / "config.ini"))
    except Exception:
        pass

    # 3) 当前工作目录
    candidates.append(str(Path.cwd() / "config.ini"))

    seen = set()
    for p_str in candidates:
        if p_str not in seen:
            seen.add(p_str)
            p = Path(p_str)
            if p.is_file():
                return p
    return None


def _read_ini(path: Path) -> dict[str, Any]:
    """解析 config.ini，返回 AGENT_* 键名的扁平字典"""
    cp = ConfigParser()
    cp.read(path, encoding="utf-8")

    result: dict[str, Any] = {}
    for section in cp.sections():
        for key, value in cp.items(section):
            agent_key = INI_TO_AGENT_MAP.get(f"{section}.{key}")
            if agent_key is None:
                print(
                    f"[config_loader] WARNING: 忽略不识别的配置项 [{section}].{key}",
                    file=sys.stderr, flush=True,
                )
                continue

            value = value.strip()

            # 布尔值
            if value.lower() in ("true", "yes", "on", "1"):
                typed: Any = True
            elif value.lower() in ("false", "no", "off", "0"):
                typed = False
            # 整数
            elif value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
                typed = int(value)
            # 浮点数
            elif _is_float(value):
                typed = float(value)
            # 列表（逗号分隔）
            elif agent_key in ("AGENT_PREVIEW_INTENT_KEYS",):
                typed = [x.strip() for x in value.split(",") if x.strip()]
            # 空字符串
            elif value == "":
                typed = ""
            else:
                typed = value

            result[agent_key] = typed

    return result


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def load_config(verbose: bool = True) -> dict[str, Any]:
    """
    加载配置，返回合并后的完整配置字典（键名为 AGENT_* 大写格式）。
    优先级：config.ini > 环境变量 > 内置默认值
    """
    result: dict[str, Any] = {}

    # 1) 先从环境变量读取（低优先级，可被配置文件覆盖）
    for cfg_key, env_key in CONFIG_TO_ENV_MAP.items():
        env_val = os.environ.get(env_key)
        if env_val is not None:
            result[cfg_key] = env_val
        else:
            default = DEFAULT_VALUES.get(cfg_key)
            result[cfg_key] = default

    for cfg_key, default in CONFIG_ONLY_DEFAULTS.items():
        result[cfg_key] = default

    # 2) 从 config.ini 读取（覆盖环境变量）
    config_path = _find_config_ini()
    if config_path:
        file_cfg = _read_ini(config_path)

        for cfg_key in CONFIG_TO_ENV_MAP:
            if cfg_key in file_cfg:
                val = file_cfg[cfg_key]
                if cfg_key == "AGENT_WORKSPACE_DIR":
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
            print(f"[config_loader] 未找到 config.ini，使用环境变量/默认值", flush=True)

    # 3) 将最终值写入环境变量
    for cfg_key, env_key in CONFIG_TO_ENV_MAP.items():
        val = result.get(cfg_key)
        if val is not None and str(val).strip():
            os.environ.setdefault(env_key, str(val))
            if config_path:
                file_cfg = _read_ini(config_path)
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
                if len(shown) > 8:
                    shown = shown[:4] + "****" + shown[-4:]
                elif shown:
                    shown = "****"
            ini_ref = AGENT_TO_INI_MAP.get(cfg_key, cfg_key)
            print(f"    {cfg_key} ← config.{ini_ref} | env[{env_key}] = {shown}", flush=True)

    return result


if __name__ == "__main__":
    cfg = load_config(verbose=True)
    print(f"\n配置摘要：")
    print(f"  BASE_URL: {cfg.get('AGENT_MODEL_API_BASE_URL')}")
    has_key = bool(cfg.get('AGENT_MODEL_API_KEY'))
    print(f"  API_KEY: {'已设置 ✓' if has_key else '未设置 ✗'}")
    print(f"  PORT: {cfg.get('AGENT_SERVER_PORT')}")
    print(f"  WORKSPACE_DIR: {cfg.get('AGENT_WORKSPACE_DIR')}")
