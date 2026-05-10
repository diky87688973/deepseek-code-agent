# -*- coding: utf-8 -*-
"""压缩包 list / extract / create。扁平参数；路径经 agent_common；Plan 模式下禁止 extract/create。"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

import agent_common as ac

try:
    import rarfile

    _HAS_RARFILE = True
except ImportError:
    rarfile = None
    _HAS_RARFILE = False


def _detect_format(source: Path) -> str:
    name = source.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "tar.gz"
    if name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return "tar.bz2"
    if name.endswith(".tar.xz") or name.endswith(".txz"):
        return "tar.xz"
    if name.endswith(".tar"):
        return "tar"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".rar"):
        return "rar"
    try:
        with open(source, "rb") as f:
            magic = f.read(4)
        if magic[:2] == b"PK":
            return "zip"
        if magic[:4] == b"Rar!":
            return "rar"
        if magic[:2] == b"\x1f\x8b":
            return "tar.gz"
        if magic[:3] == b"BZh":
            return "tar.bz2"
        if magic[:6] == b"\xfd7zXZ":
            return "tar.xz"
        if magic[:5] == b"ustar":
            return "tar"
    except OSError:
        pass
    return "unknown"


def _open_archive(source: Path, password: str | None = None):
    fmt = _detect_format(source)
    if not source.exists():
        raise FileNotFoundError(f"文件不存在: {source}")

    if fmt == "zip":
        arc = zipfile.ZipFile(source, "r")
        if password:
            arc.setpassword(password.encode("utf-8"))
        return arc, "zip", arc.close

    if fmt in ("tar.gz", "tar.bz2", "tar.xz", "tar"):
        mode_map = {"tar": "r", "tar.gz": "r:gz", "tar.bz2": "r:bz2", "tar.xz": "r:xz"}
        arc = tarfile.open(source, mode_map.get(fmt, "r"))
        return arc, fmt, arc.close

    if fmt == "rar":
        if not _HAS_RARFILE:
            raise ImportError("处理 .rar 需要安装 rarfile：pip install rarfile")
        arc = rarfile.RarFile(source)
        if password:
            arc.setpassword(password)
        return arc, "rar", arc.close

    raise ValueError(f"无法识别的压缩格式: {source.name}（支持 ZIP / tar / tar.gz / tar.bz2 / tar.xz / rar）")


def _list_archive(source: Path, password: str | None, glob_pattern: str | None) -> dict:
    arc, fmt, close_fn = _open_archive(source, password)
    try:
        if fmt in ("zip", "rar"):
            entries = []
            for info in arc.infolist():
                name = info.filename
                if glob_pattern and not fnmatch.fnmatch(name, glob_pattern):
                    continue
                entries.append(
                    {
                        "name": name,
                        "size": info.file_size,
                        "compressed_size": getattr(info, "compress_size", None),
                        "modified": datetime(*info.date_time).isoformat() if hasattr(info, "date_time") else None,
                        "is_dir": info.is_dir() if hasattr(info, "is_dir") else name.endswith("/"),
                    }
                )
        else:
            entries = []
            for member in arc.getmembers():
                if glob_pattern and not fnmatch.fnmatch(member.name, glob_pattern):
                    continue
                entries.append(
                    {
                        "name": member.name,
                        "size": member.size,
                        "compressed_size": None,
                        "modified": datetime.fromtimestamp(member.mtime).isoformat() if member.mtime else None,
                        "is_dir": member.isdir(),
                    }
                )
        return {
            "action": "list",
            "source": str(source),
            "format": fmt,
            "count": len(entries),
            "entries": entries,
        }
    finally:
        close_fn()


def _extract_archive(source: Path, dest: Path, password: str | None, glob_pattern: str | None) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    arc, fmt, close_fn = _open_archive(source, password)
    try:
        if fmt == "zip":
            members = arc.infolist()
            if glob_pattern:
                members = [m for m in members if fnmatch.fnmatch(m.filename, glob_pattern)]
            for m in members:
                arc.extract(m, path=dest)
            count = len(members)
        elif fmt == "rar":
            members = arc.infolist()
            if glob_pattern:
                members = [m for m in members if fnmatch.fnmatch(m.filename, glob_pattern)]
            arc.extractall(dest, members=members)
            count = len(members)
        else:
            members = arc.getmembers()
            if glob_pattern:
                members = [m for m in members if fnmatch.fnmatch(m.name, glob_pattern)]
            arc.extractall(dest, members=members)
            count = len(members)
        return {
            "action": "extract",
            "source": str(source),
            "dest": str(dest),
            "format": fmt,
            "extracted_count": count,
        }
    finally:
        close_fn()


def _add_to_zip(zf: zipfile.ZipFile, root: Path, current: Path, glob_pattern: str | None) -> int:
    count = 0
    if current.is_file():
        if glob_pattern is None or fnmatch.fnmatch(current.name, glob_pattern):
            name = str(current.relative_to(root.parent)) if root.is_dir() else root.name
            zf.write(current, name)
            count += 1
    else:
        for entry in sorted(current.iterdir()):
            if entry.is_file():
                if glob_pattern is None or fnmatch.fnmatch(entry.name, glob_pattern):
                    arcname = str(entry.relative_to(root.parent))
                    zf.write(entry, arcname)
                    count += 1
            elif entry.is_dir():
                count += _add_to_zip(zf, root, entry, glob_pattern)
    return count


def _add_to_tar(tf: tarfile.TarFile, root: Path, current: Path, glob_pattern: str | None) -> int:
    count = 0
    if current.is_file():
        if glob_pattern is None or fnmatch.fnmatch(current.name, glob_pattern):
            name = str(current.relative_to(root.parent)) if root.is_dir() else root.name
            tf.add(current, name)
            count += 1
    else:
        for entry in sorted(current.iterdir()):
            if entry.is_file():
                if glob_pattern is None or fnmatch.fnmatch(entry.name, glob_pattern):
                    arcname = str(entry.relative_to(root.parent))
                    tf.add(entry, arcname)
                    count += 1
            elif entry.is_dir():
                count += _add_to_tar(tf, root, entry, glob_pattern)
    return count


def _create_archive(source: Path, dest: Path, fmt: str, glob_pattern: str | None) -> dict:
    if not source.exists():
        raise FileNotFoundError(f"源不存在: {source}")

    dest.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "zip":
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            count = _add_to_zip(zf, source, source, glob_pattern)
    elif fmt == "tar.gz":
        with tarfile.open(dest, "w:gz") as tf:
            count = _add_to_tar(tf, source, source, glob_pattern)
    elif fmt == "tar.bz2":
        with tarfile.open(dest, "w:bz2") as tf:
            count = _add_to_tar(tf, source, source, glob_pattern)
    elif fmt == "tar.xz":
        with tarfile.open(dest, "w:xz") as tf:
            count = _add_to_tar(tf, source, source, glob_pattern)
    elif fmt == "tar":
        with tarfile.open(dest, "w") as tf:
            count = _add_to_tar(tf, source, source, glob_pattern)
    else:
        raise ValueError(f"不支持的输出格式: {fmt}（支持 zip / tar.gz / tar.bz2 / tar.xz / tar）")

    return {
        "action": "create",
        "source": str(source),
        "dest": str(dest),
        "format": fmt,
        "added_count": count,
    }


def agent_main(
    *,
    action: str,
    source: str,
    dest: str | None = None,
    output_format: str | None = None,
    password: str | None = None,
    glob_pattern: str | None = None,
    restrict_to_workspace: bool = False,
    run_type: str = "",
) -> dict:
    act = str(action or "").strip().lower()
    rt = str(run_type or "").strip().lower()
    write_actions = frozenset({"extract", "create"})
    if act in write_actions and rt == "plan":
        return {
            "ok": False,
            "data": None,
            "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，不允许 extract/create。"},
        }

    try:
        src = ac.resolve_path(source, allow_outside_workspace=not restrict_to_workspace)
        if act == "list":
            data = _list_archive(src, password, glob_pattern)
            return ac.ok(data)
        if act == "extract":
            if not dest:
                return ac.err(ValueError("extract 需要 dest"))
            dst = ac.resolve_path(dest, allow_outside_workspace=not restrict_to_workspace)
            data = _extract_archive(src, dst, password, glob_pattern)
            return ac.ok(data)
        if act == "create":
            if not dest:
                return ac.err(ValueError("create 需要 dest"))
            dst = ac.resolve_path(dest, allow_outside_workspace=not restrict_to_workspace)
            fmt = (output_format or "").strip() or None
            if fmt:
                out_fmt = fmt
            else:
                out_fmt = _detect_format(dst)
                if out_fmt == "unknown":
                    out_fmt = "zip"
            data = _create_archive(src, dst, out_fmt, glob_pattern)
            return ac.ok(data)
        return ac.err(ValueError(f"未知 action: {act}"))
    except Exception as e:
        return ac.err(e)


def main() -> None:
    p = argparse.ArgumentParser(description="archive（调试；与 agent_main 同参）")
    p.add_argument("--action", required=True, choices=["list", "extract", "create"])
    p.add_argument("--source", required=True)
    p.add_argument("--dest", default=None)
    p.add_argument("--output_format", default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--glob_pattern", default=None)
    p.add_argument(
        "--restrict_to_workspace",
        action="store_true",
        help="源/目标路径限定在 WORKSPACE_DIR 内（默认不限制）。",
    )
    p.add_argument("--run_type", default="")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(
        action=args.action,
        source=args.source,
        dest=args.dest,
        output_format=args.output_format,
        password=args.password,
        glob_pattern=args.glob_pattern,
        restrict_to_workspace=bool(args.restrict_to_workspace),
        run_type=str(args.run_type or ""),
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok"):
            print(json.dumps(r.get("data"), ensure_ascii=False))
        else:
            print((r.get("error") or {}).get("message", ""), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
