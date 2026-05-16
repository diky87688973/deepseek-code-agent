# -*- coding: utf-8 -*-
"""支持 JS 渲染的网页抓取工具 — 使用 Playwright 无头浏览器。

解决 SPA 动态页面无法抓取的问题（如 klingai 文档、轻雀文档等）。
自动等待 JS 渲染完成后提取全文。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Optional

import agent_common as ac


def _fetch_with_playwright(url: str, wait_sec: float = 3.0, max_chars: int = 100000) -> dict:
    """用 Playwright 无头浏览器抓取 JS 渲染后的页面内容。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "data": None, "error": {"type": "ImportError", "message": "缺少 Playwright 依赖。请执行以下命令安装：\n  pip install playwright\n  python -m playwright install chromium\n（已添加到 requirements.txt，首次部署时 pip install -r requirements.txt 即可）"}}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            if wait_sec > 0:
                time.sleep(wait_sec)

            title = page.title()
            body_text = page.inner_text("body") or ""
            html = page.content()

            meta_desc = ""
            try:
                meta_desc = page.get_attribute("meta[name=description]", "content") or ""
            except Exception:
                pass

            browser.close()

            if len(body_text) > max_chars:
                body_text = body_text[:max_chars] + "\n...(截断)"

            return ac.ok({
                "url": url,
                "title": title,
                "text": body_text,
                "html_len": len(html),
                "meta_description": meta_desc,
            })

    except Exception as e:
        return ac.err(e)


def agent_main(*, url: str = "", wait_sec: float = 3.0, max_chars: int = 100000) -> dict:
    if not url:
        return ac.err(ValueError("缺少 url 参数"))
    return _fetch_with_playwright(url, wait_sec, max_chars)


def main() -> None:
    p = argparse.ArgumentParser(description="JS 渲染网页抓取工具（Playwright 无头浏览器）")
    p.add_argument("--url", required=True, help="目标 URL")
    p.add_argument("--wait_sec", type=float, default=3.0, help="JS 渲染等待秒数")
    p.add_argument("--max_chars", type=int, default=100000, help="最大字符数")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(url=args.url, wait_sec=args.wait_sec, max_chars=args.max_chars)
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok") and isinstance(r.get("data"), dict):
            d = r["data"]
            print(f"标题: {d.get('title', '')}")
            print(f"URL: {d.get('url', '')}")
            print(f"HTML 大小: {d.get('html_len', 0)} 字符")
            print("---正文---")
            print(d.get("text", "")[:args.max_chars])
        err = r.get("error") or {}
        if err:
            print(f"错误: {err.get('message', '')}", file=sys.stderr)
            import sys as _sys
            _sys.exit(1)


if __name__ == "__main__":
    main()
