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
    allow_outside_workspace: bool = False,
    run_type: str = "",
) -> dict:
    try:
        rt = str(run_type or "").strip().lower()
        want_write = not dry_run
        if want_write and rt == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许写文件"}}

        if (patch_text is None) == (patch_file is None):
            raise ValueError("patch_text 与 patch_file 必须且只能提供一个")

        r = ac.resolve_path(path, allow_outside_workspace=allow_outside_workspace)
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
            if not str(abs_path).startswith(str(r)):
                raise ValueError(f"越界路径: {abs_path}")
            if not abs_path.is_file():
                raise FileNotFoundError(f"仅支持更新已存在文件: {abs_path}")
            new_content = pe.apply_file_patch(abs_path, fp["hunks"])
            if not dry_run:
                abs_path.write_text(new_content, encoding="utf-8")
            changed.append(str(abs_path))

        return ac.ok({"path": str(r), "dryRun": dry_run, "changedFiles": changed})
    except Exception as e:
        return ac.err(e)


def main() -> None:
    import argparse
    import json

    p = argparse.ArgumentParser(description="apply_patch")
    p.add_argument("--path", required=True)
    p.add_argument("--patchText", default=None)
    p.add_argument("--patchFile", default=None)
    p.add_argument("--dryRun", action="store_true", default=True)
    p.add_argument("--commit", action="store_false", dest="dryRun")
    p.add_argument("--allowOutsideWorkspace", action="store_true")
    p.add_argument("--runType", default="")
    p.add_argument("--jsonOut", action="store_true")
    args = p.parse_args()
    r = agent_main(
        path=args.path,
        patch_text=args.patchText,
        patch_file=args.patchFile,
        dry_run=args.dryRun,
        allow_outside_workspace=args.allowOutsideWorkspace,
        run_type=str(args.runType or ""),
    )
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
