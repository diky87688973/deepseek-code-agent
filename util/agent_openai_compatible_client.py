# -*- coding: utf-8 -*-
"""OpenAI-compatible chat completions client (POST .../v1/chat/completions, SSE data: lines).

Use any gateway that follows the OpenAI chat completions + streaming shape used by this agent.
Configure with CHAT_API_BASE_URL + CHAT_API_KEY, or fall back to DEEPSEEK_BASE_URL + DEEPSEEK_API_KEY.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, Optional, Tuple

from fastapi import HTTPException


# 仅当请求走 DeepSeek 官方 API（基址或模型名可判定）时，将上游 HTTP 状态映射为中文说明（见 DeepSeek 文档错误码表）。
_DEEPSEEK_HTTP_HINTS: Dict[int, str] = {
    400: "请求体格式错误，请根据接口返回说明修改请求体。",
    401: "API Key 认证失败，请检查密钥是否正确或是否已在控制台创建 API Key。",
    402: "账号余额不足（额度可能已用完），请确认余额并前往 DeepSeek 控制台充值。",
    422: "请求参数错误，请根据接口返回说明修改相关参数。",
    429: "请求速率达到上限（TPM 或 RPM），请降低调用频率后重试。",
    500: "上游服务器内部故障，请稍后重试；若持续出现请联系服务提供方。",
    503: "上游服务器繁忙，请稍后重试。",
}


def _should_map_deepseek_http_errors(*, base_url: str, model: Optional[str]) -> bool:
    if "deepseek" in (base_url or "").lower():
        return True
    m = str(model or "").strip().lower()
    return m.startswith("deepseek")


def _upstream_error_body_message(body: str) -> str:
    raw = (body or "").strip()
    if not raw:
        return ""
    try:
        o = json.loads(raw)
        if isinstance(o, dict):
            err = o.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()[:800]
            msg2 = o.get("message")
            if isinstance(msg2, str) and msg2.strip():
                return msg2.strip()[:800]
    except json.JSONDecodeError:
        pass
    return raw[:800]


def _map_upstream_http_for_client(status_code: int, body: str, *, base_url: str, model: Optional[str]) -> Tuple[int, str]:
    """返回 (建议返回给前端的 HTTP 状态, detail 文案)。非 DeepSeek 表适用场景时保持原有拼接方式。"""
    detail_plain = _http_error_message(status_code, body)
    if not _should_map_deepseek_http_errors(base_url=base_url, model=model):
        return 502, detail_plain
    hint = _DEEPSEEK_HTTP_HINTS.get(int(status_code))
    if not hint:
        return 502, detail_plain
    upstream_msg = _upstream_error_body_message(body)
    if upstream_msg and upstream_msg not in hint:
        return 502, f"{hint} 接口返回：{upstream_msg}"
    return 502, hint


def chat_api_base_url() -> str:
    v = (os.environ.get("CHAT_API_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    return v


def chat_api_key() -> str:
    return (os.environ.get("CHAT_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def chat_completions_url() -> str:
    path = (os.environ.get("CHAT_COMPLETIONS_PATH") or "/v1/chat/completions").strip()
    if not path.startswith("/"):
        path = "/" + path
    return f"{chat_api_base_url()}{path}"


def _stream_include_usage() -> bool:
    return os.environ.get("CHAT_API_STREAM_INCLUDE_USAGE", "1").strip().lower() not in ("0", "false", "no", "off")


def _extra_headers() -> Dict[str, str]:
    raw = os.environ.get("CHAT_API_EXTRA_HEADERS_JSON", "").strip()
    if not raw:
        return {}
    try:
        o = json.loads(raw)
        if not isinstance(o, dict):
            return {}
        return {str(k): str(v) for k, v in o.items()}
    except json.JSONDecodeError:
        return {}


def _http_error_message(code: int, body: str) -> str:
    return f"HTTP {code}: {body[:8000]}"


def _raise_http_error_from_upstream(ex: urllib.error.HTTPError, payload: dict) -> None:
    text = ex.read().decode("utf-8", errors="replace") if ex.fp else ""
    base = chat_api_base_url()
    model = payload.get("model") if isinstance(payload.get("model"), str) else None
    st, detail = _map_upstream_http_for_client(ex.code, text, base_url=base, model=model)
    raise HTTPException(status_code=st, detail=detail) from ex


def chat_completion_request(payload: dict) -> dict:
    key = chat_api_key()
    if not key:
        raise HTTPException(status_code=500, detail="CHAT_API_KEY / DEEPSEEK_API_KEY empty")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json; charset=utf-8",
    }
    headers.update(_extra_headers())
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(chat_completions_url(), data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as ex:
        _raise_http_error_from_upstream(ex, payload)
    except urllib.error.URLError as ex:
        raise HTTPException(status_code=502, detail=str(getattr(ex, "reason", ex))) from ex
    return json.loads(raw)


def _safe_json_loads(line: str) -> Optional[dict]:
    try:
        o = json.loads(line)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        return None


def chat_completion_stream(payload: dict) -> Iterator[dict]:
    key = chat_api_key()
    if not key:
        raise HTTPException(status_code=500, detail="CHAT_API_KEY / DEEPSEEK_API_KEY empty")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json; charset=utf-8",
    }
    headers.update(_extra_headers())
    stream_body: Dict[str, Any] = dict(payload)
    stream_body["stream"] = True
    if _stream_include_usage():
        stream_body["stream_options"] = {"include_usage": True}
    url = chat_completions_url()
    attempt = 0
    stream = None
    while attempt < 2:
        body_bytes = json.dumps(stream_body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
        try:
            stream = urllib.request.urlopen(req, timeout=300)
            break
        except urllib.error.HTTPError as ex:
            if attempt == 0 and ex.code == 400 and "stream_options" in stream_body:
                try:
                    ex.read()
                except Exception:
                    pass
                stream_body.pop("stream_options", None)
                attempt = 1
                continue
            _raise_http_error_from_upstream(ex, stream_body)
        except urllib.error.URLError as ex:
            raise HTTPException(status_code=502, detail=str(getattr(ex, "reason", ex))) from ex
    if stream is None:
        raise HTTPException(status_code=502, detail="stream open failed")
    try:
        while True:
            raw_line = stream.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            if data == "[DONE]":
                break
            obj = _safe_json_loads(data)
            if obj is not None:
                yield obj
    finally:
        stream.close()
