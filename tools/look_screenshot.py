# -*- coding: utf-8 -*-
"""look_screenshot：主模型给出问图 prompt，宿主调用独立视觉模型。"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, List

import agent_common as ac


def _vision_model_name() -> str:
    try:
        from util.config_loader import load_config

        cfg = load_config(verbose=False)
        m = str(cfg.get("AGENT_VISION_MODEL") or "").strip()
        if m:
            return m
    except Exception:
        pass
    return "glm-5v-turbo"


def _image_url_for(path: Path) -> str:
    raw = path.read_bytes()
    mime = "image/jpeg"
    suf = path.suffix.lower()
    if suf == ".png":
        mime = "image/png"
    elif suf == ".webp":
        mime = "image/webp"
    elif suf == ".gif":
        mime = "image/gif"
    elif suf in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _call_vision(prompt: str, image_paths: List[Path]) -> str:
    from util.agent_model_provider import get_provider, provider_api_base_url, provider_api_key
    from util.agent_openai_compatible_client import chat_completion_request

    model = _vision_model_name()
    provider = get_provider(model)
    base_url = provider_api_base_url(provider)
    api_key = provider_api_key(provider)
    if not base_url or not api_key:
        raise RuntimeError(f"视觉模型 {model} 的 API 未配置（provider={provider}）")

    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for p in image_paths:
        content.append({"type": "image_url", "image_url": {"url": _image_url_for(p)}})

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
    }
    resp = chat_completion_request(payload, base_url=base_url, api_key=api_key)
    choices = resp.get("choices") if isinstance(resp, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"视觉模型无 choices：{json.dumps(resp, ensure_ascii=False)[:800]}")
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    text = ""
    if isinstance(msg, dict):
        c = msg.get("content")
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            parts = []
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
            text = "\n".join(parts)
    text = str(text or "").strip()
    if not text:
        raise RuntimeError("视觉模型返回空内容")
    return text


def agent_main(
    *,
    prompt: str = "",
    attachment_ids: Any = None,
    paths: Any = None,
    path: str = "",
    conversation_id: str = "",
    **_kwargs,
) -> dict:
    q = str(prompt or "").strip()
    if not q:
        return ac.err(ValueError("prompt 必填：须表达用户本轮意图，并导向用户需要的答案"))
    if not str(conversation_id or "").strip():
        return ac.err(ValueError("缺少 conversation_id（由宿主注入）"))

    id_list: List[str] = []
    if isinstance(attachment_ids, str) and attachment_ids.strip():
        id_list = [x.strip() for x in attachment_ids.split(",") if x.strip()]
    elif isinstance(attachment_ids, (list, tuple)):
        id_list = [str(x).strip() for x in attachment_ids if str(x).strip()]

    path_list: List[str] = []
    if path and str(path).strip():
        path_list.append(str(path).strip())
    if isinstance(paths, str) and paths.strip():
        path_list.extend([x.strip() for x in paths.split(",") if x.strip()])
    elif isinstance(paths, (list, tuple)):
        path_list.extend([str(x).strip() for x in paths if str(x).strip()])

    try:
        from agent_v4.core.attachments import get_turn_attachments, resolve_attachment_paths, set_turn_attachments

        if str(conversation_id or "").strip() and not get_turn_attachments(conversation_id):
            try:
                from agent_v4.live_state import CONVERSATIONS

                for _m in reversed(CONVERSATIONS.get(str(conversation_id).strip()) or []):
                    if _m.get("role") != "user":
                        continue
                    _prev = _m.get("_attachments")
                    if isinstance(_prev, list) and _prev:
                        set_turn_attachments(
                            conversation_id,
                            [dict(x) for x in _prev if isinstance(x, dict)],
                        )
                    break
            except Exception:
                pass

        files = resolve_attachment_paths(str(conversation_id or ""), id_list or None, path_list or None)
    except Exception as exc:
        return ac.err(exc)

    if not files:
        return ac.err(
            FileNotFoundError(
                "未找到可查看的截图（本轮无附件，或刷新后浏览器预览已丢）。"
                "请用户重新粘贴图片到输入框后发送，再调用 look_screenshot。"
            )
        )

    try:
        text = _call_vision(q, files)
    except Exception as exc:
        return ac.err(exc)

    return ac.ok(
        {
            "vision_model": _vision_model_name(),
            "prompt": q,
            "paths": [str(p) for p in files],
            "attachment_ids": id_list,
            "description": text,
        }
    )
