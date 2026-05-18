#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置加载器 — 优先级：config.ini（配置文件） > 环境变量（无内置默认值，缺失报错）

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


# ── INI section.key → 内部 AGENT_* 键名映射 ──
INI_TO_AGENT_MAP: dict[str, str] = {
    "model.api_base_url": "AGENT_MODEL_API_BASE_URL",
    "model.api_key": "AGENT_MODEL_API_KEY",
    "server.port": "AGENT_SERVER_PORT",
    "server.host": "AGENT_SERVER_HOST",
    "workspace.dir": "AGENT_WORKSPACE_DIR",
    "workspace.data_root": "AGENT_DATA_ROOT_DIR",
    "knowledge_base.dir": "AGENT_KNOWLEDGE_BASE_DIR",
    "knowledge_base.max_file_size": "AGENT_KB_MAX_FILE_SIZE",
    "skills.dir": "AGENT_SKILLS_DIR",
    "skills.max_file_size": "AGENT_SKILLS_MAX_FILE_SIZE",
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
    "context.summary_thinking": "AGENT_SUMMARY_THINKING",
    "context.summary_output_max_chars": "AGENT_SUMMARY_OUTPUT_MAX_CHARS",
    "misc.console_log": "AGENT_CONSOLE_LOG",
    "misc.tool_debug": "AGENT_TOOL_DEBUG",
    "misc.reasoning_delta_fields": "AGENT_REASONING_DELTA_FIELDS",
    "misc.pricing_source": "AGENT_PRICING_SOURCE",
    "misc.pricing_json": "AGENT_PRICING_JSON",
    "misc.stream_include_usage": "AGENT_STREAM_INCLUDE_USAGE",
    "misc.extra_headers_json": "AGENT_EXTRA_HEADERS_JSON",
    "misc.pricing_page_url": "AGENT_PRICING_PAGE_URL",
    "model.allowed_models": "AGENT_ALLOWED_MODELS",
    "model.default_model": "AGENT_DEFAULT_MODEL",
    "kling.api_key": "AGENT_KLING_API_KEY",
    "kling.secret_key": "AGENT_KLING_SECRET_KEY",
    "kling.api_base_url": "AGENT_KLING_API_BASE_URL",
    "dreamina.cli_path": "AGENT_DREAMINA_CLI_PATH",
}

# AGENT_* → INI section.key 反向映射
AGENT_TO_INI_MAP: dict[str, str] = {v: k for k, v in INI_TO_AGENT_MAP.items()}

# 配置键 → 环境变量名映射
CONFIG_TO_ENV_MAP: dict[str, str] = {
    "AGENT_MODEL_API_BASE_URL": "CHAT_API_BASE_URL",
    "AGENT_MODEL_API_KEY": "CHAT_API_KEY",
    "AGENT_SERVER_PORT": "PORT",
    "AGENT_SERVER_HOST": "HOST",
    "AGENT_WORKSPACE_DIR": "WORKSPACE_DIR",
    "AGENT_DATA_ROOT_DIR": "AGENT_DATA_ROOT_DIR",
    "UNLOCK_CODE_UPDATE": "UNLOCK_CODE_UPDATE",
    "AGENT_KNOWLEDGE_BASE_DIR": "AGENT_KNOWLEDGE_BASE_DIR",
    "AGENT_KB_MAX_FILE_SIZE": "AGENT_KB_MAX_FILE_SIZE",
    "AGENT_SKILLS_DIR": "AGENT_SKILLS_DIR",
    "AGENT_SKILLS_MAX_FILE_SIZE": "AGENT_SKILLS_MAX_FILE_SIZE",
    "AGENT_REASONING_EFFORT": "REASONING_EFFORT",
    "AGENT_SUMMARY_THINKING": "AGENT_SUMMARY_THINKING",
    "AGENT_SUMMARY_OUTPUT_MAX_CHARS": "AGENT_SUMMARY_OUTPUT_MAX_CHARS",
    "AGENT_CONSOLE_LOG": "AGENT_CONSOLE_LOG",
    "AGENT_TOOL_DEBUG": "AGENT_TOOL_DEBUG",
    "AGENT_REASONING_DELTA_FIELDS": "CHAT_API_REASONING_DELTA_FIELDS",
    "AGENT_PRICING_SOURCE": "CHAT_PRICING_SOURCE",
    "AGENT_PRICING_JSON": "CHAT_PRICING_JSON",
    "AGENT_STREAM_INCLUDE_USAGE": "CHAT_API_STREAM_INCLUDE_USAGE",
    "AGENT_EXTRA_HEADERS_JSON": "CHAT_API_EXTRA_HEADERS_JSON",
    "AGENT_PRICING_PAGE_URL": "CHAT_PRICING_PAGE_URL",
    "AGENT_ALLOWED_MODELS": "CHAT_API_MODELS",
    "AGENT_DEFAULT_MODEL": "CHAT_API_DEFAULT_MODEL",
    "AGENT_KLING_API_KEY": "KLING_API_KEY",
    "AGENT_KLING_SECRET_KEY": "KLING_SECRET_KEY",
    "AGENT_KLING_API_BASE_URL": "KLING_API_BASE_URL",
    "AGENT_DREAMINA_CLI_PATH": "DREAMINA_CLI_PATH",
}

# 配置键 → 必须配置的关键项（缺失时 load_config 会报错）
REQUIRED_CONFIG_KEYS: list[str] = [
    "AGENT_MODEL_API_KEY",
]


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
    优先级：config.ini（配置文件） > 环境变量
    关键配置缺失时抛出 ValueError。
    """
    result: dict[str, Any] = {}

    # 1) 先从环境变量读取（低优先级，可被配置文件覆盖）
    for cfg_key, env_key in CONFIG_TO_ENV_MAP.items():
        env_val = os.environ.get(env_key)
        if env_val is not None:
            result[cfg_key] = env_val

    # 2) 从 config.ini 读取（高优先级，覆盖环境变量）
    config_path = _find_config_ini()
    if config_path:
        file_cfg = _read_ini(config_path)
        for cfg_key, val in file_cfg.items():
            if cfg_key == "AGENT_WORKSPACE_DIR":
                if val is not None and str(val).strip():
                    result[cfg_key] = str(val).strip()
            else:
                if val is not None and val != "":
                    result[cfg_key] = val
        if verbose:
            print(f"[config_loader] 已加载配置文件: {config_path}", flush=True)
    else:
        if verbose:
            print(f"[config_loader] 未找到 config.ini，仅使用环境变量配置", flush=True)

    # 3) 检查关键配置是否缺失
    for cfg_key in REQUIRED_CONFIG_KEYS:
        val = result.get(cfg_key)
        if not val or not str(val).strip():
            env_name = CONFIG_TO_ENV_MAP.get(cfg_key, "?")
            ini_ref = AGENT_TO_INI_MAP.get(cfg_key, cfg_key)
            section, key = (ini_ref.split(".", 1) if "." in ini_ref else ("?", ini_ref))
            raise ValueError(
                f"[config_loader] 关键配置 {cfg_key} 未设置！\n"
                f"  请在 config.ini 的 [{section}] 节中设置 {key}，\n"
                f"  或设置环境变量 {env_name}"
            )

    # 4) 将最终值写入环境变量（便于子进程继承）
    for cfg_key, env_key in CONFIG_TO_ENV_MAP.items():
        val = result.get(cfg_key)
        if val is not None and str(val).strip():
            os.environ[env_key] = str(val)

    # 5) 最终状态记录
    if verbose:
        print(f"[config_loader] 配置状态：", flush=True)
        for cfg_key, env_key in CONFIG_TO_ENV_MAP.items():
            final_val = result.get(cfg_key)
            shown = str(final_val) if final_val is not None else "(未设置)"
            if "KEY" in env_key or "key" in env_key:
                if len(shown) > 8:
                    shown = shown[:4] + "****" + shown[-4:]
                elif shown and shown != "(未设置)":
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
