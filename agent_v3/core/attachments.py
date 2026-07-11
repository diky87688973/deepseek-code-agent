# -*- coding: utf-8 -*-
"""会话截图附件：落盘、本 turn 索引、视觉 ephemeral 提示。"""
from __future__ import annotations

import base64
import io
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_v3.bootstrap import SESSION_DIR

_TURN_ATTACHMENTS: Dict[str, List[Dict[str, Any]]] = {}

_MAX_IMAGES = 4
_MAX_RAW_BYTES = 8 * 1024 * 1024
_JPEG_MAX_SIDE = 1560
_JPEG_QUALITY = 85

_LOOK_HINT_RE = re.compile(
    r"(图|截图|屏幕|界面|报错|看下|看看|这张|图片|screenshot|image|ui)",
    re.I,
)


def attachments_dir(cid: str) -> Path:
    d = SESSION_DIR / "attachments" / str(cid or "").strip()
    d.mkdir(parents=True, exist_ok=True)
    return d


def set_turn_attachments(cid: str, items: List[Dict[str, Any]]) -> None:
    cid = str(cid or "").strip()
    if not cid:
        return
    _TURN_ATTACHMENTS[cid] = list(items or [])


def get_turn_attachments(cid: str) -> List[Dict[str, Any]]:
    return list(_TURN_ATTACHMENTS.get(str(cid or "").strip()) or [])


def clear_turn_attachments(cid: str) -> None:
    _TURN_ATTACHMENTS.pop(str(cid or "").strip(), None)


def _sniff_image(raw: bytes) -> Tuple[bytes, str, str]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return raw, "image/png", ".png"
    if raw[:2] == b"\xff\xd8":
        return raw, "image/jpeg", ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return raw, "image/webp", ".webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return raw, "image/gif", ".gif"
    raise ValueError("无法识别的图片格式")


def _compress_to_jpeg(raw: bytes) -> Tuple[bytes, str, str]:
    """返回 (bytes, mime, ext)。优先压成 JPEG；失败则保留原格式。"""
    try:
        from PIL import Image
    except ImportError:
        return _sniff_image(raw)
    try:
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, float(_JPEG_MAX_SIDE) / float(max(w, h, 1)))
        if scale < 1.0:
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), resample)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return buf.getvalue(), "image/jpeg", ".jpg"
    except Exception:
        return _sniff_image(raw)


def save_chat_images(cid: str, images: List[Any]) -> List[Dict[str, Any]]:
    """将 ChatIn.images 落盘，返回 [{id, path, mime, name}]。"""
    cid = str(cid or "").strip()
    if not cid or not images:
        return []
    out: List[Dict[str, Any]] = []
    root = attachments_dir(cid)
    for i, item in enumerate(images[:_MAX_IMAGES]):
        if not isinstance(item, dict):
            continue
        b64 = str(item.get("data_base64") or item.get("data") or "").strip()
        if not b64:
            continue
        if "," in b64 and b64.lower().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception as exc:
            raise ValueError(f"images[{i}] base64 无效: {exc}") from exc
        if len(raw) > _MAX_RAW_BYTES:
            raise ValueError(f"images[{i}] 超过 {_MAX_RAW_BYTES} 字节上限")
        if not raw:
            continue
        try:
            blob, mime, ext = _compress_to_jpeg(raw)
        except ValueError as exc:
            raise ValueError(f"images[{i}] {exc}") from exc
        att_id = f"att_{uuid.uuid4().hex[:12]}"
        path = root / f"{att_id}{ext}"
        path.write_bytes(blob)
        out.append(
            {
                "id": att_id,
                "path": str(path.resolve()),
                "mime": mime,
                "name": f"{att_id}{ext}",
            }
        )
    return out


def format_user_attachment_footer(atts: List[Dict[str, Any]]) -> str:
    if not atts:
        return ""
    lines = ["", "[附件截图] 你看不到像素；须调用 look_screenshot 查看。"]
    for a in atts:
        lines.append(f"- id={a.get('id')} path={a.get('path')}")
    return "\n".join(lines)


_ATTACHMENT_FOOTER_MARK = "[附件截图]"


def strip_attachment_footer_for_ui(content: str) -> Tuple[str, bool]:
    """历史回显：去掉给模型看的附件 footer；返回 (正文, 是否曾带图)。"""
    s = str(content or "")
    idx = s.find(_ATTACHMENT_FOOTER_MARK)
    if idx < 0:
        return s, False
    return s[:idx].rstrip(), True


def ephemeral_attachment_tail(atts: List[Dict[str, Any]]) -> Dict[str, Any]:
    ids = ", ".join(str(a.get("id") or "") for a in atts)
    paths = "\n".join(f"- {a.get('id')}: {a.get('path')}" for a in atts)
    content = (
        "【本轮用户附带截图】\n"
        f"附件 id：{ids}\n"
        f"{paths}\n"
        "你无法直接看见图片。须调用 look_screenshot。\n"
        "prompt：表达用户本轮意图即可（可改写，不必逐字照抄用户原话），"
        "并让视觉模型直接给出用户需要的那种答案；"
        "不要改成与用户意图无关的通用看图套话（例如只顾抄布局/抄控件而丢掉「这是什么」这类问题）。\n"
        "attachment_ids 可省略（默认本轮全部）。细节不够可换一句短 prompt 再看。"
    )
    return {"role": "system", "content": content}


def should_force_look_screenshot(user_text: str, atts: List[Dict[str, Any]], looked: bool) -> bool:
    if looked or not atts:
        return False
    t = str(user_text or "").strip()
    if not t or t in ("请查看图片", "请查看截图"):
        return True
    return bool(_LOOK_HINT_RE.search(t))


def _path_under_attachments(cid: str, pp: Path) -> bool:
    try:
        root = attachments_dir(cid).resolve()
        pp.resolve().relative_to(root)
        return True
    except Exception:
        return False


def resolve_attachment_paths(
    cid: str,
    attachment_ids: Optional[List[str]] = None,
    paths: Optional[List[str]] = None,
) -> List[Path]:
    """解析 look_screenshot 要用的本地文件（仅本会话 attachments 目录）。"""
    cid = str(cid or "").strip()
    if not cid:
        return []
    found: List[Path] = []
    explicit = bool(paths) or bool(attachment_ids)

    if paths:
        for p in paths:
            pp = Path(str(p)).expanduser()
            try:
                pp = pp.resolve()
            except OSError:
                continue
            if not pp.is_file():
                continue
            if not _path_under_attachments(cid, pp):
                continue
            found.append(pp)

    turn = get_turn_attachments(cid)
    id_set = {str(x).strip() for x in (attachment_ids or []) if str(x).strip()}
    if id_set:
        by_id = {str(a.get("id") or ""): a for a in turn}
        for aid in id_set:
            a = by_id.get(aid)
            if a:
                pp = Path(str(a.get("path") or ""))
                if pp.is_file():
                    found.append(pp)
                    continue
            # 内存索引没有时，按 id 在本会话 attachments 目录试常见扩展名
            for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                cand = attachments_dir(cid) / f"{aid}{ext}"
                if cand.is_file():
                    found.append(cand.resolve())
                    break
    elif not explicit and turn:
        for a in turn:
            pp = Path(str(a.get("path") or ""))
            if pp.is_file():
                found.append(pp)

    # 显式传了 id/path 但一个都没解析到 → 不静默回退到「全部」
    if explicit and not found:
        return []

    uniq: List[Path] = []
    seen = set()
    for p in found:
        k = str(p)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq[:_MAX_IMAGES]


def read_attachment_file(cid: str, att_id: str) -> Tuple[Optional[Path], Optional[str]]:
    """供 HTTP 缩略图：返回 (path, error)。"""
    cid = str(cid or "").strip()
    att_id = str(att_id or "").strip()
    if not cid or not att_id or ".." in att_id or "/" in att_id or "\\" in att_id:
        return None, "invalid id"
    root = attachments_dir(cid).resolve()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        cand = (root / f"{att_id}{ext}").resolve()
        try:
            cand.relative_to(root)
        except ValueError:
            continue
        if cand.is_file():
            return cand, None
    return None, "not found"
