# -*- coding: utf-8 -*-
"""支持 JS 渲染的网页抓取工具 — 使用 Playwright 无头浏览器。

解决 SPA 动态页面无法抓取的问题（如 klingai 文档、轻雀文档等）。
自动等待 JS 渲染完成后提取全文。
"""

from __future__ import annotations

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
    try:
        ws = float(wait_sec)
        mc = int(max_chars)
    except (TypeError, ValueError) as e:
        return ac.err(ValueError(f"wait_sec / max_chars 必须为数字: {e}"))
    if mc <= 0:
        return ac.err(ValueError("max_chars 必须 > 0"))
    return _fetch_with_playwright(url, ws, mc)




