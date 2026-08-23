# -*- coding: utf-8 -*-
"""会话截图附件：落盘、本 turn 索引、视觉 ephemeral 提示。"""
from __future__ import annotations

import base64
import io
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_v4.bootstrap import SESSION_DIR, VISION_EMBED_MAX_BYTES, VISION_EMBED_MAX_SIDE

_TURN_ATTACHMENTS: Dict[str, List[Dict[str, Any]]] = {}

_MAX_IMAGES = 4
_MAX_RAW_BYTES = 8 * 1024 * 1024


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


def save_chat_images(cid: str, images: List[Any]) -> List[Dict[str, Any]]:
    """将 ChatIn.images 按原图像素落盘（UI 预览与 look_screenshot 共用）。返回 [{id, path, mime, name}]。"""
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
            blob, mime, ext = _sniff_image(raw)
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


def format_user_attachment_footer(atts: List[Dict[str, Any]], vision: bool = False) -> str:
    if not atts:
        return ""
    if vision:
        # 视觉模型：图片已嵌入消息，无需 footer 提示与 id/path 文本（省 token）
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
        "必填：prompt；以及 path / paths / attachment_ids 三者至少其一（从上列复制）。\n"
        "prompt：表达用户本轮意图即可（可改写，不必逐字照抄用户原话），"
        "并让视觉模型直接给出用户需要的那种答案；"
        "不要改成与用户意图无关的通用看图套话（例如只顾抄布局/抄控件而丢掉「这是什么」这类问题）。\n"
        "细节不够可换一句短 prompt 再看。"
    )
    return {"role": "system", "content": content}


def should_force_look_screenshot(
    user_text: str, atts: List[Dict[str, Any]], looked: bool, vision: bool = False
) -> bool:
    if vision or looked or not atts:
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
    """解析附件路径（仅本会话 attachments 目录）。显式参数未命中时不静默回退。"""
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


# ── 视觉嵌图：附件 → OpenAI image_url 块（本地缩放 + base64）──
try:
    from PIL import Image as _PILImage

    _HAS_PIL = True
except ImportError:
    _PILImage = None
    _HAS_PIL = False


def _image_mime_for_path(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".png":
        return "image/png"
    if suf == ".webp":
        return "image/webp"
    if suf == ".gif":
        return "image/gif"
    return "image/jpeg"


def _resize_image_bytes(path: Path, max_side: int) -> Tuple[bytes, str]:
    """本地缩放长边至 max_side（官方推理前自动缩放约 800×800，先缩省请求体且不损失效果）。
    仅对 PIL 可解码的 PNG/JPEG/WebP 缩放；失败或动图原样返回。返回 (bytes, mime)。"""
    if not _HAS_PIL or max_side <= 0:
        return path.read_bytes(), _image_mime_for_path(path)
    suf = path.suffix.lower()
    if suf not in (".png", ".jpg", ".jpeg", ".webp"):
        return path.read_bytes(), _image_mime_for_path(path)
    img = None
    try:
        img = _PILImage.open(path)
        img.load()
        if max(img.size) <= max_side:
            return path.read_bytes(), _image_mime_for_path(path)
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        fmt = "PNG" if suf == ".png" else ("WEBP" if suf == ".webp" else "JPEG")
        img.save(buf, format=fmt)
        return buf.getvalue(), _image_mime_for_path(path)
    except Exception:
        return path.read_bytes(), _image_mime_for_path(path)
    finally:
        if img is not None:
            try:
                img.close()
            except Exception:
                pass


def build_embedded_image_blocks(
    atts: List[Dict[str, Any]],
    *,
    max_bytes: int = 0,
    max_side: int = 0,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """把附件编码为 OpenAI image_url 块（data URL），供视觉模型直接嵌入 user 消息。

    返回 (blocks, skipped_ids)：blocks 元素为 {att_id, mime, url}；
    累计 base64 超过 max_bytes（0 表示不限制）后，剩余附件转 skipped（调用方应转文本占位）。
    """
    budget = int(max_bytes or VISION_EMBED_MAX_BYTES)
    side = int(max_side or VISION_EMBED_MAX_SIDE)
    blocks: List[Dict[str, Any]] = []
    skipped: List[str] = []
    used = 0
    for a in atts or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or "").strip()
        pp = Path(str(a.get("path") or ""))
        try:
            raw, mime = _resize_image_bytes(pp, side)
        except OSError:
            skipped.append(aid or "?")
            continue
        if not raw:
            skipped.append(aid or "?")
            continue
        b64 = base64.b64encode(raw).decode("ascii")
        if budget > 0 and used + len(b64) > budget:
            skipped.append(aid or "?")
            continue
        used += len(b64)
        blocks.append({"att_id": aid, "mime": mime, "url": f"data:{mime};base64,{b64}"})
    return blocks, skipped


def image_placeholder_text(ids: List[str]) -> str:
    """附件转文本占位（非视觉模型 / 预算不足 / 远记忆折叠 / 摘要剥离时使用）。"""
    if not ids:
        return ""
    return "[图片: " + ", ".join(str(x) for x in ids) + "]"
