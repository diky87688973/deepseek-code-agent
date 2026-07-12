# -*- coding: utf-8
"""agent_v4.core.llm_stream"""
from __future__ import annotations

from agent_v4.core.deps import *  # noqa: F403
from agent_v4.core.shared_state import *  # noqa: F403

from util.agent_model_provider import (
    get_provider,
    provider_api_base_url,
    provider_api_key,
    adapt_request_body,
)

def _assistant_display_content_for_sse(content: str, reasoning_content: str) -> str:
    """有效 assistant 正文：content 优先；为空时用 reasoning_content（展示/SSE/落盘/折叠/摘要统一）。"""
    c = str(content or "").strip()
    if c:
        return c
    return str(reasoning_content or "").strip()

def _assistant_message_for_persist(
    content: str, reasoning_content: str, **extra: Any
) -> Dict[str, Any]:
    """落盘 assistant：content 写入有效正文；thinking 模式 API 要求回传 reasoning_content，故非空时始终保留。"""
    persist = _assistant_display_content_for_sse(content, reasoning_content)
    msg: Dict[str, Any] = {"role": "assistant", "content": persist, **extra}
    rc = str(reasoning_content or "").strip()
    if rc:
        msg["reasoning_content"] = reasoning_content
    return msg

def _chat_api_key_available() -> bool:
    """检查 API Key 是否已配置（从 AGENT_CONFIG 读取，无内置默认值）"""
    key = AGENT_CONFIG["AGENT_MODEL_API_KEY"]
    return bool(key and key.strip())

def _choice_snapshot_message(choice0: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(choice0, dict):
        return None
    m = choice0.get("message")
    return m if isinstance(m, dict) and m else None

def _ephemeral_max_tool_rounds_wrap_user() -> Dict[str, Any]:
    return {"role": "user", "content": _max_tool_rounds_user_hint()}

def _finalize_stream_content_text(content_delta: str, last_message: Optional[Dict[str, Any]]) -> str:
    """末帧 content 补齐（如正文仅在 message 里）。"""
    out = str(content_delta or "")
    if not isinstance(last_message, dict):
        return out
    lm_c = last_message.get("content")
    if isinstance(lm_c, str) and lm_c and not out.strip():
        return lm_c
    return out

def _finalize_stream_reasoning(reasoning_delta: str, last_message: Optional[Dict[str, Any]]) -> str:
    """用末帧 choices[0].message 补全 delta 未收齐的推理字段（CHAT_API_REASONING_DELTA_FIELDS）。"""
    out = str(reasoning_delta or "")
    if not isinstance(last_message, dict):
        return out
    lm_r = _best_message_reasoning_field(last_message)
    if lm_r:
        if len(lm_r) >= len(out):
            return lm_r
        if not out:
            return lm_r
    return out

def _get_reasoning_effort(cid: str = "") -> str:
    """从会话级或配置获取 reasoning_effort（high/max），无默认值，缺失则返回 high。"""
    cid_key = str(cid or "").strip()
    if cid_key and cid_key in _REASONING_EFFORTS:
        return _REASONING_EFFORTS[cid_key]
    raw = str(AGENT_CONFIG.get("AGENT_REASONING_EFFORT") or "").strip().lower()
    if raw in ("high", "max"):
        return raw
    return "high"

def _max_tool_rounds_user_hint() -> str:
    """收尾回合 ephemeral user 提示（不落盘）：引导模型拟人总结并请用户发「继续」。"""
    return format_agent_max_tool_rounds_user_hint(MAX_TOOL_ROUNDS)

def _merge_stream_tool_calls(chunks: List[dict]) -> List[dict]:
    merged: Dict[int, dict] = {}
    for item in chunks:
        idx = int(item.get("index", 0) or 0)
        cur = merged.get(idx)
        if cur is None:
            cur = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            merged[idx] = cur
        if item.get("id"):
            cur["id"] = str(item["id"])
        fn = item.get("function") or {}
        name_part = fn.get("name")
        args_part = fn.get("arguments")
        if isinstance(name_part, str) and name_part:
            cur["function"]["name"] += name_part
        if isinstance(args_part, str) and args_part:
            cur["function"]["arguments"] += args_part
    out: List[dict] = []
    for idx in sorted(merged.keys()):
        tc = merged[idx]
        if not tc["function"]["arguments"]:
            tc["function"]["arguments"] = "{}"
        if tc["function"]["name"]:
            out.append(tc)
    return out

def _merge_stream_tool_calls_with_snapshot(
    stream_chunks: List[dict],
    last_message: Optional[Dict[str, Any]],
) -> List[dict]:
    tcalls = _merge_stream_tool_calls(stream_chunks)
    if tcalls:
        return tcalls
    snap = _tool_calls_from_snapshot_message(last_message)
    if snap:
        print(
            f"WARN: stream tool_calls empty; using message.tool_calls snapshot (n={len(snap)})",
            file=sys.stderr,
            flush=True,
        )
    return snap

def _normalize_client_ip_for_tools(ip_raw: Optional[str]) -> str:
    ip = str(ip_raw or "").strip()
    if not ip:
        return ""
    low = ip.lower()
    if low in {"localhost", "0.0.0.0", "127.0.0.1", "::1"}:
        return ""
    try:
        obj = ipaddress.ip_address(ip)
        if obj.is_loopback or obj.is_unspecified or obj.is_private:
            return ""
        return ip
    except ValueError:
        return ""

def _reasoning_stream_finalize_events(before: str, after: str, round_num: int) -> List[Dict[str, Any]]:
    """流式已推送片段后，finalize 若多出正文则再推一条 delta；若整体替换则推 reasoning_sync。"""
    if after == before:
        return []
    if after.startswith(before) and len(after) > len(before):
        return [{"type": "reasoning_delta", "round": round_num, "delta": after[len(before):]}]
    return [{"type": "reasoning_sync", "round": round_num, "text": after}]

def _set_reasoning_effort(cid: str, effort: str) -> bool:
    """设置会话级 reasoning_effort。返回是否设置成功。"""
    e = str(effort or "").strip().lower()
    if e not in ("high", "max"):
        return False
    _REASONING_EFFORTS[str(cid or "").strip()] = e
    return True

def _tool_calls_from_snapshot_message(message: Optional[Dict[str, Any]]) -> List[dict]:
    """末帧 choices[0].message.tool_calls（流式 delta 未收齐时的 API 正规降级）。"""
    if not isinstance(message, dict):
        return []
    raw = message.get("tool_calls")
    if not isinstance(raw, list):
        return []
    out: List[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        fn = item.get("function")
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        tid = str(item.get("id") or "").strip() or f"call_{uuid.uuid4().hex[:12]}"
        args_raw = fn.get("arguments")
        if isinstance(args_raw, str):
            args_s = args_raw or "{}"
        else:
            try:
                args_s = json.dumps(args_raw if args_raw is not None else {}, ensure_ascii=False)
            except Exception:
                args_s = "{}"
        out.append(
            {
                "id": tid,
                "type": str(item.get("type") or "function"),
                "function": {"name": name, "arguments": args_s},
            }
        )
    return out

def model_request(payload: dict) -> dict:
    """通过 provider 路由发送非流式聊天补全请求。"""
    model = payload.get("model", "")
    provider = get_provider(model)
    base_url = provider_api_base_url(provider)
    api_key = provider_api_key(provider)
    body = adapt_request_body(payload, provider)
    return chat_completion_request(body, base_url=base_url, api_key=api_key)


def model_stream_request(payload: dict):
    """通过 provider 路由发送流式聊天补全请求。"""
    model = payload.get("model", "")
    provider = get_provider(model)
    base_url = provider_api_base_url(provider)
    api_key = provider_api_key(provider)
    body = adapt_request_body(payload, provider)
    yield from chat_completion_stream(body, base_url=base_url, api_key=api_key)


# ── 向后兼容别名（旧调用点无需立即改名） ──
deepseek_request = model_request
deepseek_stream_request = model_stream_request

