# -*- coding: utf-8 -*-
"""
统一预览工具：支持 file / text / textStdin / url 输入，输出标准 JSON。
用于将“读取/抓取/内存字符串”统一成可展示预览内容。
"""

from __future__ import annotations

from typing import Tuple, Dict, Optional
import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
from pathlib import Path

import urllib.request
import urllib.error
from cli_help_share import _capture_help, _HelpFulParser



def build_parser() -> argparse.ArgumentParser:
    p = _HelpFulParser(description="统一预览文本内容")
    p.add_argument("--file", help="本地文件路径")
    p.add_argument("--text", help="直接传入文本")
    p.add_argument("--textStdin", action="store_true", help="从 stdin 读取文本")
    p.add_argument("--url", help="从 URL 拉取文本")
    p.add_argument("--encoding", default="utf-8", help="文件读取编码，默认 utf-8，可选 auto")
    p.add_argument("--label", default="预览", help="预览标题")
    p.add_argument("--maxChars", type=int, default=0, help="最大返回字符数；<=0 表示全量预览（默认）")
    p.add_argument("--jsonOut", action="store_true", help="输出 JSON")
    return p


def _read_file(path: str, encoding: str) -> str:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"file 不存在: {path}")
    if encoding == "auto":
        for enc in ("utf-8", "gb18030", "gbk", "cp936"):
            try:
                return p.read_text(encoding=enc)
            except UnicodeDecodeError:
                continue
        return p.read_text(encoding="utf-8", errors="replace")
    return p.read_text(encoding=encoding)


def _read_url(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "cli-preview-render/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def _pick_source_parts(
    *,
    file: Optional[str],
    text: Optional[str],
    use_stdin: bool,
    stdin_body: Optional[str],
    url: Optional[str],
    encoding: str,
) -> Tuple[str, str]:
    got = sum(bool(x) for x in [file, text is not None, use_stdin, url])
    if got != 1:
        raise ValueError("file/text/textStdin/url 必须且只能指定一项")
    if file:
        return _read_file(file, encoding), f"file:{file}"
    if text is not None:
        return text, "text:inline"
    if use_stdin:
        body = stdin_body if stdin_body is not None else input()
        return body, "text:stdin"
    assert url
    return _read_url(url), f"url:{url}"


def _emit(ok: bool, data: dict, error: dict) -> None:
    print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))


def agent_main(
    *,
    file: Optional[str] = None,
    text: Optional[str] = None,
    use_stdin: bool = False,
    stdin_body: Optional[str] = None,
    url: Optional[str] = None,
    encoding: str = "utf-8",
    label: str = "预览",
    max_chars: int = 0,
) -> dict:
    """进程内入口；stdin_body 仅在 use_stdin 时生效（CLI 未传时由 main 用 input() 填充）。"""
    try:
        raw, src = _pick_source_parts(
            file=file,
            text=text,
            use_stdin=use_stdin,
            stdin_body=stdin_body,
            url=url,
            encoding=encoding,
        )
        raw = raw or ""
        limit = int(max_chars or 0)
        if limit <= 0:
            clipped = raw
        else:
            clipped = raw if len(raw) <= limit else (raw[:limit] + "\n…")
        data = {
            "label": label,
            "source": src,
            "fullLength": len(raw),
            "truncated": (limit > 0 and len(raw) > limit),
            "previewText": clipped,
        }
        return {"ok": True, "data": data, "error": None}
    except Exception as e:
        return {"ok": False, "data": {"label": label}, "error": {"type": "PreviewError", "message": str(e)}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    stdin_line = input() if args.textStdin else None
    res = agent_main(
        file=args.file,
        text=args.text,
        use_stdin=bool(args.textStdin),
        stdin_body=stdin_line,
        url=args.url,
        encoding=args.encoding,
        label=args.label,
        max_chars=int(args.maxChars or 0),
    )
    if res["ok"]:
        data = res["data"]
        assert data is not None
        _emit(True, data, None)
        return
    err = res.get("error") or {}
    msg = str(err.get("message", ""))
    full_msg = msg + "\n\n--help:\n" + _capture_help(parser)
    data_fail = res.get("data") or {"label": args.label}
    _emit(False, data_fail, {**err, "message": full_msg})


if __name__ == "__main__":
    main()
