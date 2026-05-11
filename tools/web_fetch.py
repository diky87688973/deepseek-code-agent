# -*- coding: utf-8 -*-
"""抓取网页并提取纯文本（urllib，无第三方依赖）。支持超时、长度限制、关键词与最小字数验收。"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

BUILTIN_TIMEOUT_SEC = 20
BUILTIN_MAX_CHARS = 20000
BUILTIN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _err(code: str, message: str, hint: str, retryable: bool) -> dict:
    return {
        "code": code,
        "type": "WebFetchError",
        "message": message,
        "hint": hint,
        "retryable": retryable,
    }


def _strip_html_to_text(raw_html: str) -> str:
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw_html)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _parse_keywords(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for x in str(raw).split(","):
        k = x.strip()
        if not k:
            continue
        if k not in out:
            out.append(k)
    return out


def _fetch_url(url: str, timeout_sec: int, max_chars: int, user_agent: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        final_url = resp.geturl()
        status = getattr(resp, "status", 200)
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read()

    encoding = "utf-8"
    m = re.search(r"charset=([A-Za-z0-9_\-]+)", content_type, flags=re.I)
    if m:
        encoding = m.group(1)
    try:
        html_text = raw.decode(encoding, errors="replace")
    except LookupError:
        html_text = raw.decode("utf-8", errors="replace")

    text = _strip_html_to_text(html_text)
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    title = ""
    mt = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text)
    if mt:
        title = html.unescape(mt.group(1)).strip()

    return {
        "url": url,
        "final_url": final_url,
        "status": int(status),
        "content_type": content_type,
        "title": title,
        "text": text,
        "text_len": len(text),
        "truncated": truncated,
    }


def _run_hard_checks(text: str, *, min_chars: Optional[int], keywords: list[str]) -> Tuple[bool, dict]:
    checks: dict = {"min_chars": min_chars, "require_keywords": keywords, "hit_keywords": []}
    if min_chars is not None:
        if len(text) < min_chars:
            checks["failed"] = "min_chars"
            checks["actual_chars"] = len(text)
            return False, checks
    if keywords:
        lower = text.lower()
        hit = [k for k in keywords if k.lower() in lower]
        checks["hit_keywords"] = hit
        if not hit:
            checks["failed"] = "require_keywords"
            return False, checks
    checks["failed"] = None
    checks["actual_chars"] = len(text)
    return True, checks


def _run_fetch(
    url: str,
    timeout_sec: int,
    max_chars: int,
    user_agent: str,
    out_file: Optional[str],
    min_chars: Optional[int],
    require_keywords: Optional[str],
) -> dict:
    u = str(url).strip()
    if not u:
        raise ValueError("url 不能为空")
    parsed_u = urllib.parse.urlparse(u)
    if parsed_u.scheme not in ("http", "https"):
        raise ValueError("只支持 http/https URL")
    if timeout_sec <= 0:
        raise ValueError("timeout_sec 必须 > 0")
    if max_chars <= 0:
        raise ValueError("max_chars 必须 > 0")
    if min_chars is not None and min_chars <= 0:
        raise ValueError("min_chars 必须 > 0")

    data = _fetch_url(u, int(timeout_sec), int(max_chars), str(user_agent))

    keywords = _parse_keywords(require_keywords)
    passed, checks = _run_hard_checks(str(data.get("text", "")), min_chars=min_chars, keywords=keywords)
    data["checks"] = checks

    out_f = (out_file or "").strip()
    if out_f:
        fp = Path(out_f)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(str(data.get("text", "")), encoding="utf-8")
        data["out_file"] = str(fp)
        data["written"] = True
    else:
        data["written"] = False

    if not passed:
        err = _err(
            code="E_ACCEPTANCE",
            message=f"内容验收失败: {checks.get('failed')}",
            hint="调整 URL / max_chars 或放宽 min_chars/require_keywords 限制",
            retryable=True,
        )
        return {"ok": False, "data": data, "error": err}

    return {"ok": True, "data": data, "error": None}


def agent_main(
    *,
    url: str,
    timeout_sec: int = BUILTIN_TIMEOUT_SEC,
    max_chars: int = BUILTIN_MAX_CHARS,
    user_agent: str = BUILTIN_USER_AGENT,
    out_file: Optional[str] = None,
    min_chars: Optional[int] = None,
    require_keywords: Optional[str] = None,
) -> dict:
    ua = (user_agent or "").strip() or BUILTIN_USER_AGENT
    try:
        out = _run_fetch(
            url=url,
            timeout_sec=int(timeout_sec),
            max_chars=int(max_chars),
            user_agent=ua,
            out_file=out_file,
            min_chars=min_chars,
            require_keywords=require_keywords,
        )
        return {"ok": out["ok"], "data": out["data"], "error": out["error"]}
    except urllib.error.HTTPError as e:
        err = _err(
            code="E_HTTP_STATUS",
            message=f"HTTPError {e.code}: {e.reason}",
            hint="检查 URL 是否正确或目标服务可用",
            retryable=500 <= int(e.code) < 600,
        )
        return {"ok": False, "data": None, "error": err}
    except urllib.error.URLError as e:
        err = _err(
            code="E_NETWORK",
            message=f"网络错误: {e.reason}",
            hint="检查网络连接或代理设置",
            retryable=True,
        )
        return {"ok": False, "data": None, "error": err}
    except Exception as e:
        err = _err(
            code="E_INVALID_INPUT",
            message=str(e),
            hint="检查 URL/参数是否正确",
            retryable=False,
        )
        return {"ok": False, "data": None, "error": err}


def main() -> None:
    p = argparse.ArgumentParser(description="抓取 URL → 提取纯文本")
    p.add_argument("--url", required=True)
    p.add_argument("--timeout_sec", type=int, default=BUILTIN_TIMEOUT_SEC)
    p.add_argument("--max_chars", type=int, default=BUILTIN_MAX_CHARS)
    p.add_argument("--user_agent", default=BUILTIN_USER_AGENT)
    p.add_argument("--out_file", default=None)
    p.add_argument("--min_chars", type=int, default=None)
    p.add_argument("--require_keywords", default=None)
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(
        url=args.url,
        timeout_sec=args.timeout_sec,
        max_chars=args.max_chars,
        user_agent=args.user_agent,
        out_file=args.out_file,
        min_chars=args.min_chars,
        require_keywords=args.require_keywords,
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    elif r.get("ok") and r.get("data"):
        d = r["data"]
        if args.out_file:
            print("ok")
        else:
            print(d.get("text", ""), end="")
    else:
        print(str((r.get("error") or {}).get("message", "")), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
