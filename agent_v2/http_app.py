# -*- coding: utf-8 -*-
"""组装 FastAPI 应用：lifespan、静态资源挂载、注册显式路由模块。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from agent_v2 import agent_core as core
from agent_v2.live_state import abort_all_conversation_runs_on_shutdown
from agent_v2.routes import router as agent_http_router

@asynccontextmanager
async def agent_lifespan(app: FastAPI):
    """捕获 uvicorn 关闭时的 CancelledError，避免打印无害 Traceback。"""
    try:
        yield
    except asyncio.CancelledError:
        pass
    finally:
        abort_all_conversation_runs_on_shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Code Web agent", lifespan=agent_lifespan)
    app.mount("/assets", StaticFiles(directory=str(core.AGENT_ROOT / "res")), name="assets")
    # 挂载工作区根目录，支持图片/视频 HTTP 预览
    try:
        _ws = str(core.AGENT_CONFIG.get("AGENT_WORKSPACE_DIR") or "")
        if _ws:
            app.mount("/workspace", StaticFiles(directory=_ws), name="workspace_root")
    except Exception:
        pass
    app.include_router(agent_http_router)
    return app
