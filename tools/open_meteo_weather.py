# -*- coding: utf-8 -*-
"""天气查询（Open-Meteo 免费 API，无需 Key）。支持地名、经纬度、或按出口/指定 IP 定位。"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

BUILTIN_FORECAST_DAYS = 2
BUILTIN_TIMEOUT_SEC = 15

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FC_URL = "https://api.open-meteo.com/v1/forecast"
IP_GEO_URL = "https://ipwho.is"

CN_CITY_ALIAS: Dict[str, Tuple[float, float, str, str, str]] = {
    "北京": (39.9042, 116.4074, "北京", "北京市", "中国"),
    "北京市": (39.9042, 116.4074, "北京", "北京市", "中国"),
    "上海": (31.2304, 121.4737, "上海", "上海市", "中国"),
    "上海市": (31.2304, 121.4737, "上海", "上海市", "中国"),
    "广州": (23.1291, 113.2644, "广州", "广东省", "中国"),
    "深圳": (22.5431, 114.0579, "深圳", "广东省", "中国"),
    "杭州": (30.2741, 120.1551, "杭州", "浙江省", "中国"),
    "南京": (32.0603, 118.7969, "南京", "江苏省", "中国"),
    "成都": (30.5728, 104.0668, "成都", "四川省", "中国"),
    "重庆": (29.5630, 106.5516, "重庆", "重庆市", "中国"),
    "天津": (39.3434, 117.3616, "天津", "天津市", "中国"),
    "武汉": (30.5928, 114.3055, "武汉", "湖北省", "中国"),
    "西安": (34.3416, 108.9398, "西安", "陕西省", "中国"),
    "苏州": (31.2989, 120.5853, "苏州", "江苏省", "中国"),
}


def _http_json(url: str, timeout_sec: int) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "open-meteo-weather/1"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _normalize_name(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).casefold()


def _has_cjk(s: str) -> bool:
    return re.search(r"[\u3400-\u9fff]", str(s or "")) is not None


def _score_geo_result(query: str, hit: Dict[str, Any]) -> float:
    qn = _normalize_name(query)
    nm = str(hit.get("name") or "")
    nn = _normalize_name(nm)
    admin1 = str(hit.get("admin1") or "")
    admin2 = str(hit.get("admin2") or "")
    country = str(hit.get("country") or "")
    cc = str(hit.get("country_code") or "").upper()
    feat = str(hit.get("feature_code") or "").upper()
    pop = float(hit.get("population") or 0)

    score = 0.0
    score += min(pop / 100000.0, 50.0)

    if nn == qn:
        score += 120
    elif qn and qn in nn:
        score += 60
    elif nn and nn in qn:
        score += 35

    joined = _normalize_name(admin1 + admin2 + country)
    if qn and qn in joined:
        score += 25

    if _has_cjk(query):
        if cc == "CN":
            score += 60
        if feat in {"PPLC", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPL"}:
            score += 20

    if qn == "北京" and _normalize_name(admin1) == "北京市":
        score += 180

    return score


def _resolve_cn_alias(query: str) -> Optional[Tuple[float, float, str, str, str]]:
    q = str(query or "").strip()
    if not q:
        return None
    return CN_CITY_ALIAS.get(q)


def _pick_geo_result(query: str, js: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    results = js.get("results")
    if not isinstance(results, list) or not results:
        return None
    candidates = [x for x in results if isinstance(x, dict)]
    if not candidates:
        return None
    candidates.sort(key=lambda h: _score_geo_result(query, h), reverse=True)
    return candidates[0]


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


def _lookup_ip_region(ip_for_lookup: str, timeout_sec: int) -> Optional[Dict[str, Any]]:
    tail = ("/" + urllib.parse.quote(ip_for_lookup, safe="")) if ip_for_lookup else ""
    js = _http_json(IP_GEO_URL + tail, timeout_sec)
    if js.get("success") is False:
        return None
    if js.get("latitude") is None or js.get("longitude") is None:
        return None
    return js


def _wmo_zh(code: Optional[int]) -> str:
    if code is None:
        return "未知"
    m = {
        0: "晴",
        1: "大部晴",
        2: "少云",
        3: "阴天",
        45: "雾",
        48: "雾凇",
        51: "小毛毛雨",
        53: "毛毛雨",
        55: "大毛毛雨",
        61: "小阵雨",
        63: "阵雨",
        65: "大阵雨",
        71: "小雪",
        73: "雪",
        75: "大雪",
        80: "小阵雨",
        81: "阵雨",
        82: "强阵雨",
        95: "雷暴",
        96: "雷暴伴冰雹",
        99: "强雷暴伴冰雹",
    }
    return m.get(int(code), f"天气码{code}")


def _run_core(
    *,
    location: str = "",
    latitude: str = "",
    longitude: str = "",
    ip: str = "",
    forecast_days: int = BUILTIN_FORECAST_DAYS,
    timeout_sec: int = BUILTIN_TIMEOUT_SEC,
) -> dict:
    tsec = max(5, int(timeout_sec or BUILTIN_TIMEOUT_SEC))
    loc_input = (location or "").strip()
    days = int(forecast_days) if forecast_days else BUILTIN_FORECAST_DAYS
    days = max(1, min(7, days))

    lat_s = (latitude or "").strip()
    lon_s = (longitude or "").strip()
    ip_for_lookup = _normalize_ip_for_lookup(ip or "")

    requested_loc = loc_input
    name_used = loc_input or ""
    admin1 = ""
    country = ""
    source = "location"
    note = ""

    if lat_s and lon_s:
        lat = float(lat_s)
        lon = float(lon_s)
        name_used = f"经纬度 {lat:.4f}°,{lon:.4f}°"
        source = "coordinates"
    elif loc_input:
        alias = _resolve_cn_alias(loc_input)
        if alias is not None:
            lat, lon, name_used, admin1, country = alias
        else:
            q = urllib.parse.urlencode({"name": loc_input, "count": "10", "language": "zh", "format": "json"})
            geo = _http_json(f"{GEO_URL}?{q}", tsec)
            hit = _pick_geo_result(loc_input, geo)
            if hit is None:
                return {
                    "ok": False,
                    "data": None,
                    "error": {
                        "type": "NotFound",
                        "message": f"未找到地名：{loc_input}",
                        "hint": "可换 location 或改用 latitude/longitude",
                        "retryable": False,
                    },
                }
            lat = float(hit.get("latitude"))
            lon = float(hit.get("longitude"))
            name_used = str(hit.get("name") or loc_input)
            admin1 = str(hit.get("admin1") or "")
            country = str(hit.get("country") or "")
    else:
        ip_geo = _lookup_ip_region(ip_for_lookup, tsec)
        if ip_geo is None:
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "RegionUnavailable",
                    "message": "无法获取地区：未提供 location/经纬度，且 IP 定位失败",
                    "hint": "请传 location 或 latitude/longitude；也可传 ip（客户端公网 IP）",
                    "retryable": True,
                },
            }
        lat = float(ip_geo.get("latitude"))
        lon = float(ip_geo.get("longitude"))
        city = str(ip_geo.get("city") or "")
        region = str(ip_geo.get("region") or "")
        ctry = str(ip_geo.get("country") or "")
        name_used = city or region or ctry or "IP定位结果"
        admin1 = region
        country = ctry
        source = "ip_geolocate"
        if ip_for_lookup:
            note = f"说明：按指定 IP（{ip_for_lookup}）定位地区后查询天气。"
        else:
            note = "说明：未传 location，已按公网出口 IP 自动定位地区后查询天气。"

    q2 = urllib.parse.urlencode(
        {
            "latitude": str(lat),
            "longitude": str(lon),
            "timezone": "Asia/Shanghai",
            "forecast_days": str(days),
            "current": "temperature_2m,relative_humidity_2m,precipitation,rain,showers,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        }
    )
    fc = _http_json(f"{FC_URL}?{q2}", tsec)

    cur = fc.get("current") or {}
    daily = fc.get("daily") or {}
    d_time = daily.get("time") or []
    d_rain_p = daily.get("precipitation_probability_max") or []
    d_prec = daily.get("precipitation_sum") or []
    d_wx = daily.get("weather_code") or []

    tail = ""
    if admin1 or country:
        tail = "（" + "，".join([x for x in (admin1, country) if x]) + "）"
    display_plain = f"{name_used}{tail}"
    display_region_zh = "【解析地区】" + display_plain

    lines: List[str] = [display_region_zh]
    if note:
        lines.append(note)
    lines.append(f"坐标：{lat:.4f}, {lon:.4f}")
    cur_code = cur.get("weather_code")
    lines.append(
        f"当前：{_wmo_zh(int(cur_code)) if cur_code is not None else '未知'}，"
        f"气温 {cur.get('temperature_2m')}℃，降水 {cur.get('precipitation')}mm，湿度 {cur.get('relative_humidity_2m')}%"
    )
    if d_time and (d_rain_p or d_prec):
        lines.append("未来数日降水概况：")
        for i, t in enumerate(d_time[:days]):
            prob = d_rain_p[i] if i < len(d_rain_p) else None
            prec = d_prec[i] if i < len(d_prec) else None
            wxc = int(d_wx[i]) if i < len(d_wx) and d_wx[i] is not None else None
            prob_s = f"{prob}%" if prob is not None else "-"
            prec_s = f"{prec}mm" if prec is not None else "-"
            lines.append(f"  {t}：降水概率 {prob_s}，降水量 {prec_s}，{_wmo_zh(wxc)}")

    summary = "\n".join(lines)
    data = {
        "provider": "Open-Meteo",
        "source": source,
        "requested_location": requested_loc,
        "resolved_name": name_used,
        "display_plain": display_plain,
        "display_region_zh": display_region_zh,
        "latitude": lat,
        "longitude": lon,
        "timezone": fc.get("timezone"),
        "current": cur,
        "daily": daily,
        "summary_zh": summary,
    }
    return {"ok": True, "data": data, "error": None}


def agent_main(
    *,
    location: str = "",
    latitude: str = "",
    longitude: str = "",
    ip: str = "",
    forecast_days: int = BUILTIN_FORECAST_DAYS,
    timeout_sec: int = BUILTIN_TIMEOUT_SEC,
) -> dict:
    try:
        return _run_core(
            location=location,
            latitude=latitude,
            longitude=longitude,
            ip=ip,
            forecast_days=forecast_days,
            timeout_sec=timeout_sec,
        )
    except urllib.error.HTTPError as e:
        return {"ok": False, "data": None, "error": {"type": "HTTPError", "message": str(e), "hint": "稍后重试", "retryable": True}}
    except urllib.error.URLError as e:
        return {"ok": False, "data": None, "error": {"type": "URLError", "message": str(e.reason), "hint": "检查网络或 DNS", "retryable": True}}
    except Exception as e:
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": str(e), "hint": "", "retryable": False}}


def main() -> None:
    p = argparse.ArgumentParser(description="Open-Meteo 天气查询")
    p.add_argument("--location", default="")
    p.add_argument("--latitude", default="")
    p.add_argument("--longitude", default="")
    p.add_argument("--ip", default="")
    p.add_argument("--forecast_days", type=int, default=BUILTIN_FORECAST_DAYS)
    p.add_argument("--timeout_sec", type=int, default=BUILTIN_TIMEOUT_SEC)
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(
        location=str(args.location or ""),
        latitude=str(args.latitude or ""),
        longitude=str(args.longitude or ""),
        ip=str(args.ip or ""),
        forecast_days=int(args.forecast_days),
        timeout_sec=int(args.timeout_sec),
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    elif r.get("ok"):
        print(json.dumps(r.get("data"), ensure_ascii=False))
    else:
        print((r.get("error") or {}).get("message", ""), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
