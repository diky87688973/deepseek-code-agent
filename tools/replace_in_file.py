# -*- coding: utf-8 -*-
"""单文件替换：字面量规则，或按 region/regions 字符区间 / 行列矩形覆盖写。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

import difflib

import agent_common as ac


def _text_ends_with_newline(text: str) -> bool:
    return text.endswith("\n") or text.endswith("\r\n")


def _validate_line_replace_trailing_newline(
    new_text: str,
    *,
    has_following_lines: bool,
    line_start: int,
    line_end: int,
    auto_newline: bool = False,
) -> Tuple[str, bool]:
    """行替换检查：后置有行时 new_text 必须以换行结尾，否则黏连。
    返回 (new_text, auto_appended)。
    auto_newline=true 时仅在「后置有行且在行替换模式」下自动补换行，防止黏连。
    """
    if not has_following_lines or not new_text or _text_ends_with_newline(new_text):
        return new_text, False
    if auto_newline:
        return new_text + "\n", True
    next_line = line_end + 1
    raise ValueError(
        f"行替换 new_text 末尾缺少换行符（第 {line_start}–{line_end} 行）。"
        f"其后仍有第 {next_line} 行及之后的内容；若不在 new_text 末尾写入换行符，"
        f"新内容的最后一行将与下一行黏连在同一物理行上。"
        f"请先 read_file(path, line_start={line_start}, line_end={line_end}) "
        f"对照该区间 content 的换行形态，或在 new_text 末尾补上 \\n 后再提交；"
        f"或传 auto_newline=true 让宿主仅在行替换有后置行时自动补 \\n。"
    )


def _merge_literal_rules(
    old_text: Optional[str],
    new_text: Optional[str],
    rules: Optional[list],
) -> List[Tuple[str, str]]:
    has_pair = old_text is not None or new_text is not None
    out: List[Tuple[str, str]] = []
    if has_pair:
        if old_text is None or new_text is None:
            raise ValueError("old_text 与 new_text 必须成对出现")
        if old_text == "":
            raise ValueError("old_text 不能为空（避免误替换整文件为空）")
        out.append((old_text, new_text))
    if rules:
        for i, item in enumerate(rules):
            if not isinstance(item, dict):
                raise ValueError(f"rules[{i}] 必须是对象")
            o = item.get("old_text")
            n = item.get("new_text")
            if not isinstance(o, str) or not isinstance(n, str):
                raise ValueError(f"rules[{i}] 须含字符串字段 old_text、new_text")
            if o == "":
                raise ValueError(f"rules[{i}] 的 old 片段不能为空")
            out.append((o, n))
    if not out:
        raise ValueError("须提供 old_text+new_text，或传入非空 rules")
    return out


def _raw_escape_text(text: str) -> str:
    """将 JSON 中的真实控制字符转为源码里常见的字面转义序列。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace("\t", "\\t")


def _apply_raw_to_literal_inputs(
    old_text: Optional[str],
    new_text: Optional[str],
    rules: Optional[list],
) -> Tuple[Optional[str], Optional[str], Optional[list]]:
    if old_text is not None:
        old_text = _raw_escape_text(old_text)
    if new_text is not None:
        new_text = _raw_escape_text(new_text)
    if rules:
        out = []
        for item in rules:
            if not isinstance(item, dict):
                out.append(item)
                continue
            copied = dict(item)
            if isinstance(copied.get("old_text"), str):
                copied["old_text"] = _raw_escape_text(copied["old_text"])
            if isinstance(copied.get("new_text"), str):
                copied["new_text"] = _raw_escape_text(copied["new_text"])
            out.append(copied)
        rules = out
    return old_text, new_text, rules


def _apply_rules_sequential(
    original: str,
    rules: List[Tuple[str, str]],
    *,
    replace_all: bool,
) -> Tuple[str, List[int]]:
    # 统一换行符为 \n，消除 Windows/Mac/Linux 差异
    cur = original.replace("\r\n", "\n").replace("\r", "\n")
    counts: List[int] = []
    for old_s, new_s in rules:
        old_s = old_s.replace("\r\n", "\n").replace("\r", "\n")
        new_s = new_s.replace("\r\n", "\n").replace("\r", "\n")
        if replace_all:
            c = cur.count(old_s)
            if c:
                cur = cur.replace(old_s, new_s)
        else:
            c = 1 if old_s in cur else 0
            cur = cur.replace(old_s, new_s, 1) if c else cur
        counts.append(c)
    return cur, counts


def _first_content_line(text: str) -> str:
    lines = text.splitlines()
    return lines[0].strip() if lines else ""


def _last_content_line(text: str) -> str:
    lines = text.splitlines()
    return lines[-1].strip() if lines else ""


def _detect_warnings(
    *,
    mode: str,
    rep: str,
    before: str,
    after: str,
    original: str,
    new_body: str,
) -> List[str]:
    warnings: List[str] = []
    if mode == "linerange" and rep and after:
        rep_last = _last_content_line(rep)
        after_first = _first_content_line(after)
        if rep_last and rep_last == after_first:
            warnings.append("替换文本末行与替换范围后的首行相同，可能是 line_end 过窄导致重复行。")

    normalized = new_body.replace("\r\n", "\n").replace("\r", "\n")
    if "\n\n\n\n" in normalized:
        warnings.append("结果中出现 2 个以上连续空行，请确认替换范围和换行是否正确。")

    if original == new_body:
        warnings.append("替换后内容未变化，请确认目标范围或 old_text 是否符合预期。")

    return warnings


def _detect_replace_modes(
    *,
    old_text: Optional[str],
    new_text: Optional[str],
    rules: Optional[list],
    regions: Optional[list],
    line_ranges: Optional[list],
    region_start: Optional[int],
    region_end: Optional[int],
    line_start: Optional[int],
    line_end: Optional[int],
    start_column: Optional[int],
    end_column: Optional[int],
) -> str:
    has_literal = bool(rules) or (old_text is not None and new_text is not None)
    has_regions = bool(regions)
    has_line_ranges = bool(line_ranges)
    partial_rs = (region_start is not None) ^ (region_end is not None)
    if partial_rs:
        raise ValueError("region_start 与 region_end 须同时指定")
    mode_offset = region_start is not None and region_end is not None

    # linerange：仅行号，无列坐标 → 按行替换（行号不会漂移）
    has_linerange = (
        line_start is not None and line_end is not None
        and start_column is None and end_column is None
    )
    # box：行号 + 列坐标 → 矩形替换
    has_box = (
        line_start is not None and line_end is not None
        and start_column is not None and end_column is not None
    )
    # 行参数混用检查
    any_line_param = line_start is not None or line_end is not None
    if any_line_param and not has_linerange and not has_box:
        raise ValueError(
            "行参数不完整：纯行替换须提供 line_start+line_end（不传列坐标）；"
            "矩形替换须同时提供 line_start、line_end、start_column、end_column"
        )

    active = int(has_literal) + int(has_regions) + int(has_line_ranges) + int(mode_offset) + int(has_linerange) + int(has_box)
    if active > 1:
        raise ValueError("字面替换、regions、line_ranges、region 区间、行替换、矩形替换不可混用")
    if active == 0:
        raise ValueError(
            "须指定替换方式：字面 old_text+new_text / rules，或 regions 数组，"
            "或 region_start+region_end，或 line_start+line_end（行替换），"
            "或 line_start+line_end+列坐标（矩形替换）"
        )
    if has_literal:
        return "literal"
    if has_regions:
        return "regions"
    if has_line_ranges:
        return "line_ranges"
    if mode_offset:
        return "offset"
    if has_linerange:
        return "linerange"
    return "box"


def agent_main(
    *,
    path: str = "",
    old_text: Optional[str] = None,
    new_text: Optional[str] = None,
    rules: Optional[list] = None,
    regions: Optional[list] = None,
    line_ranges: Optional[list] = None,
    region_start: Optional[int] = None,
    region_end: Optional[int] = None,
    line_start: Optional[int] = None,
    line_end: Optional[int] = None,
    start_column: Optional[int] = None,
    end_column: Optional[int] = None,
    dry_run: bool = True,
    replace_all: bool = True,
    expected_replacements: Optional[int] = None,
    encoding: str = "utf-8",
    backup: bool = True,
    raw: bool = False,
    restrict_to_workspace: bool = False,
    run_type: str = "",
    confirm_id: str = "",
    cancel_previous: bool = False,
    auto_newline: bool = False,
) -> dict:
    try:
        if rules is not None and isinstance(rules, str):
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "ValueError",
                    "message": "rules 须为 List[dict]，禁止传入 JSON 字符串；由宿主解析参数对象传入。",
                },
            }
        rt = str(run_type or "").strip().lower()
        _appended = False
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}
        if want_write and not confirm_id:
            return {"ok": False, "data": None, "error": {"type": "ConfirmIdRequired", "message": "dry_run=false 必须传 confirm_id：请先 dry_run=true 预览，再用返回的 confirm_id 提交。"}}

        # ── confirm_id 模式校验：检测模型是否同时传了编辑参数 ──
        _EDIT_KEYS = ["path", "old_text", "new_text", "rules", "regions", "line_ranges",
                      "region_start", "region_end", "line_start", "line_end",
                      "start_column", "end_column", "replace_all", "expected_replacements",
                      "encoding", "raw", "restrict_to_workspace"]
        _EDIT_VALS = {k: v for k, v in locals().items() if k in _EDIT_KEYS}
        _DEFAULTS = {"path": "", "encoding": "utf-8", "raw": False,
                     "restrict_to_workspace": False, "replace_all": True}
        if confirm_id and not dry_run:
            _extra = [k for k, v in _EDIT_VALS.items()
                      if v is not None and v != _DEFAULTS.get(k) and v != [] and v != {}]
            if _extra:
                return {"ok": False, "data": None, "error": {
                    "type": "BadToolArguments",
                    "message": "确认提交模式（dry_run=false + confirm_id）不接受编辑参数。"
                               f"你同时传了 confirm_id 和以下参数：{', '.join(_extra)}。"
                               "请只传 confirm_id + dry_run=false，编辑参数从预览存储自动恢复。",
                }}

        # ── confirm_id 模式：从预览存储的参数恢复，模型只需传 confirm_id + dry_run=false ──
        if confirm_id:
            from agent_v4.live_state import consume_confirm_id, confirm_id_needs_review
            cid_str = str(confirm_id).strip()
            if confirm_id_needs_review(cid_str):
                return {"ok": False, "data": None, "error": {"type": "DiffReviewRequired", "message": "请先调用 review_conclusion(confirm_id=...) 提交 diff review 结论后，再 dry_run=false 提交。"}}
            stored = consume_confirm_id(cid_str)
            if stored is None:
                return {"ok": False, "data": None, "error": {"type": "InvalidConfirmId", "message": "confirm_id 无效或已过期，请重新 dry_run=true 预览"}}
            if stored.get("action") != "replace_in_file":
                return {"ok": False, "data": None, "error": {"type": "CrossToolConfirmId", "message": f"该 confirm_id 由 {stored.get('action','?')} 创建，replace_in_file 无法消费，请使用创建该 ID 的原始工具提交"}}
            sp = stored.get("params", {})
            path = sp.get("path", path)
            old_text = sp.get("old_text", old_text)
            new_text = sp.get("new_text", new_text)
            rules = sp.get("rules", rules)
            regions = sp.get("regions", regions)
            line_ranges = sp.get("line_ranges", line_ranges)
            region_start = sp.get("region_start", region_start)
            region_end = sp.get("region_end", region_end)
            line_start = sp.get("line_start", line_start)
            line_end = sp.get("line_end", line_end)
            start_column = sp.get("start_column", start_column)
            end_column = sp.get("end_column", end_column)
            replace_all = sp.get("replace_all", replace_all)
            # confirm_id 隐含已审查，强制写入
            dry_run = False

        mode = _detect_replace_modes(
            old_text=old_text,
            new_text=new_text,
            rules=rules,
            regions=regions,
            line_ranges=line_ranges,
            region_start=region_start,
            region_end=region_end,
            line_start=line_start,
            line_end=line_end,
            start_column=start_column,
            end_column=end_column,
        )

        fp = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        if not fp.is_file():
            raise FileNotFoundError(f"文件不存在: {fp}")

        original = ac.read_file_text(fp, encoding)
        counts_per_rule: List[int]
        rule_list: List[Tuple[str, str]] = []

        if mode == "literal":
            if raw:
                old_text, new_text, rules = _apply_raw_to_literal_inputs(old_text, new_text, rules)
            rule_list = _merge_literal_rules(old_text, new_text, rules)
            # ── 安全校验：禁止 old_text 含反斜杠（匹配转义序列如 \n、\t、\\ 等极易因 JSON 转义歧义导致匹配失败） ──
            for old_s, _ in rule_list:
                if "\\" in old_s and not raw:
                    raise ValueError(
                        "old_text 含反斜杠字符，禁止字面替换。\n"
                        "  原因：JSON 的 \\n、\\t、\\\\ 等经 JSON 解码后歧义大，极易匹配失败。\n"
                        "  方案一：若反斜杠是字面字符（非 JSON 转义），传 raw=true。\n"
                        "  方案二：改用 line_start+line_end（行替换，坐标来自 grep_files/read_file）。\n"
                        f"  内容：{old_s!r}"
                    )
            new_body, counts_per_rule = _apply_rules_sequential(
                original, rule_list, replace_all=replace_all
            )
        elif mode == "regions":
            # 多区间模式：按 region_start 降序排序，避免坐标漂移
            validated = []
            for i, item in enumerate(regions or []):
                if not isinstance(item, dict):
                    raise ValueError(f"regions[{i}] 必须是对象")
                rs = item.get("region_start")
                re_ = item.get("region_end")
                nt = item.get("new_text", "")
                if not isinstance(rs, int) or not isinstance(re_, int):
                    raise ValueError(f"regions[{i}] 须含整数 region_start、region_end")
                if not isinstance(nt, str):
                    raise ValueError(f"regions[{i}] 的 new_text 须为字符串")
                validated.append((int(rs), int(re_), nt))
            validated.sort(key=lambda x: x[0], reverse=True)  # 降序！防止漂移
            # 检查重叠：降序相邻两项，后一项 end > 前一项 start 即重叠
            for i in range(len(validated) - 1):
                if validated[i + 1][1] > validated[i][0]:
                    raise ValueError(
                        f"regions 区间重叠：[{validated[i+1][0]},{validated[i+1][1]}) "
                        f"与 [{validated[i][0]},{validated[i][1]}) 不可共存"
                    )
            new_body = original
            counts_per_rule = []
            for rs, re_, nt in validated:
                if rs < 0 or re_ > len(new_body) or rs > re_:
                    raise ValueError(f"region [{rs},{re_}) 越界（当前文本长度 {len(new_body)}）")
                new_body = new_body[:rs] + nt + new_body[re_:]
                counts_per_rule.append(1 if nt != original[rs:re_] else 0)
        elif mode == "line_ranges":
            validated = []
            for i, item in enumerate(line_ranges or []):
                if not isinstance(item, dict):
                    raise ValueError(f"line_ranges[{i}] 必须是对象")
                ls = item.get("line_start")
                le = item.get("line_end")
                nt = item.get("new_text", "")
                if not isinstance(ls, int) or not isinstance(le, int):
                    raise ValueError(f"line_ranges[{i}] 须含整数 line_start、line_end")
                if not isinstance(nt, str):
                    raise ValueError(f"line_ranges[{i}] 的 new_text 须为字符串")
                validated.append((int(ls), int(le), nt))
            validated.sort(key=lambda x: x[0], reverse=True)  # 降序！防止漂移
            # 重叠检测：降序相邻，后一项 end >= 前一项 start 即重叠
            for i in range(len(validated) - 1):
                if validated[i + 1][1] >= validated[i][0]:
                    raise ValueError(
                        f"line_ranges 行区间重叠：[{validated[i+1][0]},{validated[i+1][1]}] "
                        f"与 [{validated[i][0]},{validated[i][1]}] 不可共存"
                    )
            new_body = original
            counts_per_rule = []
            for ls, le, nt in validated:
                lines_kd = new_body.splitlines(keepends=True)
                total = len(lines_kd)
                s = ls - 1  # 0-based
                e = le - 1
                if s < 0:
                    s = 0
                if e >= total:
                    e = total - 1
                if s > e:
                    new_body = original
                    counts_per_rule = [0]
                    continue
                before = "".join(lines_kd[:s])
                after = "".join(lines_kd[e + 1:])
                nt, _appended = _validate_line_replace_trailing_newline(
                    nt,
                    has_following_lines=bool(after),
                    line_start=ls,
                    line_end=le,
                    auto_newline=auto_newline,
                )
                new_body = before + nt + after
                counts_per_rule.append(1)
        elif mode == "offset":
            rep = "" if new_text is None else str(new_text)
            new_body = ac.apply_range_replace(
                original, rep, int(region_start), int(region_end)
            )
            counts_per_rule = [1 if new_body != original else 0]
        elif mode == "linerange":
            rep = "" if new_text is None else str(new_text)
            lines_keepends = original.splitlines(keepends=True)
            total = len(lines_keepends)
            ls = int(line_start) - 1  # 转 0-based
            le = int(line_end) - 1
            if ls < 0:
                ls = 0
            if le >= total:
                le = total - 1
            if ls > le:
                new_body = original
                counts_per_rule = [0]
            else:
                before = "".join(lines_keepends[:ls])
                after = "".join(lines_keepends[le + 1:])
                rep, _appended = _validate_line_replace_trailing_newline(
                    rep,
                    has_following_lines=bool(after),
                    line_start=int(line_start),
                    line_end=int(line_end),
                    auto_newline=auto_newline,
                )
                new_body = before + rep + after
                counts_per_rule = [1 if new_body != original else 0]
        else:
            rep = "" if new_text is None else str(new_text)
            lines_keepends = original.splitlines(keepends=True)
            a0, a1 = ac.abs_span_lines_columns(
                original,
                lines_keepends,
                int(line_start),  # type: ignore[arg-type]
                int(start_column),  # type: ignore[arg-type]
                int(line_end),  # type: ignore[arg-type]
                int(end_column),  # type: ignore[arg-type]
            )
            new_body = original[:a0] + rep + original[a1:]
            counts_per_rule = [1 if new_body != original else 0]

        total_repl = sum(counts_per_rule)

        # 字面替换零匹配：给出诊断提示而非静默返回
        if mode == "literal" and total_repl == 0:
            snippet = original[:300].replace("\n", "\\n")
            hint_lines = [
                f"old_text 在文件中匹配 0 次。",
                f"文件内容前 300 字符: {snippet}",
                "常见原因：① 注意 \\n 是反斜杠+n 两个字面字符还是真换行符",
                "② 首尾空白差异→检查 old_text 是否与 read_file 输出字节完全一致",
                "③ 建议改用 line_start+line_end（行替换，坐标不漂移）",
            ]
            raise ValueError("\n".join(hint_lines))

        if mode == "literal" and expected_replacements is not None and total_repl != int(
            expected_replacements
        ):
            raise ValueError(
                f"替换次数合计 {total_repl} 与 expected_replacements={expected_replacements} 不一致"
            )
        if mode != "literal" and expected_replacements is not None:
            if int(expected_replacements) not in (0, 1) or total_repl != int(expected_replacements):
                raise ValueError(
                    "区间替换下 expected_replacements 只能为 0 或 1，且须与实际是否改写一致"
                )

        dl = list(
            difflib.unified_diff(
                original.splitlines(),
                new_body.splitlines(),
                fromfile=str(fp),
                tofile=str(fp),
                lineterm="",
                n=3,
            )
        )
        diff_text = "\n".join(dl) if dl else ""
        # 计算结构化警告
        warnings: List[str] = []
        if mode in ("linerange",) and new_text:
            lines_kd = original.splitlines(keepends=True)
            ls = int(line_start) - 1
            le = int(line_end) - 1
            w_before = "".join(lines_kd[:max(0, ls)])
            w_after = "".join(lines_kd[min(len(lines_kd), le + 1):])
            warnings = _detect_warnings(
                mode=mode, rep=new_text or "", before=w_before, after=w_after,
                original=original, new_body=new_body,
            )
        else:
            warnings = _detect_warnings(
                mode=mode, rep="", before="", after="",
                original=original, new_body=new_body,
            )

        if dry_run or new_body == original:
            # 生成 confirm_id 供后续免参数提交
            _confirm_id = ""
            if dry_run:
                from agent_v4.live_state import create_confirm_id, has_pending_confirm_for_path, invalidate_confirm_ids_for_path
                if cancel_previous:
                    invalidate_confirm_ids_for_path(str(fp))
                elif has_pending_confirm_for_path(str(fp)):
                    return {"ok": False, "data": None, "error": {"type": "PendingPreviewExists", "message": "该文件已有未提交的预览，请先 review_conclusion(confirm_id=..., cancel_preview=True) 取消，或 review_conclusion(confirm_id=...) 提交后再操作。"}}
                _confirm_id = create_confirm_id("replace_in_file", {
                    "path": str(fp),
                    "old_text": old_text,
                    "new_text": new_text,
                    "rules": rules,
                    "regions": regions,
                    "line_ranges": line_ranges,
                    "region_start": region_start,
                    "region_end": region_end,
                    "line_start": line_start,
                    "line_end": line_end,
                    "start_column": start_column,
                    "end_column": end_column,
                    "replace_all": replace_all,
                }, require_diff_review=True)
            return ac.ok(
                {
                    "path": str(fp),
                    "replace_mode": mode,
                    "replacements": total_repl,
                    "counts_per_rule": counts_per_rule,
                    "rule_count": len(rule_list) if mode == "literal" else len(counts_per_rule),
                    "changed": new_body != original,
                    "dry_run": dry_run,
                    "written": False,
                    "backup_path": None,
                    "mod_id": None,
                    "confirm_id": _confirm_id,
                    "warnings": warnings,
                    "diff_text": diff_text[:16000] + ("…" if len(diff_text) > 16000 else ""),
                    "auto_appended_newline": _appended,
                }
            )

        # 版本化备份
        mod_id: Optional[str] = None
        if backup and fp.is_file():
            mod_id = ac.create_file_backup(
                fp, original, encoding, mode, diff_text
            )
        ac.write_unicode_file(fp, new_body, encoding=encoding)
        from agent_v4.live_state import invalidate_confirm_ids_for_path
        invalidate_confirm_ids_for_path(str(fp))
        return ac.ok(
            {
                "path": str(fp),
                "replace_mode": mode,
                "replacements": total_repl,
                "counts_per_rule": counts_per_rule,
                "rule_count": len(rule_list) if mode == "literal" else len(counts_per_rule),
                "changed": True,
                "dry_run": False,
                "written": True,
                "backup_path": None,
                "mod_id": mod_id,
                "warnings": warnings,
                "diff_text": diff_text[:16000] + ("…" if len(diff_text) > 16000 else ""),
                "auto_appended_newline": _appended,
            }
        )
    except Exception as e:
        return ac.err(e)




