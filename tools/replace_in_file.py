# -*- coding: utf-8 -*-
"""单文件替换：字面量规则，或按 region/regions 字符区间 / 行列矩形覆盖写。"""

from __future__ import annotations

import json
from pathlib import Path

import difflib

import agent_common as ac


def _merge_literal_rules(
    old_text: str | None,
    new_text: str | None,
    rules: list | None,
) -> list[tuple[str, str]]:
    has_pair = old_text is not None or new_text is not None
    out: list[tuple[str, str]] = []
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


def _apply_rules_sequential(
    original: str,
    rules: list[tuple[str, str]],
    *,
    replace_all: bool,
) -> tuple[str, list[int]]:
    cur = original
    counts: list[int] = []
    for old_s, new_s in rules:
        if replace_all:
            c = cur.count(old_s)
            if c:
                cur = cur.replace(old_s, new_s)
        else:
            c = 1 if old_s in cur else 0
            cur = cur.replace(old_s, new_s, 1) if c else cur
        counts.append(c)
    return cur, counts


def _detect_replace_modes(
    *,
    old_text: str | None,
    new_text: str | None,
    rules: list | None,
    regions: list | None,
    line_ranges: list | None,
    region_start: int | None,
    region_end: int | None,
    line_start: int | None,
    line_end: int | None,
    start_column: int | None,
    end_column: int | None,
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
    path: str,
    old_text: str | None = None,
    new_text: str | None = None,
    rules: list | None = None,
    regions: list | None = None,
    line_ranges: list | None = None,
    region_start: int | None = None,
    region_end: int | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    start_column: int | None = None,
    end_column: int | None = None,
    dry_run: bool = True,
    replace_all: bool = True,
    expected_replacements: int | None = None,
    encoding: str = "utf-8",
    backup: bool = False,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    try:
        if rules is not None and isinstance(rules, str):
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "ValueError",
                    "message": "rules 须为 list[dict]，禁止传入 JSON 字符串；由宿主解析或仅用 CLI --rules_file。",
                },
            }
        rt = str(run_type or "").strip().lower()
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}

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
        counts_per_rule: list[int]
        rule_list: list[tuple[str, str]] = []

        if mode == "literal":
            rule_list = _merge_literal_rules(old_text, new_text, rules)
            new_body, counts_per_rule = _apply_rules_sequential(
                original, rule_list, replace_all=replace_all
            )
        elif mode == "regions":
            # 多区间模式：按 regionStart 降序排序，避免坐标漂移
            validated = []
            for i, item in enumerate(regions or []):
                if not isinstance(item, dict):
                    raise ValueError(f"regions[{i}] 必须是对象")
                rs = item.get("regionStart")
                re_ = item.get("regionEnd")
                nt = item.get("newText", "")
                if not isinstance(rs, int) or not isinstance(re_, int):
                    raise ValueError(f"regions[{i}] 须含整数 regionStart、regionEnd")
                if not isinstance(nt, str):
                    raise ValueError(f"regions[{i}] 的 newText 须为字符串")
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
                ls = item.get("lineStart")
                le = item.get("lineEnd")
                nt = item.get("newText", "")
                if not isinstance(ls, int) or not isinstance(le, int):
                    raise ValueError(f"line_ranges[{i}] 须含整数 lineStart、lineEnd")
                if not isinstance(nt, str):
                    raise ValueError(f"line_ranges[{i}] 的 newText 须为字符串")
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
                if s < 0 or e >= total or s > e:
                    raise ValueError(f"行号越界：start={ls}, end={le}, 共{total}行")
                before = "".join(lines_kd[:s])
                after = "".join(lines_kd[e + 1:])
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
            if ls < 0 or le >= total or ls > le:
                raise ValueError(f"行号越界：start={line_start}, end={line_end}, 共{total}行")
            before = "".join(lines_keepends[:ls])
            after = "".join(lines_keepends[le + 1:])
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

        if dry_run or new_body == original:
            return ac.ok(
                {
                    "path": str(fp),
                    "replaceMode": mode,
                    "replacements": total_repl,
                    "countsPerRule": counts_per_rule,
                    "ruleCount": len(rule_list) if mode == "literal" else len(counts_per_rule),
                    "changed": new_body != original,
                    "dryRun": dry_run,
                    "written": False,
                    "backupPath": None,
                    "diffText": diff_text[:16000] + ("…" if len(diff_text) > 16000 else ""),
                }
            )

        bak_path_str: str | None = None
        if backup and fp.is_file():
            bak = fp.with_suffix(fp.suffix + ".bak")
            ac.write_unicode_file(bak, original, encoding=encoding)
            bak_path_str = str(bak)

        ac.write_unicode_file(fp, new_body, encoding=encoding)
        return ac.ok(
            {
                "path": str(fp),
                "replaceMode": mode,
                "replacements": total_repl,
                "countsPerRule": counts_per_rule,
                "ruleCount": len(rule_list) if mode == "literal" else len(counts_per_rule),
                "changed": True,
                "dryRun": False,
                "written": True,
                "backupPath": bak_path_str,
                "diffText": diff_text[:16000] + ("…" if len(diff_text) > 16000 else ""),
            }
        )
    except Exception as e:
        return ac.err(e)


def main() -> None:
    import argparse
    import json

    p = argparse.ArgumentParser(description="replace_in_file：字面/区间/多区间替换")
    p.add_argument("--path", required=True)
    p.add_argument("--old_text", default=None)
    p.add_argument("--new_text", default=None)
    p.add_argument("--rules_file", default=None, help="JSON 数组：[{old_text,new_text}, ...]")
    p.add_argument("--regions_file", default=None, help="JSON 数组：[{regionStart,regionEnd,newText}, ...]，工具自动降序处理、检测重叠")
    p.add_argument("--line_ranges_file", default=None, help="JSON 数组：[{lineStart,lineEnd,newText}, ...]，工具自动行号降序处理、检测重叠")
    p.add_argument("--region_start", type=int, default=None)
    p.add_argument("--region_end", type=int, default=None)
    p.add_argument("--line_start", type=int, default=None)
    p.add_argument("--line_end", type=int, default=None)
    p.add_argument("--start_column", type=int, default=None)
    p.add_argument("--end_column", type=int, default=None)
    p.add_argument("--dryRun", action="store_true", default=True)
    p.add_argument("--commit", action="store_false", dest="dryRun")
    p.add_argument("--replaceAll", action="store_true", default=True)
    p.add_argument("--single", action="store_false", dest="replaceAll")
    p.add_argument("--expectedReplacements", type=int, default=None)
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--backup", action="store_true")
    p.add_argument(
        "--restrictToWorkspace",
        action="store_true",
        help="将 path 限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--runType", default="")
    p.add_argument("--jsonOut", action="store_true")
    args = p.parse_args()

    rules: list | None = None
    if args.rules_file:
        raw = Path(str(args.rules_file).strip()).expanduser().read_text(encoding="utf-8", errors="strict")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise SystemExit("rules_file JSON 顶层须为数组")
        rules = data  # type: ignore[assignment]

    regions: list | None = None
    if args.regions_file:
        raw = Path(str(args.regions_file).strip()).expanduser().read_text(encoding="utf-8", errors="strict")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise SystemExit("regions_file JSON 顶层须为数组")
        regions = data

    line_ranges: list | None = None
    if args.line_ranges_file:
        raw = Path(str(args.line_ranges_file).strip()).expanduser().read_text(encoding="utf-8", errors="strict")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise SystemExit("line_ranges_file JSON 顶层须为数组")
        line_ranges = data

    r = agent_main(
        path=args.path,
        old_text=args.old_text,
        new_text=args.new_text,
        rules=rules,
        regions=regions,
        line_ranges=line_ranges,
        region_start=args.region_start,
        region_end=args.region_end,
        line_start=args.line_start,
        line_end=args.line_end,
        start_column=args.start_column,
        end_column=args.end_column,
        dry_run=args.dryRun,
        replace_all=args.replaceAll,
        expected_replacements=args.expectedReplacements,
        encoding=args.encoding,
        backup=bool(args.backup),
        restrict_to_workspace=bool(getattr(args, "restrictToWorkspace", False)),
        run_type=str(args.runType or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
