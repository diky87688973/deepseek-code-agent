# -*- coding: utf-8 -*-
"""会话落盘加密：与 {session}.json 整包加密同算法；{session}.raw 按行独立密文 envelope。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SESSION_ENCRYPTION_MAGIC = "__code_web_agent_session_encrypted__"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ctx: Optional["_CryptoCtx"] = None


class _CryptoCtx:
    def __init__(self) -> None:
        from util.config_loader import load_config

        self.cfg = load_config(verbose=False)
        dr = str(self.cfg.get("AGENT_DATA_ROOT_DIR") or "").strip().strip("'\"")
        if not dr:
            raise RuntimeError("AGENT_DATA_ROOT_DIR 未配置")
        self.data_root = Path(dr).expanduser().resolve()
        skf = str(self.cfg.get("AGENT_SESSION_KEY_FILE") or "").strip().strip("'\"")
        self.session_key_file = (
            Path(skf).expanduser().resolve()
            if skf
            else (self.data_root / "cache" / "session_encryption.key")
        )
        if getattr(sys, "frozen", False):
            agent_root = Path(sys.executable).resolve().parent
        else:
            agent_root = _REPO_ROOT
        self.app_entropy = hashlib.sha256(
            (str(agent_root) + "|code-web-agent-session-v1").encode("utf-8")
        ).digest()

    @property
    def mode(self) -> str:
        m = str(self.cfg.get("AGENT_SESSION_ENCRYPTION") or "").strip().lower()
        if m not in {"auto", "dpapi", "local", "none"}:
            return "none"
        return m


def _get_ctx() -> _CryptoCtx:
    global _ctx
    if _ctx is None:
        _ctx = _CryptoCtx()
    return _ctx


def session_encryption_enabled() -> bool:
    return _get_ctx().mode != "none"


def _dpapi_crypt(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    ctx = _get_ctx()
    in_buf = ctypes.create_string_buffer(data)
    in_blob = DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    entropy_buf = ctypes.create_string_buffer(ctx.app_entropy)
    entropy_blob = DATA_BLOB(
        len(ctx.app_entropy), ctypes.cast(entropy_buf, ctypes.POINTER(ctypes.c_char))
    )
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(in_blob), None, ctypes.byref(entropy_blob), None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _session_key_material() -> bytes:
    ctx = _get_ctx()
    kf = ctx.session_key_file
    kf.parent.mkdir(parents=True, exist_ok=True)
    if kf.is_file():
        val = kf.read_text(encoding="ascii").strip()
        return base64.urlsafe_b64decode(val.encode("ascii"))
    key = os.urandom(32)
    kf.write_text(base64.urlsafe_b64encode(key).decode("ascii"), encoding="ascii")
    try:
        os.chmod(str(kf), 0o600)
    except Exception:
        pass
    return key


def _session_fallback_key() -> bytes:
    ctx = _get_ctx()
    return hmac.new(ctx.app_entropy, _session_key_material(), hashlib.sha256).digest()


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(data, out[: len(data)]))


def encrypt_session_payload(plain: bytes) -> Optional[Dict[str, Any]]:
    """加密字节载荷；未启用加密时返回 None（调用方写明文）。"""
    mode = _get_ctx().mode
    if mode == "none":
        return None
    if os.name == "nt" and mode in {"auto", "dpapi"}:
        try:
            data = _dpapi_crypt(plain, True)
            return {
                SESSION_ENCRYPTION_MAGIC: 1,
                "alg": "dpapi-user-v1",
                "data": base64.b64encode(data).decode("ascii"),
            }
        except Exception as exc:
            if mode == "dpapi":
                raise
            print(
                f"WARN: DPAPI session encryption failed, using local key fallback: {exc}",
                file=sys.stderr,
                flush=True,
            )
    key = _session_fallback_key()
    nonce = os.urandom(16)
    cipher = _xor_stream(plain, key, nonce)
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return {
        SESSION_ENCRYPTION_MAGIC: 1,
        "alg": "local-hmac-sha256-stream-v1",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "data": base64.b64encode(cipher).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
    }


def decrypt_session_payload(raw: Any) -> Optional[bytes]:
    if not isinstance(raw, dict) or raw.get(SESSION_ENCRYPTION_MAGIC) != 1:
        return None
    alg = str(raw.get("alg") or "")
    if alg == "dpapi-user-v1":
        data = base64.b64decode(str(raw.get("data") or "").encode("ascii"))
        return _dpapi_crypt(data, False)
    if alg == "local-hmac-sha256-stream-v1":
        key = _session_fallback_key()
        nonce = base64.b64decode(str(raw.get("nonce") or "").encode("ascii"))
        cipher = base64.b64decode(str(raw.get("data") or "").encode("ascii"))
        tag = base64.b64decode(str(raw.get("tag") or "").encode("ascii"))
        expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("session encryption tag mismatch")
        return _xor_stream(cipher, key, nonce)
    raise ValueError(f"unsupported session encryption alg: {alg}")


def encrypt_raw_line(plain_message_json: str) -> str:
    """将一条消息 JSON 文本加密为可单独 append 的一行（仍为 JSON envelope 文本）。"""
    envelope = encrypt_session_payload(plain_message_json.encode("utf-8"))
    if envelope is None:
        return plain_message_json
    return json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))


def decrypt_raw_line(line: str) -> Optional[bytes]:
    """解密 .raw 一行，得到消息 JSON 字节；明文行则原样返回 UTF-8 bytes。"""
    text = line.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text.encode("utf-8")
    if not isinstance(parsed, dict):
        return text.encode("utf-8")
    if parsed.get(SESSION_ENCRYPTION_MAGIC) == 1:
        plain = decrypt_session_payload(parsed)
        if plain is None:
            return None
        return plain
    return text.encode("utf-8")
