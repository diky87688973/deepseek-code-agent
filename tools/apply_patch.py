# -*- coding: utf-8 -*-
"""将 unified diff 应用到 workspace root 下的文件（仅更新已存在文件，不支持 rename）。"""

from __future__ import annotations

from pathlib import Path

import agent_patch_engine as pe
import agent_common as ac


def agent_main(
    *,
    path: str,
    patch_text: str | None = None,
    patch_file: str | None = None,
    dry_run: bool = True,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    try:
        rt = str(run_type or "").strip().lower()
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}

        if (patch_text is None) == (patch_file is None):
            raise ValueError("patch_text 与 patch_file 必须且只能提供一个")

        r = ac.resolve_path(path, allow_outside_workspace=not restrict_to_workspace)
        if not r.is_dir():
            raise ValueError(f"path 必须是目录: {r}")

        raw = pe.load_patch_text(patch_text=patch_text, patch_file=patch_file)
        file_patches = pe.parse_unified_diff(raw)
        changed: list[str] = []

        for fp in file_patches:
            if fp["old_path"] != fp["new_path"]:
                raise ValueError("当前版本不支持 rename，请保持 ---/+++ 路径一致")
            rel = Path(fp["new_path"])
            abs_path = (r / rel).resolve()
            try:
                abs_path.relative_to(r)
            except ValueError:
                raise ValueError(f"越界路径: {abs_path}")
            if not abs_path.is_file():
                raise FileNotFoundError(f"仅支持更新已存在文件: {abs_path}")
            new_content = pe.apply_file_patch(abs_path, fp["hunks"])
            if not dry_run:
                ac.write_unicode_file(abs_path, new_content, encoding="utf-8")
            changed.append(str(abs_path))

        return ac.ok(
            {
                "path": str(r),
                "dry_run": dry_run,
                "changed_files": changed,
                "diff_text": raw[:16000] + ("…" if len(raw) > 16000 else ""),
            }
        )
    except Exception as e:
        return ac.err(e)


def main() -> None:
    import argparse
    import json

    p = argparse.ArgumentParser(description="apply_patch")
    p.set_defaults(dry_run=True)
    p.add_argument("--path", required=True)
    p.add_argument("--patch_text", default=None)
    p.add_argument("--patch_file", default=None)
    p.add_argument("--dry_run", dest="dry_run", action="store_true")
    p.add_argument("--commit", dest="dry_run", action="store_false")
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
        patch_text=args.patch_text,
        patch_file=args.patch_file,
        dry_run=bool(args.dry_run),
        restrict_to_workspace=bool(args.restrict_to_workspace),
        run_type=str(args.run_type or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
