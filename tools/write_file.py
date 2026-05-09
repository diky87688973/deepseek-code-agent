# -*- coding: utf-8 -*-
"""写入或覆盖文本文件。默认 dry_run 不落盘。"""

from __future__ import annotations

import agent_common as ac


def agent_main(
    *,
    path: str,
    content: str,
    encoding: str = "utf-8",
    dry_run: bool = True,
    create_only: bool = False,
    allow_outside_workspace: bool = False,
    run_type: str = "",
) -> dict:
    """dry_run=True 时只返回元信息不落盘。run_type=plan 时禁止实际写盘。"""
    try:
        rt = str(run_type or "").strip().lower()
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}

        fp = ac.resolve_path(path, allow_outside_workspace=allow_outside_workspace)
        existed = fp.is_file()
        if create_only and existed:
            raise FileExistsError(f"create_only：文件已存在 {fp}")

        parents_ok = str(fp.parent)
        preview = {
            "path": str(fp),
            "encoding": encoding,
            "byteLengthApprox": len(content.encode(encoding, errors="replace")),
            "existedBefore": existed,
            "dryRun": dry_run,
        }
        if dry_run:
            return ac.ok({**preview, "written": False})

        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding=encoding, newline="\n")
        return ac.ok({**preview, "written": True})
    except Exception as e:
        return ac.err(e)


def main() -> None:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="write_file")
    p.add_argument("--path", required=True)
    p.add_argument("--content", default="")
    p.add_argument("--encoding", default="utf-8")
    p.add_argument("--dryRun", action="store_true", default=True)
    p.add_argument("--commit", action="store_false", dest="dryRun", help="真正写盘（关闭 dryRun）")
    p.add_argument("--createOnly", action="store_true")
    p.add_argument("--allowOutsideWorkspace", action="store_true")
    p.add_argument("--runType", default="")
    p.add_argument("--jsonOut", action="store_true")
    args = p.parse_args()
    r = agent_main(
        path=args.path,
        content=args.content,
        encoding=args.encoding,
        dry_run=args.dryRun,
        create_only=args.createOnly,
        allow_outside_workspace=args.allowOutsideWorkspace,
        run_type=str(args.runType or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
