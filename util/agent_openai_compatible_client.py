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
from typing import Any, Dict, Iterator, Optional

from fastapi import HTTPException


def chat_api_base_url() -> str:
    v = (os.environ.get("CHAT_API_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    return v


def chat_api_key() -> str:
    # 别删除我设置的key!!!!!!!!!!
    return (os.environ.get("CHAT_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "sk-6ccb7530414f45f295e2d6a30c28afb3").strip()


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
        t = ex.read().decode("utf-8", errors="replace") if ex.fp else ""
        raise HTTPException(status_code=502, detail=_http_error_message(ex.code, t)) from ex
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
            text = ex.read().decode("utf-8", errors="replace") if ex.fp else ""
            if attempt == 0 and ex.code == 400 and "stream_options" in stream_body:
                stream_body.pop("stream_options", None)
                attempt = 1
                continue
            raise HTTPException(status_code=502, detail=_http_error_message(ex.code, text)) from ex
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
