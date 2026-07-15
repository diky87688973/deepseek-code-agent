# -*- coding: utf-8 -*-
"""
multimodal_proxy: Cursor <-> DeepSeek/GLM API 代理
- 入站：Cursor 的 OpenAI 格式 → DeepSeek/GLM 格式
- 出站：DeepSeek/GLM 响应 → OpenAI 标准格式
"""
from __future__ import annotations
import configparser, io, json, logging, os, re, sys, tempfile, uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from flask import Flask, Response, jsonify, request

logging.basicConfig(level=logging.INFO, format="[mp] %(message)s")
log = logging.getLogger("multimodal_proxy")

CONFIG_PATH = Path(__file__).resolve().parent / "config.ini"

# ── 配置加载 ────────────────────────────────────────────────────


def _load_cfg() -> dict:
    cfg = configparser.ConfigParser()
    cfg.read(str(CONFIG_PATH), encoding="utf-8")
    out = {}
    if cfg.has_section("model_reasoning"):
        out["ds_base_url"] = cfg.get("model_reasoning", "api_base_url", fallback="https://api.deepseek.com").rstrip("/")
        out["ds_api_key"] = cfg.get("model_reasoning", "api_key", fallback="").strip()
        out["default_model"] = cfg.get("model_reasoning", "default_model", fallback="deepseek-v4-flash")
    if cfg.has_section("model_vision"):
        out["vision_model"] = cfg.get("model_vision", "default_model", fallback="glm-5v-turbo")
    if cfg.has_section("model_vision"):
        out["glm_base_url"] = cfg.get("model_vision", "api_base_url", fallback="https://open.bigmodel.cn/api/paas/v4").rstrip("/")
        out["glm_api_key"] = cfg.get("model_vision", "api_key", fallback="").strip()
    else:
        out["glm_base_url"] = "https://open.bigmodel.cn/api/paas/v4"
        out["glm_api_key"] = ""
    if not out["glm_base_url"].endswith("/chat/completions"):
        out["glm_base_url"] += "/chat/completions"
    if not out["ds_base_url"].endswith("/chat/completions"):
        out["ds_base_url"] += "/chat/completions"
    if cfg.has_section("multimodal_proxy"):
        out["port"] = cfg.getint("multimodal_proxy", "port", fallback=18802)
        out["api_key"] = cfg.get("multimodal_proxy", "api_key", fallback="").strip()
        out["bind"] = cfg.get("multimodal_proxy", "bind", fallback="127.0.0.1")
        out["debug_log"] = cfg.getboolean("multimodal_proxy", "debug_log", fallback=True)
    return out


CFG = _load_cfg()
app = Flask(__name__)


# ── API 认证 ────────────────────────────────────────────────────


@app.before_request
def _check_auth():
    if request.method == "OPTIONS":
        return None
    cfg_key = CFG.get("api_key", "")
    if not cfg_key:
        return None
    auth = request.headers.get("Authorization", "")
    if auth == f"Bearer {cfg_key}":
        return None
    return jsonify({"error": {"message": "Unauthorized", "type": "auth_error"}}), 401


# ── 通用 HTTP 请求 ──────────────────────────────────────────────


def _http_post(url: str, headers: dict, body: dict) -> dict:
    import sys as _hsys
    _hsys.stderr.write(f"[http] >>> POST {url}\n"); _hsys.stderr.flush()
    _hsys.stderr.write(f"[http] >>> headers: {json.dumps({k:v for k,v in headers.items() if k.lower() != "authorization"})}\n"); _hsys.stderr.flush()
    _hsys.stderr.write(f"[http] >>> body tools: {len(body.get("tools",[]))} model={body.get("model")} stream={body.get("stream")}\n"); _hsys.stderr.flush()
    if "deepseek" in url: _hsys.stderr.write("[http] >>> full body: " + json.dumps(body, ensure_ascii=False)[:2000] + "\n"); _hsys.stderr.flush()
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            _hsys.stderr.write(f"[http] <<< status={resp.status} finish_reason={result.get("choices",[{}])[0].get("finish_reason")} tool_calls={len(result.get("choices",[{}])[0].get("message",{}).get("tool_calls",[]))}\n"); _hsys.stderr.flush()
            return result
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        _hsys.stderr.write(f"[http] <<< ERROR {e.code}: {err_body[:300]}\n"); _hsys.stderr.flush()
        raise RuntimeError(f"Upstream {e.code}: {err_body[:500]}")


def _has_image(msg: dict) -> bool:
    content = msg.get("content", "")
    if isinstance(content, list):
        return any(p.get("type") == "image_url" for p in content)
    # 字符串内容不判断图片（Cursor 对话可能包含 "image_url" 文本）
    return False


def _extract_images(msg: dict) -> list:
    content = msg.get("content", "")
    images = []
    if isinstance(content, list):
        for p in content:
            if p.get("type") == "image_url":
                images.append(p["image_url"]["url"])
    return images


def _strip_images(msg: dict) -> dict:
    content = msg.get("content", "")
    if isinstance(content, list):
        text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
        msg["content"] = " ".join(text_parts) if text_parts else ""
    return msg


_VISION_TOOL = {
    "type": "function",
    "function": {
        "name": "vision",
        "description": "图像分析工具（唯一的图片识别工具）：当用户上传或截图发来任何图片时，必须调用此工具来分析图片内容。调用时必须传入 prompt（分析提示词，描述用户想了解什么）和 image_url（图片地址）。如有任何图片相关问题，优先使用此工具，不要用其他工具替代。",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "What to look for or analyze in the image"
                },
                "image_url": {
                    "type": "string",
                    "description": "The image URL or base64 data URI to analyze"
                }
            },
            "required": ["prompt", "image_url"]
        },
    },
}


# ── SSE 输出（直接打印到控制台） ─────────────────────────────


def _emit_sse(event_data: str):
    """打印 SSE 事件到控制台，每条独立一行"""
    import sys as _sys
    sys.stderr.write("_emit_sse called, debug_log=" + str(CFG.get("debug_log", "missing")) + "\n")
    _sys.stderr.write(f"[debug] CFG.debug_log={CFG.get("debug_log", "NOT_FOUND")}\n")
    _sys.stderr.flush()
    if not CFG.get("debug_log", True):
        return
    msg = f">> {event_data}"
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


# ── Chat Completion ──────────────────────────────────────────────


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    import sys
    sys.stdout.write("[req] request received\n"); sys.stdout.flush()
    data = request.get_json(force=True) or {}
    sys.stdout.write("[req] parsed body\n"); sys.stdout.flush()
    messages = data.get("messages", [])
    sys.stdout.write(f"[req] messages={len(messages)}\n"); sys.stdout.flush()
    stream = data.get("stream", False)
    sys.stdout.write(f"[req] stream={stream}\n"); sys.stdout.flush()
    model = CFG["default_model"]
    sys.stdout.write(f"[req] model={model}\n"); sys.stdout.flush()
    tools = data.get("tools", [])
    sys.stdout.write(f"[req] tools={len(tools) if tools else 0}\n"); sys.stdout.flush()
    tool_choice = data.get("tool_choice", "auto")
    sys.stdout.write(f"[req] tool_choice={tool_choice}\n"); sys.stdout.flush()

    # 图片检测：只检测最新一条用户消息
    has_image = False
    images = []
    for m in reversed(messages):
        if m.get("role") == "user":
            if _has_image(m):
                has_image = True
                images = _extract_images(m)
            break

    if not has_image or not images:
        return _proxy_to_deepseek(messages, model, tools, tool_choice, stream)

    # 含图片：先发占位防超时，再内部处理
    if stream:
        def gen_vision():
            import sys as _vsys
            import json as _json
            _vsys.stderr.write("处理中...\n"); _vsys.stderr.flush()
            yield "data: {\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"图片识别中。\"},\"finish_reason\":null}]}\n\n"
            try:
                import threading as _vth
                _ka_event = _vth.Event()
                def _ka():
                    while not _ka_event.is_set():
                        import time as _vtime
                        _vtime.sleep(1)
                        try:
                            _vsys.stdout.write(":\n\n"); _vsys.stdout.flush()
                        except:
                            pass
                _ka_thread = _vth.Thread(target=_ka, daemon=True)
                _ka_thread.start()
                enhanced_tools = [_VISION_TOOL]
                _vsys.stderr.write(f"[vision] only vision tool, ignoring Cursor tools\n"); _vsys.stderr.flush()
                _image_map = {}
                processed = []
                for m in messages:
                    if _has_image(m):
                        sm = dict(m)
                        c = sm.get("content", "")
                        if isinstance(c, list):
                            img_idx = 0
                            new_parts = []
                            for p in c:
                                if p.get("type") == "image_url":
                                    img_idx += 1
                                    _vsys.stderr.write(f"[vision] assign image id: 图片{img_idx} for m role={m.get("role")}\n"); _vsys.stderr.flush()
                                    _image_map["图片" + str(img_idx)] = p["image_url"]["url"]
                                    new_parts.append({"type": "text", "text": f"[图片{img_idx}]"})
                                else:
                                    new_parts.append(p)
                            sm["content"] = new_parts
                        processed.append(sm)
                    else:
                        processed.append(m)
                _image_map = {}
                _vsys.stderr.write(f"[vision] calling DeepSeek with {len(enhanced_tools)} tools: {[t.get("function",{}).get("name","?") for t in enhanced_tools]}\n"); _vsys.stderr.flush()
                result = _chat_completion(processed, model=model, tools=enhanced_tools, tool_choice="auto")
                _vsys.stderr.write("[vision] DeepSeek done fr=" + str(result.get("choices",[{}])[0].get("finish_reason")) + " fn=" + str(result.get("choices",[{}])[0].get("message",{}).get("tool_calls",[{}])[0].get("function",{}).get("name","?")) + " tcs=" + str(len(result.get("choices",[{}])[0].get("message",{}).get("tool_calls",[]))) + "\n"); _vsys.stderr.flush()
                if _has_vision_tool_call(result):
                    _vsys.stderr.write("[vision] vision called, collecting...\n"); _vsys.stderr.flush()
                    vr = _collect_vision_results(result, images, _image_map)
                    _vsys.stderr.write(f"[vision] collected {len(vr)} results\n"); _vsys.stderr.flush()
                    if not vr:
                        _vsys.stderr.write("[vision] GLM failed, yielding error\n"); _vsys.stderr.flush()
                        _ka_event.set()
                        yield f"data: {_json.dumps({"choices":[{"index":0,"delta":{"content":"\n<br>【图片识别失败，请重试或检查图片格式】"},"finish_reason":"stop"}]})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    if vr:
                        vm = list(processed)
                        vm.append({"role": "assistant", "content": None, "tool_calls": result["choices"][0]["message"]["tool_calls"]})
                        for v in vr:
                            vm.append({"role": "tool", "tool_call_id": v["tool_call_id"], "content": v["content"]})
                        try:
                            final = _chat_completion(vm, model=model, tools=enhanced_tools, tool_choice=tool_choice)
                            result = final or result
                        except Exception as exc:
                            _vsys.stderr.write(f"[vision] final call error: {exc}\n"); _vsys.stderr.flush()
                for ch in result.get("choices", []):
                    msg = ch.get("message", {})
                    if msg.get("tool_calls"):
                        msg["tool_calls"] = [tc for tc in msg["tool_calls"] if tc.get("function",{}).get("name") != "vision"]
                _ka_event.set(); _ka_thread.join(timeout=1)
                _vsys.stderr.write("[vision] yielding result\n"); _vsys.stderr.flush()
                _vsys.stderr.write(f"[vision] final content: {(result.get("choices",[{}])[0].get("message",{}).get("content") or "")[:500]}\n"); _vsys.stderr.flush()
                _vsys.stderr.write(f"[vision] yielding result to Cursor\n"); _vsys.stderr.flush()
                # 以流式 chunk 格式发送最终结果
                _vsys.stderr.write(f"[vision] final finish_reason={result.get("choices",[{}])[0].get("finish_reason")}\n"); _vsys.stderr.flush()
                _content = result.get("choices",[{}])[0].get("message",{}).get("content") or ""
                if _content:
                    yield f"data: {_json.dumps({"choices":[{"index":0,"delta":{"content":"<hr>" + _content},"finish_reason":"stop"}]})}\n\n"
                yield "data: [DONE]\n\n"
            except GeneratorExit:
                _ka_event.set()
                raise
            except Exception as exc:
                _ka_event.set()
                _vsys.stderr.write(f"[vision] ERROR: {exc}\n"); _vsys.stderr.flush()
                yield f"data: {_json.dumps({'error': str(exc)})}\n\n"
                yield "data: [DONE]\n\n"
        return Response(gen_vision(), mimetype="text/event-stream")

    # 非流式 vision
    enhanced_tools = [_VISION_TOOL]
    processed_messages = []
    for m in messages:
        if _has_image(m):
            processed_messages.append(_strip_images(m))
        else:
            processed_messages.append(m)

    result = _chat_completion(
        processed_messages,
        model=model,
        tools=enhanced_tools,
        tool_choice=tool_choice,
        api_base_url=CFG["ds_base_url"],
        api_key=CFG["ds_api_key"],
    )
    if not _has_vision_tool_call(result):
        return _clean_response(result)
    vision_results = _collect_vision_results(result, images)
    if not vision_results:
        return _clean_response(result)
    vision_messages = list(processed_messages)
    vision_messages.append({"role": "assistant", "content": None, "tool_calls": result["choices"][0]["message"]["tool_calls"]})
    for vr in vision_results:
        vision_messages.append({"role": "tool", "tool_call_id": vr["tool_call_id"], "content": vr["content"]})
    try:
        final = _chat_completion(
            vision_messages,
            model=model,
            tools=enhanced_tools,
            tool_choice=tool_choice,
            api_base_url=CFG["ds_base_url"],
            api_key=CFG["ds_api_key"],
        )
    except RuntimeError:
        return _clean_response(result)
    if not final:
        return _clean_response(result)
    return _clean_response(final)


def _chat_completion(messages, *, model, tools=None, tool_choice=None, api_base_url=None, api_key=None):
    """向模型发送聊天补全请求（非流式）。"""
    body = {"model": model, "messages": messages, "stream": False}
    if tools:
        body["tools"] = tools
    if tool_choice:
        body["tool_choice"] = tool_choice
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key or CFG['ds_api_key']}"}
    try:
        return _http_post(api_base_url or CFG["ds_base_url"], headers, body)
    except RuntimeError as e:
        raise


def _has_vision_tool_call(response: dict) -> bool:
    for choice in response.get("choices", []):
        for tc in choice.get("message", {}).get("tool_calls", []):
            if tc.get("function", {}).get("name") == "vision":
                return True
    return False


def _collect_vision_results(response: dict, images: list, image_map: dict = None) -> list:
    results = []
    for choice in response.get("choices", []):
        for tc in choice.get("message", {}).get("tool_calls", []):
            if tc.get("function", {}).get("name") != "vision":
                continue
            tool_call_id = tc["id"]
            try:
                args = json.loads(tc["function"].get("arguments", "{}"))
                prompt = args.get("prompt", "Describe what you see in detail.")
                img_url = args.get("image_url", images[0] if images else "")
                if image_map and img_url in image_map:
                    img_url = image_map[img_url]
                    import sys as _vsys2; _vsys2.stderr.write(f"[vision] resolved image ref {img_url[:50]}\n"); _vsys2.stderr.flush()
                import sys as _vsys2; _vsys2.stderr.write(f"[vision] img_url starts_with={str(img_url)[:50]} len={len(str(img_url))}\n"); _vsys2.stderr.flush()
                # 本地文件路径转 base64 data URI
                if img_url and not img_url.startswith("data:") and not img_url.startswith("http"):
                    import os as _os
                    import re as _vre
                    _local_path = _vre.sub(r"^file:///", "", img_url)
                    if _os.path.isfile(_local_path):
                        img_url = _local_path
                        import base64 as _b64
                        with open(img_url, "rb") as _f:
                            _raw = _f.read()
                        _ext = _os.path.splitext(img_url)[1].lower()
                        _mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
                        _mt = _mime.get(_ext.lstrip("."), "image/png")
                        img_url = f"data:{_mt};base64,{_b64.b64encode(_raw).decode()}"
                        import sys; sys.stderr.write(f"[vision] converted local file to data URI, len={len(img_url)}\n"); sys.stderr.flush()
                glm_body = {
                    "model": CFG.get("vision_model", "glm-5v-turbo"),
                    "messages": [
                        {"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": img_url}},
                            {"type": "text", "text": prompt}
                        ]}
                    ],
                    "stream": False,
                }
                glm_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {CFG['glm_api_key']}"}
                import sys as _vsys
                _vsys.stderr.write(f"[vision] GLM prompt: {prompt[:200]}\n"); _vsys.stderr.flush()
                import urllib.request as _vg; _vg_req = _vg.Request(CFG["glm_base_url"], data=json.dumps(glm_body, ensure_ascii=False).encode("utf-8"), headers=glm_headers, method="POST"); _vg_resp = _vg.urlopen(_vg_req, timeout=30); glm_resp = json.loads(_vg_resp.read().decode("utf-8"))
                vision_text = glm_resp["choices"][0]["message"]["content"]
                _vsys.stderr.write(f"[vision] GLM result: {vision_text[:300]}\n"); _vsys.stderr.flush()
                results.append({"tool_call_id": tool_call_id, "content": vision_text})
            except Exception as exc:
                import sys; sys.stderr.write(f"[vision] GLM error: {exc}\n"); sys.stderr.flush()
    return results


def _clean_response(response: dict) -> Response:
    """剔除响应中非标准字段（避免 Cursor 不认识）。"""
    for choice in response.get("choices", []):
        msg = choice.get("message", {})
        # 过滤 vision 工具调用
        if not msg.get("tool_calls"):
            continue
        filtered = [tc for tc in msg["tool_calls"] if tc.get("function", {}).get("name") != "vision"]
        if filtered:
            msg["tool_calls"] = filtered
        else:
            msg.pop("tool_calls", None)
    return jsonify(response)

def _proxy_to_deepseek(messages, model, tools, tool_choice, stream):
    print(f"[_proxy] stream={stream}, tools={len(tools) if tools else 0}", flush=True)
    tools = list(tools) + [_VISION_TOOL] if tools else [_VISION_TOOL]
    # 清洗消息：剔除 content 列表中的 image_url 条目（DeepSeek 不支持）
    cleaned = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            filtered = [p for p in c if p.get("type") != "image_url"]
            if filtered:
                m = dict(m)
                m["content"] = filtered
        cleaned.append(m)
    messages = cleaned
    body = {"model": model, "messages": messages, "stream": stream}
    if tools:
        body["tools"] = tools
    if tool_choice:
        body["tool_choice"] = tool_choice
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {CFG['ds_api_key']}"}

    if stream:
        def generate():
            print("[generate] STARTED", flush=True)
            req = Request(CFG["ds_base_url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
            try:
                with urlopen(req, timeout=120) as resp:
                    buf = ""
                    leftover = b""
                    while True:
                        chunk = resp.read(4096)
                        if not chunk:
                            # 流结束，丢弃残留的残缺字节
                            break
                        chunk = leftover + chunk
                        try:
                            text = chunk.decode("utf-8")
                            leftover = b""
                        except UnicodeDecodeError as e:
                            text = chunk[:e.start].decode("utf-8")
                            leftover = chunk[e.start:]
                        buf += text
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("data: "):
                                event_data = line[6:]
                                if event_data == "[DONE]":
                                    _emit_sse("data: [DONE]")
                                    yield "data: [DONE]\n\n"
                                    continue
                                try:
                                    obj = json.loads(event_data)
                                    # reasoning_content 原样保留
                                    # 同时拼接到 content，用标记包裹
                                    if not hasattr(generate, "_thinking_active"):
                                        generate._thinking_active = False
                                    for ch_obj in obj.get("choices", []):
                                        dd = ch_obj.get("delta", {})
                                        rrc = dd.get("reasoning_content")
                                        cct = dd.get("content")
                                        if rrc is not None:
                                            if not generate._thinking_active:
                                                generate._thinking_active = True
                                                dd["content"] = "Thought for<hr>" + (rrc or "")
                                            else:
                                                dd["content"] = rrc or ""
                                        elif cct is not None and generate._thinking_active:
                                            dd["content"] = "<hr><br>" + cct
                                            generate._thinking_active = False
                                    _sse = f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
                                    _emit_sse(_sse.strip())
                                    yield _sse
                                except json.JSONDecodeError:
                                    _emit_sse(f"data: {event_data}")
                                    yield f"data: {event_data}\n\n"
            except HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                err_msg = f"data: {json.dumps({'error': str(e), 'detail': err_body[:300]})}\n\n"
                _emit_sse(err_msg.strip())
                yield err_msg
        print("[_proxy] returning streaming Response", flush=True)
        return Response(generate(), mimetype="text/event-stream")

    try:
        data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(CFG["ds_base_url"], data=data_bytes, headers=headers, method="POST")
        with urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            result = json.loads(raw)
            # 字段映射：reasoning_content → reasoning
            for ch in result.get("choices", []):
                msg = ch.get("message", {})
                if "reasoning_content" in msg:
                    msg["reasoning"] = msg.pop("reasoning_content")
            _emit_sse(json.dumps(result, ensure_ascii=False)[:500])
            return jsonify(result), resp.status
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return jsonify({"error": {"message": err_body[:500], "type": "upstream_error"}}), e.code


# ── 其他端点 ────────────────────────────────────────────────────


@app.route("/v1/models", methods=["GET"])
def list_models():
    return jsonify({
        "object": "list",
        "data": [
            {"id": CFG["default_model"], "object": "model"},
            {"id": CFG.get("vision_model", "glm-5v-turbo"), "object": "model"},
        ],
    })


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = CFG.get("port", 18802)
    host = CFG.get("bind", "127.0.0.1")
    log.info("multimodal proxy starting on %s:%s", host, port)
    log.info("deepseek: %s", CFG["ds_base_url"])
    log.info("glm:      %s", CFG["glm_base_url"])
    from waitress import serve
    serve(app, host=host, port=port)
