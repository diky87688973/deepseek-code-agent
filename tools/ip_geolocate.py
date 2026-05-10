# -*- coding: utf-8 -*-
"""IP 地理位置查询（ipwho.is 免费 API，无需 Key）。"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BUILTIN_TIMEOUT_SEC = 12
API_URL = "https://ipwho.is"


def _http_json(url: str, timeout_sec: int) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "ip-geolocate/1"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _normalize_ip_for_lookup(ip_raw: str) -> str:
    ip = str(ip_raw or "").strip()
    if not ip:
        return ""
    low = ip.lower()
    if low in {"localhost", "0.0.0.0", "127.0.0.1", "::1"}:
        return ""
    try:
        obj = ipaddress.ip_address(ip)
        if obj.is_loopback or obj.is_unspecified or obj.is_private:
            return ""
        return ip
    except ValueError:
        return ""


def agent_main(*, ip: str = "", timeout_sec: int = BUILTIN_TIMEOUT_SEC) -> dict:
    tsec = max(5, int(timeout_sec if timeout_sec is not None else BUILTIN_TIMEOUT_SEC))
    ip_raw = (ip or "").strip()
    ip_n = _normalize_ip_for_lookup(ip_raw)

    try:
        q = ""
        if ip_n:
            q = "/" + urllib.parse.quote(ip_n, safe="")
        js = _http_json(API_URL + q, tsec)

        if js.get("success") is False:
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "GeoLocateError",
                    "message": str(js.get("message") or "ipwho.is 查询失败"),
                    "hint": "检查 IP 是否合法，或稍后重试",
                    "retryable": True,
                },
            }

        city = str(js.get("city") or "")
        region = str(js.get("region") or "")
        country = str(js.get("country") or "")
        lat = js.get("latitude")
        lon = js.get("longitude")
        tz = js.get("timezone") or {}
        tz_id = str(tz.get("id") or "") if isinstance(tz, dict) else ""
        ip_used = str(js.get("ip") or ip_n)

        parts = [x for x in (city, region, country) if x]
        display_region = ",".join(parts) if parts else "未知地区"
        summary = f"【IP定位地区】{display_region}"
        if lat is not None and lon is not None:
            summary += f"（{lat},{lon}）"
        if tz_id:
            summary += f"，时区 {tz_id}"

        data = {
            "provider": "ipwho.is",
            "ip": ip_used,
            "city": city,
            "region": region,
            "country": country,
            "latitude": lat,
            "longitude": lon,
            "timezone": tz_id,
            "display_region_zh": "【IP定位地区】" + display_region,
            "summary_zh": summary,
            "raw": js,
        }
        return {"ok": True, "data": data, "error": None}
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "data": None,
            "error": {"type": "HTTPError", "message": str(e), "hint": "稍后重试", "retryable": True},
        }
    except urllib.error.URLError as e:
        return {
            "ok": False,
            "data": None,
            "error": {"type": "URLError", "message": str(e.reason), "hint": "检查网络或 DNS", "retryable": True},
        }
    except Exception as e:
        return {
            "ok": False,
            "data": None,
            "error": {"type": e.__class__.__name__, "message": str(e), "hint": "", "retryable": False},
        }


def main() -> None:
    p = argparse.ArgumentParser(description="IP 地理位置查询")
    p.add_argument("--ip", default="")
    p.add_argument("--timeout_sec", type=int, default=BUILTIN_TIMEOUT_SEC)
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(ip=str(args.ip or ""), timeout_sec=int(args.timeout_sec))
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    elif r.get("ok"):
        print(json.dumps(r.get("data"), ensure_ascii=False))
    else:
        print((r.get("error") or {}).get("message", ""), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
