# -*- coding: utf-8 -*-
"""Model pricing: static JSON (CHAT_PRICING_SOURCE / config.ini misc.pricing_source)."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import urllib.error
import urllib.request

from .agent_model_dispatch import ALLOWED_MODELS
from util.config_loader import load_config

_AGENT_CONFIG = load_config(verbose=False)

_PAIR_RE = re.compile(r"<td>([0-9.]+)元</td><td>([0-9.]+)元")

_NUM_KEYS = frozenset({"cache_hit_cny_per_m", "cache_miss_cny_per_m", "output_cny_per_m"})


def _pricing_source_mode() -> str:
    v = str(_AGENT_CONFIG.get("AGENT_PRICING_SOURCE") or "").strip().lower()
    if v in ("deepseek", "fetch", "html"):
        return "deepseek"
    if v in ("0", "false", "no", "off", "none", "static_only"):
        return "off"
    return "auto"


def _row_from_static(obj: Any) -> Optional[Dict[str, float]]:
    if not isinstance(obj, dict):
        return None
    out: Dict[str, float] = {}
    for k in _NUM_KEYS:
        if k not in obj:
            return None
        out[k] = float(obj[k])
    return out


def _static_table() -> Optional[Dict[str, Any]]:
    raw = str(_AGENT_CONFIG.get("AGENT_PRICING_JSON") or "").strip()
    if not raw:
        return None
    try:
        t = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return t if isinstance(t, dict) else None


def _lookup_static(model: str, table: Dict[str, Any]) -> Optional[Tuple[Dict[str, float], str]]:
    if model in table and isinstance(table.get(model), dict):
        row = _row_from_static(table[model])
        if row:
            return row, "CHAT_PRICING_JSON"
    d = table.get("__default__")
    if isinstance(d, dict):
        row = _row_from_static(d)
        if row:
            return row, "CHAT_PRICING_JSON"
    return None


def _fetch_rows() -> Optional[List[Tuple[float, float]]]:
    url = str(_AGENT_CONFIG["AGENT_PRICING_PAGE_URL"]).strip()
    if not url:
        raise RuntimeError("AGENT_PRICING_PAGE_URL 未设置！请在 config.ini 的 [misc] 节配置 pricing_page_url")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "deepseek-code-agent-pricing/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            t = resp.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        return None

    pairs = _PAIR_RE.findall(t)
    if len(pairs) < 3:
        return None
    try:
        return [(float(a), float(b)) for a, b in pairs[:3]]
    except ValueError:
        return None


_PRICING_CACHE: Dict[str, Dict[str, Any]] = {}


def get_model_pricing_snapshot(conversation_id: str, model: str) -> Dict[str, Any]:
    cid = str(conversation_id or "").strip()
    m = str(model or "").strip()
    if not cid:
        return {"ok": False, "error": "missing_conversation_id"}
    if m not in ALLOWED_MODELS:
        return {"ok": False, "error": "unknown_model"}
    key = f"{cid}\x00{m}"
    if key in _PRICING_CACHE:
        return _PRICING_CACHE[key]

    tbl = _static_table()
    if tbl:
        hit = _lookup_static(m, tbl)
        if hit:
            row, src = hit
            ok_row: Dict[str, Any] = {"ok": True, "model": m, **row, "source": src}
            _PRICING_CACHE[key] = ok_row
            return ok_row

    mode = _pricing_source_mode()
    if mode == "off":
        err = {
            "ok": False,
            "error": "pricing_disabled",
            "hint": "Set CHAT_PRICING_JSON or CHAT_PRICING_SOURCE=deepseek",
        }
        _PRICING_CACHE[key] = err
        return err
    if mode == "auto" and not m.startswith("deepseek-"):
        err = {
            "ok": False,
            "error": "no_static_pricing",
            "hint": "Set CHAT_PRICING_JSON or CHAT_PRICING_SOURCE=deepseek for HTML pricing",
        }
        _PRICING_CACHE[key] = err
        return err

    page_url = str(_AGENT_CONFIG.get("AGENT_PRICING_PAGE_URL") or "").strip()
    rows = _fetch_rows()
    if not rows or len(rows) < 3:
        err = {"ok": False, "error": "fetch_or_parse", "source": page_url or "AGENT_PRICING_PAGE_URL"}
        _PRICING_CACHE[key] = err
        return err
    col = 0 if m == "deepseek-v4-flash" else 1
    hit_flash, hit_pro = rows[0]
    miss_flash, miss_pro = rows[1]
    out_flash, out_pro = rows[2]
    hit_w, miss_w, out_w = (
        (hit_flash, miss_flash, out_flash) if col == 0 else (hit_pro, miss_pro, out_pro)
    )
    ok_row = {
        "ok": True,
        "model": m,
        "cache_hit_cny_per_m": hit_w,
        "cache_miss_cny_per_m": miss_w,
        "output_cny_per_m": out_w,
        "source": page_url,
    }
    _PRICING_CACHE[key] = ok_row
    return ok_row
