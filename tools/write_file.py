# -*- coding: utf-8 -*-
"""写入或覆盖文本文件。默认 dry_run 不落盘。"""

from __future__ import annotations

import difflib

import agent_common as ac


def agent_main(
    *,
    path: str,
    content: str,
    encoding: str = "utf-8",
    dry_run: bool = True,
    create_only: bool = False,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    """dry_run=True 时只返回元信息不落盘。run_type=plan 时禁止实际写盘。"""
    try:
        rt = str(run_type or "").strip().lower()
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}

        fp = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        existed = fp.is_file()
        if create_only and existed:
            raise FileExistsError(f"create_only：文件已存在 {fp}")

        original = ac.read_file_text(fp, encoding) if existed else ""
        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(),
                content.splitlines(),
                fromfile=str(fp) if existed else "/dev/null",
                tofile=str(fp),
                lineterm="",
                n=3,
            )
        )
        diff_text = "\n".join(diff_lines) if diff_lines else ""
        preview = {
            "path": str(fp),
            "encoding": encoding,
            "byte_length_approx": len(content.encode(encoding, errors="replace")),
            "existed_before": existed,
            "dry_run": dry_run,
            "changed": content != original,
            "diff_text": diff_text[:16000] + ("…" if len(diff_text) > 16000 else ""),
        }
        if dry_run:
            return ac.ok({**preview, "written": False})

        ac.write_unicode_file(fp, content, encoding=encoding)
        return ac.ok({**preview, "written": True})
    except Exception as e:
        return ac.err(e)


def main() -> None:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="write_file")
    p.set_defaults(dry_run=True)
    p.add_argument("--path", required=True)
    p.add_argument("--content", default="")
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--dry_run", dest="dry_run", action="store_true", help="仅预览（默认）")
    p.add_argument("--commit", dest="dry_run", action="store_false", help="真正写盘（关闭 dry_run 预览）")
    p.add_argument("--create_only", action="store_true")
    p.add_argument(
        "--restrict_to_workspace",
        action="store_true",
        help="将 path 限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--run_type", default="")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(
        path=args.path,
        content=args.content,
        encoding=args.encoding,
        dry_run=bool(args.dry_run),
        create_only=bool(args.create_only),
        restrict_to_workspace=bool(args.restrict_to_workspace),
        run_type=str(args.run_type or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
