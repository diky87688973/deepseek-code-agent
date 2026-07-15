# -*- coding: utf-8 -*-
"""模型 Provider 路由：根据模型名前缀 (deepseek- / glm-) 返回对应的 API 配置。

使用方式：
    from util.agent_model_provider import get_provider, provider_api_base_url, provider_api_key

    model = "glm-5.2"
    p = get_provider(model)              # → "glm"
    url = provider_api_base_url(p)      # → 从 [model_vision] 读取 base_url
    key = provider_api_key(p)           # → 从 [model_vision] 读取 api_key
"""

from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

from util.config_loader import load_config

_AGENT_CONFIG = load_config(verbose=False)

# ── 模型名前缀 → provider 标识 ──
_PREFIX_PROVIDER_MAP: Dict[str, str] = {
    "glm-": "glm",
    "deepseek-": "deepseek",
    "local-model": "local_model",
}

# ── provider → (base_url 配置键, api_key 配置键) ──
_PROVIDER_CONFIG_KEYS: Dict[str, Tuple[str, str]] = {
    "deepseek": ("AGENT_MODEL_API_BASE_URL", "AGENT_MODEL_API_KEY"),
    "glm": ("AGENT_MODEL_GLM_API_BASE_URL", "AGENT_MODEL_GLM_API_KEY"),
    "local_model": ("AGENT_LOCAL_MODEL_BASE_URL", "AGENT_LOCAL_MODEL_API_KEY"),
}

# ── 缓存 provider 对应的配置值（首次延迟读取） ──
_PROVIDER_CACHE: Dict[str, Dict[str, str]] = {}


def _resolve_provider_config(provider: str) -> Dict[str, str]:
    """延迟读取并缓存 provider 的 base_url + api_key。"""
    if provider in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[provider]
    keys = _PROVIDER_CONFIG_KEYS.get(provider)
    if not keys:
        raise ValueError(f"未知的 provider: {provider!r}，支持: {list(_PROVIDER_CONFIG_KEYS)}")
    url_key, key_key = keys
    base_url = str(_AGENT_CONFIG.get(url_key) or "").strip().rstrip("/")
    api_key = str(_AGENT_CONFIG.get(key_key) or "").strip()
    cfg = {"base_url": base_url, "api_key": api_key}
    _PROVIDER_CACHE[provider] = cfg
    return cfg


def supported_providers() -> Set[str]:
    """返回所有已注册的 provider 标识。"""
    return set(_PROVIDER_CONFIG_KEYS.keys())


def get_provider(model_name: str) -> str:
    """根据模型名返回 provider 标识（DeepSeek / glm）。

    示例：
        get_provider("glm-5.2")       → "glm"
        get_provider("deepseek-v4-pro") → "deepseek"
        get_provider("unknown-x")       → ValueError
    """
    m = str(model_name or "").strip().lower()
    if not m:
        raise ValueError("模型名不能为空")
    for prefix, provider in _PREFIX_PROVIDER_MAP.items():
        if m.startswith(prefix):
            return provider
    raise ValueError(
        f"无法识别模型 {model_name!r} 的 provider。"
        f"支持的模型名前缀: {list(_PREFIX_PROVIDER_MAP)}"
    )


def _chat_completions_url(base: str, provider: str) -> str:
    """按 provider 拼接 chat/completions 完整 URL。

    - DeepSeek 官方基址为 https://api.deepseek.com → .../v1/chat/completions
    - GLM 视觉模型基址已含 /v4 → .../v4/chat/completions
    - local_model 代理暴露 /v1/chat/completions
    """
    b = str(base or "").strip().rstrip("/")
    if not b:
        return b
    if b.endswith("/chat/completions"):
        return b
    if provider in ("deepseek", "local_model"):
        if b.endswith("/v1"):
            return f"{b}/chat/completions"
        return f"{b}/v1/chat/completions"
    return f"{b}/chat/completions"


def provider_api_base_url(provider: str) -> str:
    """返回 provider 的 chat completions API 完整地址。"""
    cfg = _resolve_provider_config(provider)
    base = cfg["base_url"]
    if not base:
        raise ValueError(
            f"Provider {provider!r} 的 api_base_url 未配置。"
            f"请在 config.ini 中设置对应节。"
        )
    return _chat_completions_url(base, provider)


def provider_api_key(provider: str) -> str:
    """返回 provider 的 API Key。

    - local_model 不需要 API Key（本地代理不校验）
    - 其余 provider 必须配置 api_key
    """
    cfg = _resolve_provider_config(provider)
    key = cfg["api_key"]
    if not key:
        if provider == "local_model":
            return ""
        raise ValueError(
            f"Provider {provider!r} 的 api_key 未配置。"
            f"请在 config.ini 的 [model_reasoning] 或 [model_vision] 中设置对应字段。"
        )
    return key


def provider_has_config(provider: str) -> bool:
    """检查 provider 是否已配置 api_key（可用作 UI 中隐藏未配置选项）。"""
    try:
        cfg = _resolve_provider_config(provider)
        return bool(cfg["api_key"])
    except ValueError:
        return False


def adapt_request_body(payload: dict, provider: str) -> dict:
    """对请求体做 provider 专属适配（返回修改后的 payload 副本）。

    - local_model: 浏览器页面不支持 tools/thinking/reasoning_effort
    - 其余 provider 无需剥离（GLM-5.2 也支持 thinking / reasoning_effort）
    """
    out = dict(payload)
    if provider == "local_model":
        out.pop("tools", None)
        out.pop("reasoning_effort", None)
        out.pop("thinking", None)
        out.pop("tool_choice", None)
        out.pop("parallel_tool_calls", None)
        out.pop("stream_options", None)
        out["max_tokens"] = 65536
    return out
