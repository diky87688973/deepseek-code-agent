# -*- coding: utf-8 -*-
"""
CLI 网页抓取工具
================

用途
----
抓取 http(s) URL 并提取纯文本内容，支持内容长度限制与关键词验证。
适合 agent 获取在线文档、API 响应等。
"""

from __future__ import annotations
from cli_help_share import _capture_help, _HelpFulParser

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BUILTIN_TIMEOUT_SEC = 20
BUILTIN_MAX_CHARS = 20000
BUILTIN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _emit_json(ok: bool, data=None, error=None) -> None:
    print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))


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


def _parse_keywords(raw: str | None) -> list[str]:
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
        "finalUrl": final_url,
        "status": int(status),
        "contentType": content_type,
        "title": title,
        "text": text,
        "textLen": len(text),
        "truncated": truncated,
    }


def _run_hard_checks(text: str, *, min_chars: int | None, keywords: list[str]) -> tuple[bool, dict]:
    checks: dict = {"minChars": min_chars, "requireKeywords": keywords, "hitKeywords": []}
    if min_chars is not None:
        if len(text) < min_chars:
            checks["failed"] = "minChars"
            checks["actualChars"] = len(text)
            return False, checks
    if keywords:
        lower = text.lower()
        hit = [k for k in keywords if k.lower() in lower]
        checks["hitKeywords"] = hit
        if not hit:
            checks["failed"] = "requireKeywords"
            return False, checks
    checks["failed"] = None
    checks["actualChars"] = len(text)
    return True, checks


def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="抓取 URL -> 提取纯文本内容")
    p.add_argument("--url", required=True, help="http(s) URL")
    p.add_argument("--timeoutSec", type=int, default=BUILTIN_TIMEOUT_SEC, help=f"超时秒数，默认 {BUILTIN_TIMEOUT_SEC}")
    p.add_argument("--maxChars", type=int, default=BUILTIN_MAX_CHARS, help=f"返回最大字符数，默认 {BUILTIN_MAX_CHARS}")
    p.add_argument("--userAgent", default=BUILTIN_USER_AGENT, help="自定义 User-Agent")
    p.add_argument("--outFile", help="将文本内容写出到文件")
    p.add_argument("--minChars", type=int, help="最小字符数要求，不满足则失败")
    p.add_argument("--requireKeywords", help="需包含的关键词，逗号分隔")
    p.add_argument("--jsonOut", action="store_true", help="输出 {ok,data,error}")
    return p


def _web_fetch_envelope(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict:
    u = str(args.url).strip()
    if not u:
        raise ValueError("url 不能为空")
    parsed_u = urllib.parse.urlparse(u)
    if parsed_u.scheme not in ("http", "https"):
        raise ValueError("只支持 http/https URL")
    if args.timeoutSec <= 0:
        raise ValueError("timeoutSec 必须 > 0")
    if args.maxChars <= 0:
        raise ValueError("maxChars 必须 > 0")
    if args.minChars is not None and args.minChars <= 0:
        raise ValueError("minChars 必须 > 0")

    data = _fetch_url(u, int(args.timeoutSec), int(args.maxChars), str(args.userAgent))

    keywords = _parse_keywords(args.requireKeywords)
    passed, checks = _run_hard_checks(str(data.get("text", "")), min_chars=args.minChars, keywords=keywords)
    data["checks"] = checks

    out_file = args.outFile
    if out_file:
        fp = Path(out_file)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(str(data.get("text", "")), encoding="utf-8")
        data["outFile"] = str(fp)
        data["written"] = True
    else:
        data["written"] = False

    if not passed:
        err = _err(
            code="E_ACCEPTANCE",
            message=f"内容验收失败: {checks.get('failed')}",
            hint="调整 URL / maxChars 或放宽 minChars/requireKeywords 限制",
            retryable=True,
        )
        return {"ok": False, "data": data, "error": err, "_plain": ""}

    return {"ok": True, "data": data, "error": None, "_plain": "" if out_file else str(data.get("text", ""))}


def agent_main(
    *,
    url: str,
    timeout_sec: int | None = None,
    max_chars: int | None = None,
    user_agent: str | None = None,
    out_file: str | None = None,
    min_chars: int | None = None,
    require_keywords: str | None = None,
) -> dict:
    parser = build_parser()
    args = argparse.Namespace(
        url=url,
        timeoutSec=timeout_sec if timeout_sec is not None else BUILTIN_TIMEOUT_SEC,
        maxChars=max_chars if max_chars is not None else BUILTIN_MAX_CHARS,
        userAgent=user_agent if user_agent is not None else BUILTIN_USER_AGENT,
        outFile=out_file,
        minChars=min_chars,
        requireKeywords=require_keywords,
        jsonOut=True,
    )
    try:
        out = _web_fetch_envelope(parser, args)
        out.pop("_plain", None)
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
        msg = str(e) + "\n\n--help:\n" + _capture_help(parser)
        err = _err(
            code="E_INVALID_INPUT",
            message=msg,
            hint="检查 URL/参数是否正确",
            retryable=False,
        )
        return {"ok": False, "data": None, "error": err}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        out = _web_fetch_envelope(parser, args)
        plain = out.pop("_plain", "") or ""
        if not out["ok"]:
            if args.jsonOut:
                _emit_json(False, data=out.get("data"), error=out["error"])
            else:
                raise ValueError((out["error"] or {}).get("message", "fetch failed"))
            return
        if args.jsonOut:
            _emit_json(True, data=out["data"], error=None)
        else:
            if args.outFile:
                print("ok")
            else:
                print(plain)
    except urllib.error.HTTPError as e:
        err = _err(
            code="E_HTTP_STATUS",
            message=f"HTTPError {e.code}: {e.reason}",
            hint="检查 URL 是否正确或目标服务可用",
            retryable=500 <= int(e.code) < 600,
        )
        if args.jsonOut:
            _emit_json(False, data=None, error=err)
        else:
            raise
    except urllib.error.URLError as e:
        err = _err(
            code="E_NETWORK",
            message=f"网络错误: {e.reason}",
            hint="检查网络连接或代理设置",
            retryable=True,
        )
        if args.jsonOut:
            _emit_json(False, data=None, error=err)
        else:
            raise
    except Exception as e:
        e.args = (str(e) + "\n\n--help:\n" + _capture_help(parser),)
        err = _err(
            code="E_INVALID_INPUT",
            message=str(e),
            hint="检查 URL/参数是否正确",
            retryable=False,
        )
        if args.jsonOut:
            _emit_json(False, data=None, error=err)
        else:
            raise


if __name__ == "__main__":
    main()