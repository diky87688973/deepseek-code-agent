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


def _is_harmless_client_disconnect(exc: BaseException | None) -> bool:
    """浏览器刷新/关标签会掐断 SSE、/workspace 视频 206 等连接；Windows Proactor 清理时常报 10054。"""
    if exc is None:
        return False
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return True
    if isinstance(exc, OSError):
        w = getattr(exc, "winerror", None)
        if w in (10054, 10053, 10058):
            return True
        if exc.errno in (10054, 104, 32, 54):
            return True
    return False


def _install_client_disconnect_exception_filter(loop: asyncio.AbstractEventLoop):
    """返回被替换的旧 handler（lifespan 结束时还原）。"""
    previous = loop.get_exception_handler()

    def _handler(inner_loop: asyncio.AbstractEventLoop, context: dict) -> None:
        if _is_harmless_client_disconnect(context.get("exception")):
            return
        if previous is not None:
            previous(inner_loop, context)
        else:
            inner_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    return previous


@asynccontextmanager
async def agent_lifespan(app: FastAPI):
    """捕获 uvicorn 关闭时的 CancelledError，避免打印无害 Traceback。"""
    loop = asyncio.get_running_loop()
    prev_handler = _install_client_disconnect_exception_filter(loop)
    try:
        yield
    except asyncio.CancelledError:
        pass
    finally:
        if prev_handler is not None:
            loop.set_exception_handler(prev_handler)
        else:
            loop.set_exception_handler(None)
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
