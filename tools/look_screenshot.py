# -*- coding: utf-8 -*-
"""look_screenshot：主模型给出问图 prompt，宿主调用独立视觉模型。"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import agent_common as ac

_MAX_IMAGES = 4
_MAX_PROMPT_CHARS = 4000
_MAX_FILE_BYTES = 8 * 1024 * 1024
_ALLOWED_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_ATT_ID_RE = re.compile(r"^att_[A-Za-z0-9]{6,64}$")


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


def _as_str_list(val: Any, *, field: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """把 string / list / tuple / JSON 数组 规范成非空字符串列表。"""
    if val is None:
        return [], None
    if isinstance(val, bool) or isinstance(val, (int, float)):
        return None, f"{field} 类型无效（需要 string 或 string 数组），收到 {type(val).__name__}"
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return [], None
        if s[0] in "[{":
            try:
                parsed = json.loads(s)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                out = [str(x).strip() for x in parsed if str(x).strip()]
                return out, None
            if isinstance(parsed, str) and parsed.strip():
                return [parsed.strip()], None
            return None, f"{field} 的 JSON 无法解析为字符串列表"
        # 逗号分隔；兼容中文逗号
        parts = re.split(r"[,，;；\n]+", s)
        return [p.strip() for p in parts if p.strip()], None
    if isinstance(val, (list, tuple)):
        out: List[str] = []
        for i, x in enumerate(val):
            if x is None:
                continue
            if isinstance(x, (dict, list, tuple, bool)):
                return None, f"{field}[{i}] 类型无效（需要字符串路径或 id）"
            t = str(x).strip()
            if t:
                out.append(t)
        return out, None
    return None, f"{field} 类型无效（需要 string 或 string 数组），收到 {type(val).__name__}"


def _dedupe_keep_order(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _validate_att_id(aid: str) -> Optional[str]:
    if not aid:
        return "attachment_ids 含空 id"
    if ".." in aid or "/" in aid or "\\" in aid:
        return f"attachment_ids 非法 id（含路径字符）: {aid!r}"
    if not _ATT_ID_RE.match(aid):
        return f"attachment_ids 格式无效（期望 att_…）: {aid!r}"
    return None


def _sniff_ok(raw: bytes) -> bool:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if raw[:2] == b"\xff\xd8":
        return True
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return True
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return True
    return False


def _check_image_file(pp: Path) -> Optional[str]:
    try:
        if not pp.is_file():
            return f"文件不存在: {pp}"
        size = pp.stat().st_size
    except OSError as exc:
        return f"无法读取文件 {pp}: {exc}"
    if size <= 0:
        return f"空文件: {pp}"
    if size > _MAX_FILE_BYTES:
        return f"文件超过 {_MAX_FILE_BYTES} 字节上限: {pp} ({size} bytes)"
    suf = pp.suffix.lower()
    if suf not in _ALLOWED_EXT:
        return f"不支持的图片扩展名 {suf!r}（允许 {', '.join(_ALLOWED_EXT)}）: {pp}"
    try:
        raw = pp.read_bytes()[:32]
    except OSError as exc:
        return f"无法读取文件头 {pp}: {exc}"
    if not _sniff_ok(raw):
        return f"文件内容不是可识别图片: {pp}"
    return None


def _ensure_turn_attachments(conversation_id: str) -> None:
    from agent_v4.core.attachments import get_turn_attachments, set_turn_attachments

    if get_turn_attachments(conversation_id):
        return
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


def _resolve_one_path(cid: str, raw: str) -> Tuple[Optional[Path], Optional[str]]:
    from agent_v4.core.attachments import _path_under_attachments

    s = str(raw or "").strip().strip('"').strip("'")
    if not s:
        return None, "path 为空"
    pp = Path(s).expanduser()
    try:
        pp = pp.resolve()
    except OSError as exc:
        return None, f"路径无法解析 {s!r}: {exc}"
    if not _path_under_attachments(cid, pp):
        return None, f"路径不属于本会话 attachments 目录: {pp}"
    err = _check_image_file(pp)
    if err:
        return None, err
    return pp, None


def _resolve_one_id(cid: str, aid: str) -> Tuple[Optional[Path], Optional[str]]:
    from agent_v4.core.attachments import attachments_dir, get_turn_attachments

    bad = _validate_att_id(aid)
    if bad:
        return None, bad
    turn = get_turn_attachments(cid)
    by_id = {str(a.get("id") or ""): a for a in turn}
    a = by_id.get(aid)
    if a:
        pp = Path(str(a.get("path") or ""))
        try:
            pp = pp.expanduser().resolve()
        except OSError as exc:
            return None, f"id={aid} 的 path 无法解析: {exc}"
        err = _check_image_file(pp)
        if err:
            # 索引 path 失效时再按目录扫
            pass
        else:
            from agent_v4.core.attachments import _path_under_attachments

            if _path_under_attachments(cid, pp):
                return pp, None
    for ext in _ALLOWED_EXT:
        cand = (attachments_dir(cid) / f"{aid}{ext}").resolve()
        err = _check_image_file(cand)
        if err is None:
            return cand, None
    return None, f"未找到附件 id={aid}（本会话 attachments 下无对应文件）"


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
    if len(q) > _MAX_PROMPT_CHARS:
        return ac.err(
            ValueError(f"prompt 过长（{len(q)} 字符），上限 {_MAX_PROMPT_CHARS}；请缩短后重试")
        )

    cid = str(conversation_id or "").strip()
    if not cid:
        return ac.err(ValueError("缺少 conversation_id（由宿主注入）"))

    id_list, id_err = _as_str_list(attachment_ids, field="attachment_ids")
    if id_err:
        return ac.err(ValueError(id_err))
    path_only, p_err = _as_str_list(path if path not in (None, "") else None, field="path")
    if p_err:
        return ac.err(ValueError(p_err))
    if len(path_only or []) > 1:
        return ac.err(ValueError("path 只接受单张路径；多张请用 paths"))
    paths_list, ps_err = _as_str_list(paths, field="paths")
    if ps_err:
        return ac.err(ValueError(ps_err))

    id_list = _dedupe_keep_order(id_list or [])
    path_list = _dedupe_keep_order((path_only or []) + (paths_list or []))

    if not path_list and not id_list:
        return ac.err(
            ValueError(
                "path / paths / attachment_ids 必须至少传其一。"
                "请从系统提示【本轮用户附带截图】复制 path 或 id。"
            )
        )

    # 请求条目数（去重后）超限直接拦，避免解析后才发现
    req_n = len(path_list) + len(id_list)
    if req_n > _MAX_IMAGES:
        return ac.err(
            ValueError(
                f"一次最多查看 {_MAX_IMAGES} 张图，当前 path/paths/attachment_ids 去重后合计 {req_n} 个。"
                "请减少后重试。"
            )
        )

    for aid in id_list:
        bad = _validate_att_id(aid)
        if bad:
            return ac.err(ValueError(bad))

    try:
        _ensure_turn_attachments(cid)
    except Exception as exc:
        return ac.err(exc)

    files: List[Path] = []
    failures: List[str] = []
    seen = set()

    for raw in path_list:
        pp, err = _resolve_one_path(cid, raw)
        if err or pp is None:
            failures.append(f"path={raw!r}: {err or 'unknown'}")
            continue
        key = str(pp)
        if key in seen:
            continue
        seen.add(key)
        files.append(pp)

    for aid in id_list:
        pp, err = _resolve_one_id(cid, aid)
        if err or pp is None:
            failures.append(f"attachment_ids={aid!r}: {err or 'unknown'}")
            continue
        key = str(pp)
        if key in seen:
            continue
        seen.add(key)
        files.append(pp)

    if failures:
        return ac.err(
            FileNotFoundError(
                "部分或全部图片定位失败：\n- "
                + "\n- ".join(failures)
                + "\n请只使用本会话系统提示中的 path / id；不要编造路径。"
            )
        )

    if not files:
        return ac.err(
            FileNotFoundError(
                "未解析到任何可读截图。请核对 path / paths / attachment_ids。"
            )
        )

    if len(files) > _MAX_IMAGES:
        return ac.err(
            ValueError(
                f"一次最多查看 {_MAX_IMAGES} 张图，当前解析到 {len(files)} 张。"
                "请减少后重试。"
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
