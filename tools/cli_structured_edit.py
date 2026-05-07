# -*- coding: utf-8 -*-
"""
CLI 结构化编辑工具
==================

用途
----
对文件做追加/插入/区间替换/字面替换；从文件或内存文本做片段提取；按区间或短语删除片段。
输入统一为 payload（或 request 包裹 file/encoding/payload）。

payload.type
------------
- append / append_line / insert / replace_range / replace_literal / replace_markers：目标为 --file 或 request.file（写盘）
- replace_markers：提供 startMarker、endMarker、text（可选 searchFrom 整数）；半开区间 [index(startMarker), index(endMarker))，等价先算 offset 再 replace_range。
- extract：从 file / text / stdin / url 四选一读取内容后切片（不写源文件，除非指定 outFile）
- delete_segments：同上读入内容后删除 masks 区间与/或 dropPhrases 字面命中（不写源，除非指定 outFile）

提取模式 extract.mode
---------------------
- lines：按行闭区间 [startLine, endLine]，1-based
- lines_columns：行列矩形区间，1-based；结束列开区间；endColumn 支持 -1/-2 倒推
- offsets：全串字符索引 [start, end)，0-based；end 支持 -1/-2 倒推

delete_segments
---------------
- masks: [{start, end}] 半开 0-based，无负索引
- dropPhrases: 字符串数组，字面全量删除
- masks 与 dropPhrases 至少提供一个非空
"""

from __future__ import annotations

import cli_stdio_utf8 as _stdio_utf8

_stdio_utf8.install_stdio_utf8()

import argparse
import json
import subprocess
import sys
import threading

from urllib.request import Request, urlopen
from pathlib import Path
from cli_help_share import _capture_help, _HelpFulParser

_INLINE_TOOL_LOCK = threading.RLock()


TYPE_APPEND = "append"
TYPE_APPEND_LINE = "append_line"
TYPE_INSERT = "insert"
TYPE_REPLACE_RANGE = "replace_range"
TYPE_REPLACE_LITERAL = "replace_literal"
TYPE_REPLACE_MARKERS = "replace_markers"
TYPE_EXTRACT = "extract"
TYPE_DELETE_SEGMENTS = "delete_segments"
TYPE_BATCH = "batch"

TYPE_MUTATE = {
    TYPE_APPEND,
    TYPE_APPEND_LINE,
    TYPE_INSERT,
    TYPE_REPLACE_RANGE,
    TYPE_REPLACE_LITERAL,
    TYPE_REPLACE_MARKERS,
}
TYPE_ALL = TYPE_MUTATE | {TYPE_EXTRACT, TYPE_DELETE_SEGMENTS, TYPE_BATCH}

EXTRACT_MODE_LINES = "lines"
EXTRACT_MODE_LINES_COLUMNS = "lines_columns"
EXTRACT_MODE_OFFSETS = "offsets"
EXTRACT_MODES = frozenset({EXTRACT_MODE_LINES, EXTRACT_MODE_LINES_COLUMNS, EXTRACT_MODE_OFFSETS})


def _resolve_end_open_by_negative(end_value: int, length: int) -> int:
    if end_value >= 0:
        return end_value
    return length + end_value + 1


def _read_text(file_path: Path, encoding: str) -> str:
    if encoding != "auto":
        return file_path.read_text(encoding=encoding, errors="replace")
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return file_path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="utf-8", errors="replace")


def _write_text(file_path: Path, text: str, encoding: str) -> None:
    file_path.write_text(text, encoding="utf-8" if encoding == "auto" else encoding)


def _ensure_parent_and_file(file_path: Path, encoding: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if not file_path.exists():
        file_path.write_text("", encoding="utf-8" if encoding == "auto" else encoding)


def _append_no_newline(file_path: Path, text: str, encoding: str) -> None:
    with file_path.open("a", encoding="utf-8" if encoding == "auto" else encoding) as f:
        f.write(text)


def _append_with_newline(file_path: Path, text: str, encoding: str) -> None:
    current = _read_text(file_path, encoding)
    with file_path.open("a", encoding="utf-8" if encoding == "auto" else encoding) as f:
        if current and not current.endswith("\n"):
            f.write("\n")
        f.write(text)


def _insert_text(file_path: Path, text: str, start_line: int, start_col: int, encoding: str) -> None:
    if start_line < 1 or start_col < 1:
        raise ValueError("插入模式下 startLine/startColumn 必须 >= 1")

    content = _read_text(file_path, encoding)
    lines = content.splitlines(keepends=True)

    if not lines:
        if start_line != 1 or start_col != 1:
            raise ValueError("空文件仅允许在 startLine=1,startColumn=1 进行插入")
        _write_text(file_path, text, encoding)
        return

    total = len(lines)
    if start_line > total:
        raise ValueError(f"startLine 越界: startLine={start_line}, totalLines={total}")

    idx = start_line - 1
    line = lines[idx]
    line_body = line.rstrip("\r\n")
    line_break = line[len(line_body) :]
    body_len = len(line_body)

    col0 = min(start_col - 1, body_len)
    new_line = line_body[:col0] + text + line_body[col0:] + line_break
    lines[idx] = new_line
    _write_text(file_path, "".join(lines), encoding)


def _replace_range(
    file_path: Path, text: str, start_index: int, end_index: int, encoding: str
) -> tuple[str, str]:
    if start_index < 0 or end_index < 0:
        raise ValueError("replace_range 下 start/end 必须 >= 0")
    if start_index > end_index:
        raise ValueError("replace_range 区间非法：start > end")
    content = _read_text(file_path, encoding)
    n = len(content)
    if end_index > n:
        raise ValueError(f"replace_range end 越界: end={end_index}, textLen={n}")
    before = content
    out = content[:start_index] + text + content[end_index:]
    _write_text(file_path, out, encoding)
    return before, out


def _replace_markers(
    file_path: Path,
    *,
    start_marker: str,
    end_marker: str,
    replacement: str,
    encoding: str,
    search_from: int = 0,
) -> tuple[str, str]:
    content = _read_text(file_path, encoding)
    if not start_marker or not end_marker:
        raise ValueError("replace_markers: empty startMarker or endMarker")
    i0 = content.find(start_marker, search_from)
    if i0 < 0:
        raise ValueError("replace_markers: startMarker not found")
    i1 = content.find(end_marker, i0 + len(start_marker))
    if i1 < 0:
        raise ValueError("replace_markers: endMarker not found after startMarker")
    return _replace_range(file_path, replacement, i0, i1, encoding)


def _replace_literal(file_path: Path, old_text: str, new_text: str, count: int, encoding: str) -> None:
    if not old_text:
        raise ValueError("replace_literal 的 oldText 不能为空")
    content = _read_text(file_path, encoding)
    if count == 0:
        return
    if count < 0:
        out = content.replace(old_text, new_text)
    else:
        out = content.replace(old_text, new_text, count)
    if out == content:
        raise ValueError(f"replace_literal 失败：oldText 在目标文件中未找到匹配（长度 {len(old_text)} 字符）")
    _write_text(file_path, out, encoding)


def _extract_mode_a(lines_keepends: list[str], start_line: int, end_line: int) -> str:
    total = len(lines_keepends)
    if start_line < 1 or end_line < 1 or start_line > end_line or end_line > total:
        raise ValueError(f"行号越界: startLine={start_line}, endLine={end_line}, totalLines={total}")
    return "".join(lines_keepends[start_line - 1 : end_line])


def _line_meta(lines_keepends: list[str]) -> tuple[list[int], list[int]]:
    starts = []
    content_lens = []
    cur = 0
    for ln in lines_keepends:
        starts.append(cur)
        content = ln.rstrip("\r\n")
        content_lens.append(len(content))
        cur += len(ln)
    return starts, content_lens


def _extract_mode_b(
    full_text: str,
    lines_keepends: list[str],
    start_line: int,
    start_col: int,
    end_line: int,
    end_col: int,
) -> str:
    total = len(lines_keepends)
    if start_line < 1 or end_line < 1 or start_line > total or end_line > total:
        raise ValueError(f"行号越界: startLine={start_line}, endLine={end_line}, totalLines={total}")

    starts, content_lens = _line_meta(lines_keepends)
    sl = start_line - 1
    el = end_line - 1
    slen = content_lens[sl]
    elen = content_lens[el]

    if start_col < 1:
        raise ValueError("startColumn 必须 >= 1")
    start_col_py = start_col - 1
    if start_col_py > slen:
        raise ValueError(f"startColumn 越界: line={start_line}, startColumn={start_col}, lineLen={slen}")

    end_col_open_1based = _resolve_end_open_by_negative(end_col, elen)
    end_col_py = end_col_open_1based - 1
    if end_col_py < 0 or end_col_py > elen:
        raise ValueError(
            f"endColumn 越界: line={end_line}, endColumn={end_col}, resolvedEnd={end_col_open_1based}, lineLen={elen}"
        )

    abs_start = starts[sl] + start_col_py
    abs_end = starts[el] + end_col_py
    if abs_end < abs_start:
        raise ValueError("区间无效：结束位置早于起始位置")
    return full_text[abs_start:abs_end]


def _extract_mode_c(full_text: str, start_idx: int, end_idx: int) -> str:
    """offsets 模式 [start, end)：正数 end 超出文本长度时截断到文末，避免 Agent 误写过大 end 即失败。"""
    n = len(full_text)
    if start_idx < 0:
        raise ValueError("start 必须 >= 0")
    start_py = n if start_idx > n else start_idx
    if end_idx < 0:
        end_py = _resolve_end_open_by_negative(end_idx, n)
    else:
        end_py = end_idx
    end_py = max(0, min(end_py, n))
    if end_py < start_py:
        raise ValueError("区间无效：end 早于 start")
    return full_text[start_py:end_py]


def _parse_masks_list(raw: list, text_len: int) -> list[tuple[int, int]]:
    intervals = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"masks 第 {i} 项不是对象")
        if "start" not in item or "end" not in item:
            raise ValueError(f"masks 第 {i} 项缺少 start 或 end")
        s = item["start"]
        e = item["end"]
        if not isinstance(s, int) or not isinstance(e, int):
            raise ValueError(f"masks 第 {i} 项 start/end 必须是整数")
        if s < 0 or e < 0:
            raise ValueError(f"masks 第 {i} 项不支持负索引")
        if s > e:
            raise ValueError(f"masks 第 {i} 项区间非法")
        if e > text_len:
            raise ValueError(f"masks 第 {i} 项 end 越界")
        if s != e:
            intervals.append((s, e))
    return intervals


def _intervals_from_phrases(text: str, phrases: list[str]) -> list[tuple[int, int]]:
    intervals = []
    for p in phrases:
        start = 0
        while True:
            idx = text.find(p, start)
            if idx < 0:
                break
            intervals.append((idx, idx + len(p)))
            start = idx + len(p)
    return intervals



def _intervals_from_line_numbers(text: str, line_numbers: list[int]) -> list[tuple[int, int]]:
    """按行号（1‑based）计算删除区间 [start, end)。"""
    lines = text.splitlines(keepends=True)
    total = len(lines)
    intervals = []
    st, _cl = _line_meta(lines)
    for ln in set(line_numbers):
        if ln < 1 or ln > total:
            raise ValueError(f"lineNumbers 行号越界: {ln}, totalLines={total}")
        idx = ln - 1
        intervals.append((st[idx], st[idx] + len(lines[idx])))
    return intervals


def _intervals_from_line_ranges(text: str, ranges: list[dict]) -> list[tuple[int, int]]:
    """按行范围 [{startLine, endLine}] （闭区间，1‑based）计算区间。"""
    lines = text.splitlines(keepends=True)
    total = len(lines)
    intervals = []
    st, _cl = _line_meta(lines)
    for r in ranges:
        sl = int(r.get("startLine", 1))
        el = int(r.get("endLine", total))
        if sl < 1 or el < 1 or sl > total or el > total or el < sl:
            raise ValueError(f"lineRanges 越界: startLine={sl}, endLine={el}, totalLines={total}")
        s_idx = st[sl - 1]
        e_idx = st[el - 1] + len(lines[el - 1])
        intervals.append((s_idx, e_idx))
    return intervals



# ── 内存操作函数（batch 原子操作复用）──

def _apply_replace_literal_text(text: str, old_text: str, new_text: str, count: int) -> str:
    """内存中的字面替换（不写盘）"""
    if not old_text:
        raise ValueError("replace_literal 的 oldText 不能为空")
    out = text.replace(old_text, new_text, count) if count != 0 else text
    if out == text and count != 0:
        raise ValueError("replace_literal 失败：oldText 在文件中未找到匹配")
    return out


def _apply_replace_range_text(text: str, replacement: str, start_idx: int, end_idx: int) -> str:
    """内存中的区间替换"""
    n = len(text)
    if start_idx < 0 or end_idx < 0 or start_idx > end_idx or end_idx > n:
        raise ValueError(f"replace_range 区间非法: start={start_idx}, end={end_idx}, textLen={n}")
    return text[:start_idx] + replacement + text[end_idx:]


def _apply_append_text(text: str, more: str) -> str:
    """末尾追加（不断行）"""
    return text + more


def _apply_append_line_text(text: str, more: str) -> str:
    """末尾追加（自动补换行）"""
    if text and not text.endswith("\n"):
        return text + "\n" + more
    return text + more


def _apply_insert_text(text: str, insertion: str, start_line: int, start_col: int) -> str:
    """在指定行列插入文本"""
    lines = text.splitlines(keepends=True)
    if not lines:
        if start_line != 1 or start_col != 1:
            raise ValueError("空文本仅允许在 (1,1) 插入")
        return insertion
    total = len(lines)
    if start_line < 1 or start_line > total:
        raise ValueError(f"startLine 越界: {start_line}, totalLines={total}")
    idx = start_line - 1
    line = lines[idx]
    body = line.rstrip("\r\n")
    brk = line[len(body):]
    col0 = min(start_col - 1, len(body)) if start_col >= 1 else 0
    lines[idx] = body[:col0] + insertion + body[col0:] + brk
    return "".join(lines)


def _apply_delete_segments_text(text: str, intervals: list[tuple[int,int]]) -> str:
    """内存中的区间删除（复用已有 _apply_delete_segments）"""
    return _apply_delete_segments(text, intervals)


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: (x[0], x[1]))
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ls, le = merged[-1]
        if s <= le:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


def _apply_delete_segments(text: str, intervals: list[tuple[int, int]]) -> str:
    if not intervals:
        return text
    merged = _merge_intervals(intervals)
    out: list[str] = []
    cur = 0
    for s, e in merged:
        if cur < s:
            out.append(text[cur:s])
        cur = e
    if cur < len(text):
        out.append(text[cur:])
    return "".join(out)


def _read_url_text(url: str, encoding: str) -> str:
    req = Request(url, headers={"User-Agent": "tool-library-structured-editor/1.0"})
    with urlopen(req, timeout=30) as resp:  # nosec B310
        raw = resp.read()
    if encoding != "auto":
        return raw.decode(encoding, errors="replace")
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _load_input_text(
    *,
    file_path: Path | None,
    text_inline: str | None,
    text_stdin: bool,
    url: str | None,
    encoding: str,
    stdin_body: str | None = None,
) -> str:
    n = int(file_path is not None) + int(text_inline is not None) + int(text_stdin) + int(bool(url))
    if n != 1:
        raise ValueError("内容源必须且只能一个：file / text / textStdin / url")
    if file_path is not None:
        if not file_path.is_file():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        return _read_text(file_path, encoding)
    if text_inline is not None:
        return text_inline
    if url:
        return _read_url_text(url, encoding)
    return sys.stdin.read() if stdin_body is None else stdin_body


def _parse_payload(obj: dict) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("payload 必须是 JSON 对象")

    t = obj.get("type")
    if t not in TYPE_ALL:
        raise ValueError(f"type 无效，必须是 {sorted(TYPE_ALL)}")

    if t in {TYPE_APPEND, TYPE_APPEND_LINE, TYPE_INSERT, TYPE_REPLACE_RANGE, TYPE_REPLACE_MARKERS}:
        txt = obj.get("text")
        if not isinstance(txt, str):
            raise ValueError(f"{t} 需要 text 字符串")

    if t == TYPE_INSERT:
        if "startLine" not in obj or "startColumn" not in obj:
            raise ValueError("insert 需要 startLine 和 startColumn")
        if not isinstance(obj["startLine"], int) or not isinstance(obj["startColumn"], int):
            raise ValueError("startLine/startColumn 必须是整数")
    elif t == TYPE_REPLACE_RANGE:
        if "start" not in obj or "end" not in obj:
            raise ValueError("replace_range 需要 start 和 end")
        if not isinstance(obj["start"], int) or not isinstance(obj["end"], int):
            raise ValueError("start/end 必须是整数")
    elif t == TYPE_REPLACE_LITERAL:
        if "oldText" not in obj or "newText" not in obj:
            raise ValueError("replace_literal 需要 oldText 和 newText")
        if not isinstance(obj["oldText"], str) or not isinstance(obj["newText"], str):
            raise ValueError("oldText/newText 必须是字符串")
        if "count" in obj and not isinstance(obj["count"], int):
            raise ValueError("count 必须是整数")
    elif t == TYPE_REPLACE_MARKERS:
        for k in ("startMarker", "endMarker", "text"):
            if k not in obj or not isinstance(obj[k], str):
                raise ValueError("replace_markers needs string field: " + k)
        if "searchFrom" in obj and not isinstance(obj["searchFrom"], int):
            raise ValueError("searchFrom must be int")
    elif t == TYPE_EXTRACT:
        mode = obj.get("mode")
        if mode not in EXTRACT_MODES:
            raise ValueError(f"extract.mode 无效，允许值: {sorted(EXTRACT_MODES)}")
        if mode == EXTRACT_MODE_LINES:
            if not isinstance(obj.get("startLine"), int) or not isinstance(obj.get("endLine"), int):
                raise ValueError("extract mode=lines 需要整数 startLine/endLine")
        elif mode == EXTRACT_MODE_LINES_COLUMNS:
            need = ("startLine", "startColumn", "endLine", "endColumn")
            if any(not isinstance(obj.get(k), int) for k in need):
                raise ValueError(
                    "extract mode=lines_columns 需要整数 startLine/startColumn/endLine/endColumn"
                )
        else:
            if not isinstance(obj.get("start"), int) or not isinstance(obj.get("end"), int):
                raise ValueError("extract mode=offsets 需要整数 start/end")
    elif t == TYPE_DELETE_SEGMENTS:
        masks = obj.get("masks")
        phrases = obj.get("dropPhrases")
        line_numbers = obj.get("lineNumbers")
        line_ranges = obj.get("lineRanges")
        has_masks = isinstance(masks, list) and len(masks) > 0
        has_phrases = isinstance(phrases, list) and any(str(x) for x in phrases)
        has_ln = isinstance(line_numbers, list) and all(isinstance(x, int) for x in line_numbers)
        has_lr = isinstance(line_ranges, list) and all(isinstance(x, dict) and "startLine" in x and "endLine" in x for x in line_ranges)
        if not (has_masks or has_phrases or has_ln or has_lr):
            raise ValueError("delete_segments 需要非空 masks / dropPhrases / lineNumbers / lineRanges")
    elif t == TYPE_BATCH:
        ops = obj.get("operations")
        if not isinstance(ops, list) or len(ops) == 0:
            raise ValueError("batch 需要非空 operations 数组")
        for i, op in enumerate(ops):
            if not isinstance(op, dict):
                raise ValueError(f"batch operations[{i}] 必须是 JSON 对象")
            ot = op.get("type")
            if ot not in TYPE_MUTATE | {TYPE_DELETE_SEGMENTS}:
                raise ValueError(
                    f"batch operations[{i}].type 无效: {ot}，允许值: "
                    + ", ".join(sorted(TYPE_MUTATE | {TYPE_DELETE_SEGMENTS}))
                )


    return obj


def build_parser() -> argparse.ArgumentParser:
    epilog = (
        "Windows/PowerShell：JSON 优先 --requestStdin/--payloadStdin；避免单行超长 --requestJson。\n"
        "胶水代码：.py 用 UTF-8 保存，路径与中文可直接写字面量或 Path 分段；勿把中文正文塞进 PowerShell 的 here-string；需要时把路径交给 --file。\n"
        "大段替换文本：用 UTF-8 的 *File 或 stdin 传 JSON；不要把整段嵌进 here-string。\n"
        "一次性块替换：payload type=replace_markers（startMarker、endMarker、text）。\n"
        "写新文件：payload type=append 或 append_line（文件不存在时自动创建）。\n"
    )
    p = _HelpFulParser(
        description="结构化编辑：写盘 / 提取 / 按区间或短语删除",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    p.add_argument("--requestJson", help="统一请求 JSON 字符串")
    p.add_argument("--requestFile", help="从文件读取统一请求 JSON")
    p.add_argument("--requestStdin", action="store_true", help="从 stdin 读取统一请求 JSON")
    p.add_argument("--file", help="目标文件路径（写盘类必填；读入类与 --text/--textStdin/--url 四选一）")
    p.add_argument("--text", help="直接传入待处理文本（读入类）")
    p.add_argument("--textStdin", action="store_true", help="从 stdin 读取待处理文本（读入类）")
    p.add_argument("--url", help="从 URL 读取待处理文本（读入类）")
    p.add_argument("--payload", help="动作 JSON")
    p.add_argument("--payloadFile", help="从文件读取动作 JSON")
    p.add_argument("--payloadStdin", action="store_true", help="从 stdin 读取动作 JSON")
    p.add_argument("--runType", choices=["auto", "plan", "execute"], default="", help="当前运行模式；plan 时写操作（append/append_line/insert/replace_*）将被拒绝")
    p.add_argument("--encoding", default="utf-8", help="读写编码，默认 utf-8，可选 auto")
    p.add_argument("--jsonOut", action="store_true", help="按统一协议输出 JSON")
    return p


def _emit(args: argparse.Namespace, ok: bool, data=None, error=None, legacy_text: str = "") -> None:
    if args.jsonOut:
        print(json.dumps({"ok": ok, "data": data, "error": error}, ensure_ascii=False))
    else:
        if not ok:
            return
        if legacy_text:
            print(legacy_text, end="")
        else:
            print("ok")


def _load_request(args: argparse.Namespace, *, stdin_override: str | None = None) -> dict | None:
    req_count = int(args.requestJson is not None) + int(args.requestFile is not None) + int(args.requestStdin)
    if req_count == 0:
        return None
    if req_count != 1:
        raise ValueError("request 输入必须且只能一个：--requestJson / --requestFile / --requestStdin")
    if args.requestJson is not None:
        raw = args.requestJson
    elif args.requestFile is not None:
        raw = Path(args.requestFile).read_text(encoding="utf-8", errors="replace")
    else:
        raw = stdin_override if stdin_override is not None else sys.stdin.read()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"request 不是合法 JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("request 必须是 JSON 对象")
    return obj


def _load_payload_raw(args: argparse.Namespace, *, stdin_override: str | None = None) -> str:
    n = int(args.payload is not None) + int(args.payloadFile is not None) + int(args.payloadStdin)
    if n != 1:
        raise ValueError("payload 必须且只能一个：--payload / --payloadFile / --payloadStdin")
    if args.payload is not None:
        return args.payload
    if args.payloadFile is not None:
        return Path(args.payloadFile).read_text(encoding="utf-8", errors="replace")
    return stdin_override if stdin_override is not None else sys.stdin.read()


def _resolve_paths_and_encoding(
    args: argparse.Namespace, req: dict | None
) -> tuple[Path | None, str | None, bool, str | None, str]:
    """返回 (file_path, text, text_stdin, url, encoding)。"""
    encoding = args.encoding
    file_path: Path | None = None
    text_val: str | None = None
    text_stdin = False
    url: str | None = None

    if req is not None:
        encoding = str(req.get("encoding", encoding))
        if "file" in req and req["file"] is not None:
            file_path = Path(str(req["file"]))
        if "text" in req and req["text"] is not None:
            text_val = str(req["text"])
        if req.get("textStdin"):
            text_stdin = True
        if "url" in req and req["url"] is not None:
            url = str(req["url"])

    if args.file:
        file_path = Path(args.file)
    if args.text is not None:
        text_val = args.text
    if args.textStdin:
        text_stdin = True
    if args.url:
        url = args.url

    return file_path, text_val, text_stdin, url, encoding


def _run_mutate(file_path: Path, payload: dict, encoding: str) -> dict:
    _ensure_parent_and_file(file_path, encoding)
    t = payload["type"]
    if t == TYPE_APPEND:
        _append_no_newline(file_path, payload["text"], encoding)
    elif t == TYPE_APPEND_LINE:
        _append_with_newline(file_path, payload["text"], encoding)
    elif t == TYPE_INSERT:
        _insert_text(file_path, payload["text"], payload["startLine"], payload["startColumn"], encoding)
    elif t == TYPE_REPLACE_RANGE:
        fb, fa = _replace_range(
            file_path, payload["text"], payload["start"], payload["end"], encoding
        )
        return {
            "file": str(file_path),
            "type": t,
            "previewFullBefore": fb,
            "previewFullAfter": fa,
        }
    elif t == TYPE_REPLACE_MARKERS:
        sf = int(payload.get("searchFrom", 0))
        fb, fa = _replace_markers(
            file_path,
            start_marker=payload["startMarker"],
            end_marker=payload["endMarker"],
            replacement=payload["text"],
            encoding=encoding,
            search_from=sf,
        )
        return {
            "file": str(file_path),
            "type": t,
            "previewFullBefore": fb,
            "previewFullAfter": fa,
        }
    else:
        _replace_literal(
            file_path,
            payload["oldText"],
            payload["newText"],
            payload.get("count", -1),
            encoding,
        )
    return {"file": str(file_path), "type": t}


def _call_preview_render(*, text: str | None = None, file_path: str | None = None, max_chars: int = 12000) -> dict | None:
    # 进程内直接导入并调用 cli_preview_render（避免 subprocess 启动第二个进程）
    import importlib, io, json, sys as _sys
    try:
        mod = importlib.import_module("cli_preview_render")
    except Exception:
        return None
    argv = ["cli_preview_render.py", "--jsonOut", "--label", "预览", "--maxChars", str(max_chars)]
    if isinstance(file_path, str) and file_path.strip():
        argv.extend(["--file", file_path])
    elif isinstance(text, str):
        argv.extend(["--text", text])
    else:
        return None
    with _INLINE_TOOL_LOCK:
        old_argv = _sys.argv
        old_stdout = _sys.stdout
        captured = io.StringIO()
        try:
            _sys.argv = argv
            _sys.stdout = captured
            mod.main()
        except SystemExit:
            pass
        except Exception:
            return None
        finally:
            _sys.stdout = old_stdout
            _sys.argv = old_argv
    output = captured.getvalue().strip()
    if not output:
        return None
    try:
        parsed = json.loads(output.splitlines()[-1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _run_extract(text: str, payload: dict) -> tuple[dict, str]:
    mode = payload["mode"]
    lines_keepends = text.splitlines(keepends=True)
    if mode == EXTRACT_MODE_LINES:
        out = _extract_mode_a(lines_keepends, payload["startLine"], payload["endLine"])
    elif mode == EXTRACT_MODE_LINES_COLUMNS:
        out = _extract_mode_b(
            text,
            lines_keepends,
            payload["startLine"],
            payload["startColumn"],
            payload["endLine"],
            payload["endColumn"],
        )
    else:
        out = _extract_mode_c(text, payload["start"], payload["end"])

    out_file = payload.get("outFile")
    out_encoding = str(payload.get("encoding", "utf-8"))
    use_preview = bool(payload.get("usePreview", False))
    data: dict = {"type": TYPE_EXTRACT, "mode": mode, "resultLen": len(out), "outFile": out_file, "usePreview": use_preview}
    if out_file:
        op = Path(str(out_file))
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(out, encoding=out_encoding)
        data["written"] = True
    else:
        data["written"] = False

    if use_preview:
        preview_obj = _call_preview_render(text=None if out_file else out, file_path=str(out_file) if out_file else None)
        if isinstance(preview_obj, dict) and preview_obj.get("ok") and isinstance(preview_obj.get("data"), dict):
            pdata = preview_obj["data"]
            ptext = pdata.get("previewText")
            if isinstance(ptext, str):
                data["text"] = ptext
            data["previewLinked"] = True
        else:
            data["previewLinked"] = False
    if out_file:
        return data, ""
    return data, out


def _run_delete_segments(text: str, payload: dict) -> tuple[dict, str]:
    intervals: list[tuple[int, int]] = []
    preview_only = bool(payload.get("preview", False))
    masks = payload.get("masks")
    if isinstance(masks, list) and masks:
        intervals.extend(_parse_masks_list(masks, len(text)))
    phrases = payload.get("dropPhrases")
    if isinstance(phrases, list) and phrases:
        plist = [str(x) for x in phrases if str(x)]
        intervals.extend(_intervals_from_phrases(text, plist))
    line_numbers = payload.get("lineNumbers")
    if isinstance(line_numbers, list) and line_numbers:
        intervals.extend(_intervals_from_line_numbers(text, [int(x) for x in line_numbers]))
    line_ranges = payload.get("lineRanges")
    if isinstance(line_ranges, list) and line_ranges:
        intervals.extend(_intervals_from_line_ranges(text, line_ranges))

    result = _apply_delete_segments(text, intervals)
    out_file = payload.get("outFile")
    out_encoding = str(payload.get("encoding", "utf-8"))
    deleted = len(text) - len(result)
    data: dict = {
        "type": TYPE_DELETE_SEGMENTS,
        "resultLen": len(result),
        "outFile": out_file,
        "deletedChars": deleted,
        "written": False,
    }
    if preview_only:
        data["previewText"] = result[:5000] + ("..." if len(result) > 5000 else "")
        data["preview"] = True
        return data, ""
    if out_file:
        op = Path(str(out_file))
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(result, encoding=out_encoding)
        data["written"] = True
        return data, ""
    data["written"] = True
    return data, result


def _run_batch(file_path: Path, payload: dict, encoding: str) -> dict:
    """批量原子操作：读文件 → 内存中依序执行全部 operations → 全部成功后一次性写回。"""
    text = _read_text(file_path, encoding) if file_path else ""
    operations = payload["operations"]
    ops_done = 0
    try:
        for i, op in enumerate(operations):
            t = op["type"]
            if t == TYPE_REPLACE_LITERAL:
                text = _apply_replace_literal_text(
                    text, str(op["oldText"]), str(op["newText"]),
                    int(op.get("count", -1)),
                )
            elif t == TYPE_REPLACE_RANGE:
                text = _apply_replace_range_text(
                    text, str(op["text"]), int(op["start"]), int(op["end"]),
                )
            elif t == TYPE_REPLACE_MARKERS:
                sm = str(op["startMarker"]); em = str(op["endMarker"])
                sf = int(op.get("searchFrom", 0))
                i0 = text.find(sm, sf)
                if i0 < 0:
                    raise ValueError(f"batch[{i}]: startMarker 未找到")
                i1 = text.find(em, i0 + len(sm))
                if i1 < 0:
                    raise ValueError(f"batch[{i}]: endMarker 未找到")
                text = _apply_replace_range_text(text, str(op["text"]), i0, i1)
            elif t == TYPE_APPEND:
                text = _apply_append_text(text, str(op["text"]))
            elif t == TYPE_APPEND_LINE:
                text = _apply_append_line_text(text, str(op["text"]))
            elif t == TYPE_INSERT:
                text = _apply_insert_text(
                    text, str(op["text"]),
                    int(op["startLine"]), int(op["startColumn"]),
                )
            elif t == TYPE_DELETE_SEGMENTS:
                intervals = []
                masks = op.get("masks")
                if isinstance(masks, list) and masks:
                    intervals.extend(_parse_masks_list(masks, len(text)))
                phrases = op.get("dropPhrases")
                if isinstance(phrases, list) and phrases:
                    plist = [str(x) for x in phrases if str(x)]
                    intervals.extend(_intervals_from_phrases(text, plist))
                ln = op.get("lineNumbers")
                if isinstance(ln, list) and ln:
                    intervals.extend(_intervals_from_line_numbers(text, [int(x) for x in ln]))
                lr = op.get("lineRanges")
                if isinstance(lr, list) and lr:
                    intervals.extend(_intervals_from_line_ranges(text, lr))
                if intervals:
                    text = _apply_delete_segments_text(text, intervals)
            else:
                raise ValueError(f"batch[{i}]: 不支持的操作类型: {t}")
            ops_done = i + 1
        _write_text(file_path, text, encoding)
        return {"type": TYPE_BATCH, "operations": ops_done, "resultLen": len(text), "written": True}
    except Exception as e:
        return {
            "type": TYPE_BATCH,
            "operations": ops_done,
            "failedStep": ops_done,
            "error": str(e),
            "written": False,
        }



def _structured_edit_run(
    args: argparse.Namespace,
    *,
    request_stdin_body: str | None = None,
    payload_stdin_body: str | None = None,
    text_stdin_body: str | None = None,
) -> dict:
    req = _load_request(
        args,
        stdin_override=request_stdin_body if args.requestStdin else None,
    )
    if req is not None:
        file_path, text_val, text_stdin, url, encoding = _resolve_paths_and_encoding(args, req)
        inner = req.get("payload")
        if inner is None:
            raise ValueError("request.payload 必填")
        if isinstance(inner, dict):
            payload = _parse_payload(inner)
        elif isinstance(inner, str):
            payload = _parse_payload(json.loads(inner))
        else:
            raise ValueError("request.payload 必须是对象或 JSON 字符串")
    else:
        payload_raw = _load_payload_raw(
            args,
            stdin_override=payload_stdin_body if args.payloadStdin else None,
        )
        payload = _parse_payload(json.loads(payload_raw))
        file_path, text_val, text_stdin, url, encoding = _resolve_paths_and_encoding(args, None)

    t = payload["type"]

    run_type = str(getattr(args, "runType", "") or "").strip().lower()
    if run_type == "plan":
        if t in TYPE_MUTATE or t == TYPE_BATCH:
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写操作"}}
        if t in (TYPE_EXTRACT, TYPE_DELETE_SEGMENTS) and payload.get("outFile"):
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，禁止任何落盘操作（含 outFile）"}}

    if t == TYPE_BATCH:
        if not file_path:
            raise ValueError("batch 操作需要指定目标文件（--file 或 request.file）")
        data = _run_batch(file_path, payload, encoding)
        if not data.get("written") and data.get("error"):
            return {
                "ok": False,
                "data": data,
                "error": {"type": "BatchError", "message": f"step {data.get('failedStep',0)}: {data.get('error')}"},
            }
        return {"ok": True, "data": data}
    if t in TYPE_MUTATE:
        if file_path is None or not str(file_path):
            raise ValueError("写盘类操作需要 file 路径")
        data = _run_mutate(file_path, payload, encoding)
        return {"ok": True, "data": data, "error": None, "_legacy_text": ""}

    text = _load_input_text(
        file_path=file_path,
        text_inline=text_val,
        text_stdin=text_stdin,
        url=url,
        encoding=encoding,
        stdin_body=text_stdin_body if text_stdin else None,
    )

    if t == TYPE_EXTRACT:
        data, legacy = _run_extract(text, payload)
        if not data.get("written"):
            data = dict(data)
            data["text"] = legacy
        leg = "" if data.get("written") else legacy
        return {"ok": True, "data": data, "error": None, "_legacy_text": leg}

    data, legacy = _run_delete_segments(text, payload)
    if not data.get("written"):
        data = dict(data)
        data["text"] = legacy
    leg = "" if data.get("written") else legacy
    return {"ok": True, "data": data, "error": None, "_legacy_text": leg}


def agent_main(
    *,
    request: dict | None = None,
    request_json: str | None = None,
    request_file: str | None = None,
    request_stdin: bool = False,
    request_stdin_body: str | None = None,
    file: str | None = None,
    text: str | None = None,
    text_stdin: bool = False,
    text_stdin_body: str | None = None,
    url: str | None = None,
    payload: str | dict | None = None,
    payload_file: str | None = None,
    payload_stdin: bool = False,
    payload_stdin_body: str | None = None,
    encoding: str = "utf-8",
    run_type: str = "",
) -> dict:
    parser = build_parser()
    rj = request_json
    if request is not None:
        rj = json.dumps(request, ensure_ascii=False)
    req_n = int(rj is not None and str(rj).strip() != "") + int(request_file is not None and str(request_file).strip() != "") + int(bool(request_stdin))
    pay_s: str | None
    if isinstance(payload, dict):
        pay_s = json.dumps(payload, ensure_ascii=False)
    elif payload is not None:
        pay_s = str(payload)
    else:
        pay_s = None
    pay_n = int(pay_s is not None and str(pay_s).strip() != "") + int(payload_file is not None and str(payload_file).strip() != "") + int(bool(payload_stdin))
    if req_n > 1:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "request 输入必须且只能一个"}}
    if pay_n > 1:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "payload 必须且只能一个"}}
    if req_n == 1 and pay_n != 0:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "request 与 payload 不能同时使用"}}
    if req_n == 0 and pay_n != 1:
        return {"ok": False, "data": None, "error": {"type": "ValueError", "message": "须提供 request 或 payload 之一"}}

    if req_n == 1:
        args = argparse.Namespace(
            requestJson=rj,
            requestFile=request_file,
            requestStdin=bool(request_stdin),
            file=file,
            text=text,
            textStdin=bool(text_stdin),
            url=url,
            payload=None,
            payloadFile=None,
            payloadStdin=False,
            encoding=encoding,
            jsonOut=True,
            runType=run_type,
        )
    else:
        args = argparse.Namespace(
            requestJson=None,
            requestFile=None,
            requestStdin=False,
            file=file,
            text=text,
            textStdin=bool(text_stdin),
            url=url,
            payload=pay_s,
            payloadFile=payload_file,
            payloadStdin=bool(payload_stdin),
            encoding=encoding,
            jsonOut=True,
            runType=run_type,
        )
    try:
        out = _structured_edit_run(
            args,
            request_stdin_body=request_stdin_body,
            payload_stdin_body=payload_stdin_body,
            text_stdin_body=text_stdin_body,
        )
        out.pop("_legacy_text", None)
        return {"ok": out["ok"], "data": out["data"], "error": out["error"]}
    except Exception as e:
        msg = str(e) + "\n\n--help:\n" + _capture_help(parser)
        return {"ok": False, "data": None, "error": {"type": e.__class__.__name__, "message": msg}}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        out = _structured_edit_run(args)
        legacy = str(out.pop("_legacy_text", "") or "")
        _emit(args, out["ok"], data=out["data"], error=out.get("error"), legacy_text=legacy)
    except Exception as e:
        e.args = (str(e) + "\n\n--help:\n" + _capture_help(parser),)
        _emit(args, False, data=None, error={"type": e.__class__.__name__, "message": str(e)})
        if not args.jsonOut:
            raise


if __name__ == "__main__":
    main()
