#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek Code Agent v2 入口：装配 agent_v2.agent_core 与 HTTP 路由。

原版 deepseek_code_agent.py 保留不动。实现细节见 agent_v2/agent_core.py。
"""
from __future__ import annotations

from agent_v2.http_app import create_app
from agent_v2 import agent_core as _core

app = create_app()


def main() -> None:
    from util.config_loader import load_config
    import sys
    import uvicorn

    load_config(verbose=True)
    port_str = str(_core.AGENT_CONFIG["AGENT_SERVER_PORT"]).strip()
    if not port_str:
        print("FATAL: AGENT_SERVER_PORT 未设置！请在 config.ini 的 [server] 节配置 port", flush=True)
        sys.exit(1)
    if not _core._chat_api_key_available():
        print(
            "⚠️  WARNING: API Key 未配置或为空！请在 config.ini 的 [model] 节设置 api_key 或环境变量 CHAT_API_KEY",
            file=sys.stderr,
            flush=True,
        )
        print("⚠️  或通过环境变量 CHAT_API_KEY 设置", file=sys.stderr, flush=True)
    uvicorn.run(
        app,
        host=_core.AGENT_CONFIG["AGENT_SERVER_HOST"],
        port=int(port_str),
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
