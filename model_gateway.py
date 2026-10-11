# -*- coding: utf-8 -*-
"""
model_gateway: 本地模型网关 —— 对外提供 OpenAI 兼容 API，后端转发 DeepSeek
- 入站：OpenAI 兼容格式 → DeepSeek
- 出站：DeepSeek 响应 → OpenAI 标准格式
- 图片：image_url 原生透传给 DeepSeek 视觉模型（不再走第三方视觉模型兜底）
"""
from __future__ import annotations
import configparser, datetime, io, json, logging, os, re, sys, tempfile, uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from flask import Flask, Response, jsonify, request

logging.basicConfig(level=logging.INFO, format="[mg] %(message)s")
log = logging.getLogger("model_gateway")

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
    if not out["ds_base_url"].endswith("/chat/completions"):
        out["ds_base_url"] += "/chat/completions"
    if cfg.has_section("model_gateway"):
        out["port"] = cfg.getint("model_gateway", "port", fallback=18802)
        out["api_key"] = cfg.get("model_gateway", "api_key", fallback="").strip()
        out["bind"] = cfg.get("model_gateway", "bind", fallback="127.0.0.1")
        out["debug_log"] = cfg.getboolean("model_gateway", "debug_log", fallback=True)
        out["context_dump"] = cfg.getboolean("model_gateway", "context_dump", fallback=True)
        out["context_dir"] = cfg.get("model_gateway", "context_dir", fallback="temp/proxy_context").strip()
        out["prompt_inject"] = cfg.getboolean("model_gateway", "prompt_inject", fallback=False)
        out["prompt_mode"] = cfg.get("model_gateway", "prompt_mode", fallback="prepend").strip()
        out["prompt_parts"] = cfg.get("model_gateway", "prompt_parts", fallback="code_hint,user_rules,tool_agent,catalog_hints").strip()
        out["prompt_user_rules_file"] = cfg.get("model_gateway", "prompt_user_rules_file", fallback="").strip()
    if cfg.has_section("workspace"):
        out["data_root"] = cfg.get("workspace", "runtime_data_root", fallback="").strip()
    if cfg.has_section("agent"):
        out["agent_user_rules_file"] = cfg.get("agent", "user_rules_file", fallback="").strip()
    return out


CFG = _load_cfg()
app = Flask(__name__)

# ── 完整上下文落盘 ──────────────────────────────────────────────

_CONTEXT_READY = False
_CONTEXT_DIR = None


def _shrink_data_uris(node):
    """递归把 data:...;base64,... 的超长内联图片换成占位说明，其余内容原样保留。"""
    if isinstance(node, dict):
        return {k: _shrink_data_uris(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_shrink_data_uris(v) for v in node]
    if isinstance(node, str) and node.startswith("data:") and ";base64," in node:
        head, _, b64 = node.partition(";base64,")
        return head + ";base64,<已省略 " + str(len(b64)) + " 字符>"
    return node


def _context_dir():
    """解析并创建上下文落盘目录；关闭或失败时返回 None。"""
    global _CONTEXT_READY, _CONTEXT_DIR
    if _CONTEXT_READY:
        return _CONTEXT_DIR
    _CONTEXT_READY = True
    if not CFG.get("context_dump", True):
        return None
    try:
        raw = (CFG.get("context_dir") or "temp/proxy_context").strip()
        path = Path(raw)
        if not path.is_absolute():
            path = Path(CFG.get("data_root") or "D:/AI_DATA_ROOT") / path
        path.mkdir(parents=True, exist_ok=True)
        _CONTEXT_DIR = path
    except Exception as exc:
        sys.stderr.write(f"[ctx] 上下文目录创建失败: {exc}\n")
        sys.stderr.flush()
    return _CONTEXT_DIR


def _dump_context(body: dict, target: str) -> None:
    """把即将发给模型的完整 body 写成单独 JSON 文件，便于观察。"""
    try:
        out_dir = _context_dir()
        if out_dir is None:
            return
        now = datetime.datetime.now()
        name = now.strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8] + ".json"
        messages = body.get("messages") or []
        tools = body.get("tools") or []
        payload = {
            "_meta": {
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "target": target,
                "prompt_inject": bool(CFG.get("prompt_inject", False)),
                "prompt_mode": (CFG.get("prompt_mode") or "") if CFG.get("prompt_inject", False) else "",
                "model": body.get("model"),
                "stream": body.get("stream"),
                "messages": len(messages),
                "tools": len(tools),
            },
            "_body": _shrink_data_uris(body),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        (out_dir / name).write_text(text, encoding="utf-8")
        sys.stdout.write(
            "[ctx] 完整上下文 -> %s (messages=%d, tools=%d, stream=%s, %.1f KB)\n"
            % (name, len(messages), len(tools), body.get("stream"), len(text.encode("utf-8")) / 1024.0)
        )
        sys.stdout.flush()
    except Exception as exc:
        try:
            sys.stderr.write(f"[ctx] 上下文落盘失败: {exc}\n")
            sys.stderr.flush()
        except Exception:
            pass


# ── 项目系统提示词注入 ──────────────────────────────────────────

_PROMPT_CACHE = None


def _project_system_prompt() -> str:
    """从项目 util.agent_prompt_constants_v2 构建系统提示词（懒加载并缓存）。"""
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE
    _PROMPT_CACHE = ""
    try:
        root = str(Path(__file__).resolve().parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from util import agent_prompt_constants_v2 as apc

        wanted = [p.strip() for p in (CFG.get("prompt_parts") or "").split(",") if p.strip()]
        segments = []
        if "code_hint" in wanted:
            segments.append(str(apc.AGENT_CODE_HINT_SYSTEM_PROMPT or "").strip())
        if "user_rules" in wanted:
            rules_file = (CFG.get("prompt_user_rules_file") or CFG.get("agent_user_rules_file") or "").strip()
            data_root = Path(CFG.get("data_root") or "D:/AI_DATA_ROOT")
            segments.append(str(apc.resolve_user_rules_system_prompt(rules_file, data_root) or "").strip())
        if "tool_agent" in wanted:
            segments.append(str(apc.TOOL_AGENT_SYSTEM_PROMPT or "").strip())
        if "catalog_hints" in wanted:
            catalog_path = Path(__file__).resolve().parent / "tools" / "tool_list_agent.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            segments.append(str(apc.build_catalog_hints_system_prompt(catalog) or "").strip())
        _PROMPT_CACHE = "\n\n---\n\n".join(s for s in segments if s)
        sys.stdout.write(
            "[prompt] 项目系统提示词已构建: %d 字符, 段落=%s\n" % (len(_PROMPT_CACHE), ",".join(wanted))
        )
        sys.stdout.flush()
    except Exception as exc:
        sys.stderr.write("[prompt] 项目系统提示词构建失败: %s\n" % exc)
        sys.stderr.flush()
    return _PROMPT_CACHE


def _inject_system_prompt(messages):
    """按配置注入项目系统提示词：prepend 保留客户端 system，replace 先移除客户端 system。"""
    if not CFG.get("prompt_inject", False):
        return messages
    text = _project_system_prompt()
    if not text:
        return messages
    out = list(messages)
    mode = (CFG.get("prompt_mode") or "prepend").strip().lower()
    if mode == "replace":
        out = [m for m in out if m.get("role") != "system"]
    out.insert(0, {"role": "system", "content": text})
    sys.stdout.write("[prompt] 已注入 (mode=%s, %d 字符)\n" % (mode, len(text)))
    sys.stdout.flush()
    return out


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


def _count_images(messages) -> int:
    """统计消息里 image_url 块的数量（用于日志判断是否走了原生视觉）。"""
    n = 0
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list):
            n += sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")
    return n


# ── SSE 输出（直接打印到控制台） ─────────────────────────────


def _emit_sse(event_data: str):
    """回显 SSE 事件，每条独立一行。由 [model_gateway] debug_log 开关控制。"""
    if not CFG.get("debug_log", False):
        return
    sys.stdout.write(f">> {event_data}\n")
    sys.stdout.flush()


# ── Chat Completion ──────────────────────────────────────────────


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    import sys
    sys.stdout.write("[req] request received\n"); sys.stdout.flush()
    data = request.get_json(force=True) or {}
    sys.stdout.write("[req] parsed body\n"); sys.stdout.flush()
    messages = _inject_system_prompt(data.get("messages", []))
    sys.stdout.write(f"[req] messages={len(messages)}\n"); sys.stdout.flush()
    stream = data.get("stream", False)
    sys.stdout.write(f"[req] stream={stream}\n"); sys.stdout.flush()
    model = CFG["default_model"]
    sys.stdout.write(f"[req] model={model}\n"); sys.stdout.flush()
    tools = data.get("tools", [])
    sys.stdout.write(f"[req] tools={len(tools) if tools else 0}\n"); sys.stdout.flush()
    tool_choice = data.get("tool_choice", "auto")
    sys.stdout.write(f"[req] tool_choice={tool_choice}\n"); sys.stdout.flush()

    # 图片：DeepSeek 已原生支持视觉，image_url 直接透传，不再走第三方视觉模型
    img_count = _count_images(messages)
    if img_count:
        sys.stdout.write(f"[req] 含图 {img_count} 张，原生透传 DeepSeek\n"); sys.stdout.flush()

    return _proxy_to_deepseek(messages, model, tools, tool_choice, stream)


def _proxy_to_deepseek(messages, model, tools, tool_choice, stream):
    print(f"[_proxy] stream={stream}, tools={len(tools) if tools else 0}", flush=True)
    # 消息与工具原样透传：DeepSeek 原生支持视觉，image_url 无需剥离，
    # 也不再注入内部的 vision 工具（避免污染客户端自己的工具列表）。
    body = {"model": model, "messages": messages, "stream": stream}
    if tools:
        body["tools"] = tools
    if tool_choice:
        body["tool_choice"] = tool_choice
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {CFG['ds_api_key']}"}
    _dump_context(body, CFG["ds_base_url"])

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
                                                # dd["content"] = "Thought for<hr>" + (rrc or "")
                                                dd["content"] = rrc or ""
                                            else:
                                                dd["content"] = rrc or ""
                                        elif cct is not None and generate._thinking_active:
                                            # dd["content"] = "<hr><br>" + cct
                                            dd["content"] = cct
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
        ],
    })


@app.route("/health")
def health():
    return jsonify({"ok": True})


# ── 日志落盘（控制台 + 文件双写）──────────────────────────────


class _Tee(io.TextIOBase):
    """写入时同时转发到控制台和日志文件。"""

    def __init__(self, console, logfile) -> None:
        self._console = console
        self._logfile = logfile

    def write(self, text):
        if not text:
            return 0
        for stream in (self._console, self._logfile):
            try:
                stream.write(text)
                stream.flush()
            except Exception:
                pass
        return len(text)

    def flush(self):
        for stream in (self._console, self._logfile):
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return bool(self._console.isatty())
        except Exception:
            return False

    def fileno(self):
        return self._console.fileno()

    @property
    def encoding(self):
        return getattr(self._console, "encoding", None) or "utf-8"

    @property
    def buffer(self):
        return getattr(self._console, "buffer", None)


def _tee_output_to_file():
    """把 stdout/stderr 同时写入 <runtime_data_root>/temp/model_gateway_日期.log。

    返回 (日志路径, 文件对象)；创建失败时返回 (None, None)，仅保留控制台输出。
    """
    try:
        log_dir = Path(CFG.get("data_root") or "D:/AI_DATA_ROOT") / "temp"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / ("model_gateway_" + datetime.datetime.now().strftime("%Y%m%d") + ".log")
        logfile = open(path, "a", encoding="utf-8", buffering=1, errors="replace")
    except Exception as exc:
        sys.stderr.write("[mg] 日志落盘不可用(%s)，本次仅输出到控制台\n" % exc)
        sys.stderr.flush()
        return None, None

    orig_out, orig_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(orig_out, logfile)
    sys.stderr = _Tee(orig_err, logfile)
    # logging.basicConfig 在模块导入时已绑定原 stderr，这里改指到 Tee
    for handler in logging.root.handlers:
        if isinstance(handler, logging.StreamHandler) and getattr(handler, "stream", None) in (orig_out, orig_err):
            handler.stream = sys.stderr
    return path, logfile


if __name__ == "__main__":
    port = CFG.get("port", 18802)
    host = CFG.get("bind", "127.0.0.1")
    log_path, _logfile = _tee_output_to_file()
    if log_path:
        sys.stdout.write("[mg] 启动时间: %s\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        sys.stdout.write("[mg] 日志文件: %s\n" % log_path)
        sys.stdout.flush()
    log.info("model gateway starting on %s:%s", host, port)
    log.info("deepseek: %s", CFG["ds_base_url"])
    from waitress import serve
    serve(app, host=host, port=port)
