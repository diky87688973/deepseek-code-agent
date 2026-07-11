# -*- coding: utf-8 -*-
"""FastAPI 请求/响应体模型（HTTP 层专用，与 agent_core 解耦）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class UsageAccumIn(BaseModel):
    session_token_used: int = Field(default=0, ge=0)
    total_prompt_tokens: int = Field(default=0, ge=0)
    total_completion_tokens: int = Field(default=0, ge=0)
    total_cache_hit_tokens: int = Field(default=0, ge=0)
    total_cache_miss_tokens: int = Field(default=0, ge=0)


class ChatImageIn(BaseModel):
    mime: Optional[str] = Field(default="image/png", description="image/png|image/jpeg|...")
    data_base64: str = Field(..., description="纯 base64，可无 data: 前缀")


class ChatIn(BaseModel):
    message: str = Field(default="", description="User message；可与 images 同时为空以外至少一项")
    conversation_id: Optional[str] = Field(default=None, description="Thread id; omit to start new")
    mode: Optional[str] = Field(default=None, description="Mode override: auto/plan/execute")
    model: Optional[str] = Field(
        default=None,
        description="Session model id (must match ALLOWED_MODELS / CHAT_API_MODELS)",
    )
    images: Optional[List[ChatImageIn]] = Field(
        default=None,
        description="可选截图附件（运输用 base64）；落盘后主模型经 look_screenshot 查看",
    )


class ChatCommandInputIn(BaseModel):
    conversation_id: str = Field(..., description="会话 id")
    tool_call_id: str = Field(..., description="当前 run_command 的 tool_call_id")
    input: str = Field(..., description="发送到子进程 stdin 的文本，如 Y 或 N")


class ChatUserConfirmIn(BaseModel):
    conversation_id: str = Field(..., description="Thread id (matches session / cid)")
    confirm: str = Field(..., description="User-selected or typed confirmation text")
    mode: Optional[str] = Field(default=None, description="Mode override: auto/plan/execute")
    model: Optional[str] = Field(
        default=None,
        description="Session model id (must match ALLOWED_MODELS / CHAT_API_MODELS)",
    )


class ChatStopIn(BaseModel):
    conversation_id: str = Field(..., description="Thread id to stop")
    run_id: Optional[str] = Field(default=None, description="Active run id to stop")


class ChatTitleIn(BaseModel):
    conversation_id: str = Field(..., description="Thread id to title")


class ChatUiStateIn(BaseModel):
    active_conversation_id: Optional[str] = Field(default=None)
    tabs: List[Dict[str, Any]] = Field(default_factory=list)


class KbCheckedIn(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    checked: List[str] = Field(default_factory=list, description="勾选的文件相对路径列表")
