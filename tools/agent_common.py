# -*- coding: utf-8 -*-
"""Agent 工作区工具共用：路径沙箱、编码读取、统一返回信封。"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import random
import re
import string
import time
from pathlib import Path
from typing import List, Optional, Set, Tuple, Union

from util.config_loader import load_config

_AGENT_CONFIG = load_config(verbose=False)

# ── 版本化备份配置 ──
_FILE_BACKUP_ROOT: Optional[Path] = None


def configure_file_backup_root(root: Path) -> None:
    global _FILE_BACKUP_ROOT
    _FILE_BACKUP_ROOT = root


def _ensure_backup_root() -> Path:
    if _FILE_BACKUP_ROOT is None:
        raise RuntimeError("file_backup_root 未配置（请在 bootstrap 中调用 configure_file_backup_root）")
    _FILE_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    return _FILE_BACKUP_ROOT


def ok(data: Optional[dict]) -> dict:
    return {"ok": True, "data": data, "error": None}


def err(exc: Exception) -> dict:
    return {"ok": False, "data": None, "error": {"type": exc.__class__.__name__, "message": str(exc)}}


def _strip_outer_quotes(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].strip()
    return s


def workspace_root() -> Path:
    w = _strip_outer_quotes(str(_AGENT_CONFIG.get("AGENT_WORKSPACE_DIR") or ""))
    if w:
        return Path(w).expanduser().resolve()
    return Path.cwd().resolve()


def resolve_path(
    raw: Union[str, Path],
    *,
    allow_outside_workspace: bool = True,
    workspace: Optional[Path] = None,
) -> Path:
    """将用户传入路径解析为绝对路径；allow_outside_workspace=False 时将路径限定在 WORKSPACE_DIR 内。"""

    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        root = workspace or workspace_root()
        p = (root / p).resolve()
    else:
        p = p.resolve()
    root = workspace or workspace_root()
    if not allow_outside_workspace:
        try:
            p.relative_to(root)
        except ValueError:
            raise PermissionError(f"路径越出工作区限制: {p}（工作区根: {root}）") from None
    return p


def write_unicode_file(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """落盘文本，换行统一为 \\n。避免 pathlib.Path.write_text(..., newline=) 在 Python 3.10 以下不可用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="\n") as f:
        f.write(content)


def read_file_text(path: Path, encoding: str) -> str:
    enc = (encoding or "utf-8").strip().lower()
    if enc in ("auto", ""):
        for candidate in ("utf-8", "gb18030", "gbk"):
            try:
                return path.read_text(encoding=candidate)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="replace")
    return path.read_text(encoding=encoding, errors="replace")


def parse_tool_bool(v: object, default: bool = True) -> bool:
    """工具参数布尔解析：兼容 true/false、1/0、是/否 等。"""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("0", "false", "no", "off", "否", "不"):
        return False
    if s in ("1", "true", "yes", "on", "是"):
        return True
    return default


def utf8_preview(text: str, max_bytes: int = 200) -> str:
    """按 UTF-8 字节数截断预览，不在多字节字符中间切断。"""
    raw = str(text or "").encode("utf-8")
    if len(raw) <= max_bytes:
        return str(text or "")
    chunk = raw[:max_bytes]
    while chunk:
        try:
            return chunk.decode("utf-8")
        except UnicodeDecodeError:
            chunk = chunk[:-1]
    return ""


def rglob_files(root: Path, glob_pat: str, *, recursive: bool) -> List[Path]:
    pat = glob_pat or "*"
    if not root.is_dir():
        return [root] if root.is_file() else []
    it = root.rglob(pat) if recursive else root.glob(pat)
    return sorted(x for x in it if x.is_file())


# 目录内「内容搜索」省略 glob_pattern 时使用：常见以纯文本/源码形式编辑的扩展名，及少量无扩展名工程文件名。
# 非文本类文件一律不纳入默认集合（含各类压缩包、图片、音视频、办公二进制、可执行体、字体、大型 blob 等）；
# 若确需对任意扩展名/二进制做内容检索，请显式传 glob_pattern=\"*\"。
TEXT_SEARCH_SOURCE_GLOBS: Tuple[str, ...] = (
    "*.py",
    "*.pyi",
    "*.pyw",
    "*.js",
    "*.mjs",
    "*.cjs",
    "*.jsx",
    "*.ts",
    "*.tsx",
    "*.json",
    "*.jsonc",
    "*.md",
    "*.rst",
    "*.adoc",
    "*.yml",
    "*.yaml",
    "*.toml",
    "*.ini",
    "*.cfg",
    "*.conf",
    "*.txt",
    "*.html",
    "*.htm",
    "*.xhtml",
    "*.css",
    "*.scss",
    "*.sass",
    "*.less",
    "*.vue",
    "*.svelte",
    "*.java",
    "*.kt",
    "*.kts",
    "*.cs",
    "*.fs",
    "*.go",
    "*.rs",
    "*.php",
    "*.rb",
    "*.sql",
    "*.sh",
    "*.bash",
    "*.zsh",
    "*.fish",
    "*.ps1",
    "*.psm1",
    "*.bat",
    "*.cmd",
    "*.xml",
    "*.svg",
    "*.cmake",
    "*.h",
    "*.hpp",
    "*.hh",
    "*.c",
    "*.cc",
    "*.cpp",
    "*.cxx",
    "*.swift",
    "*.scala",
    "*.clj",
    "*.cljs",
    "*.edn",
    "*.ex",
    "*.exs",
    "*.erl",
    "*.hrl",
    "*.r",
    "*.pl",
    "*.pm",
    "Dockerfile",
    "Makefile",
    "GNUmakefile",
    "CMakeLists.txt",
    "LICENSE",
    "README",
    "Rakefile",
    "Gemfile",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".env.example",
)


def iter_source_files(root: Path, glob_pattern: Optional[str], *, recursive: bool):
    """遍历 path 下待扫文件。glob_pattern 为空/None → TEXT_SEARCH_SOURCE_GLOBS；\"*\" 或 \"**/*\" → 全部文件。"""
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    gp_raw = glob_pattern if glob_pattern is not None else ""
    gp = str(gp_raw).strip()
    if gp in ("*", "**/*"):
        it = root.rglob("*") if recursive else root.iterdir()
        for p in it:
            if p.is_file():
                yield p
        return
    if not gp:
        seen: Set[Path] = set()
        for pat in TEXT_SEARCH_SOURCE_GLOBS:
            try:
                it = root.rglob(pat) if recursive else root.glob(pat)
            except (OSError, ValueError):
                continue
            for p in it:
                if not p.is_file():
                    continue
                try:
                    k = p.resolve()
                except OSError:
                    k = p
                if k in seen:
                    continue
                seen.add(k)
                yield p
        return
    try:
        it = root.rglob(gp) if recursive else root.glob(gp)
    except (OSError, ValueError):
        return
    for p in it:
        if p.is_file():
            yield p


def progress_abort_requested(progress_dict: Optional[dict]) -> bool:
    """宿主在 _progress_dict 上置 _abort=true 时，工具内循环应尽快退出。"""
    if not isinstance(progress_dict, dict):
        return False
    return bool(progress_dict.get("_abort"))


def collect_source_files(root: Path, glob_pattern: Optional[str], *, recursive: bool) -> List[Path]:
    """单文件返回 [root]；目录返回按路径排序的文件列表（glob 语义同 iter_source_files）。"""
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    return sorted(iter_source_files(root, glob_pattern, recursive=recursive), key=lambda x: str(x).lower())


def filter_by_gitignore(paths: List[Path], repo_root: Optional[Path]) -> List[Path]:
    if repo_root is None:
        return paths
    git_dir = repo_root / ".git"
    if not git_dir.is_dir():
        return paths
    try:
        import subprocess

        spec: Set[str] = set()
        for p in paths:
            try:
                rel = p.resolve().relative_to(repo_root.resolve())
            except ValueError:
                continue
            cp = subprocess.run(
                ["git", "check-ignore", "-q", str(rel).replace("\\", "/")],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if cp.returncode == 0:
                spec.add(str(p.resolve()))
        return [p for p in paths if str(p.resolve()) not in spec]
    except Exception:
        return paths


def compile_pattern(pattern: str, *, regex: bool, ignore_case: bool) -> Tuple[Union[re.Pattern[str], str], bool]:
    if regex:
        flags = re.MULTILINE
        if ignore_case:
            flags |= re.IGNORECASE
        return re.compile(pattern, flags), True
    if ignore_case:
        return pattern.lower(), False
    return pattern, False


def line_matches(line: str, pat: Union[re.Pattern[str], str], is_regex: bool, ignore_case: bool) -> bool:
    if is_regex:
        assert isinstance(pat, re.Pattern)
        return pat.search(line) is not None
    p = pat
    if ignore_case:
        return str(p) in line.lower()
    return str(p) in line


def resolve_end_column_open(end_value: int, line_content_len: int) -> int:
    """end_column 为 1-based 开区间右界；负数从行末向前数（与 read_file / replace_in_file 行列矩形一致）。"""
    if end_value >= 0:
        return end_value
    return line_content_len + end_value + 1


def line_meta_keepends(lines_keepends: List[str]) -> Tuple[List[int], List[int]]:
    starts: List[int] = []
    content_lens: List[int] = []
    cur = 0
    for ln in lines_keepends:
        starts.append(cur)
        body = ln.rstrip("\r\n")
        content_lens.append(len(body))
        cur += len(ln)
    return starts, content_lens


def text_slice_by_lines(lines_keepends: List[str], start_line: int, end_line: int) -> str:
    total = len(lines_keepends)
    if start_line < 1 or start_line > total:
        return ""
    if end_line > total:
        end_line = total
    if start_line > end_line:
        start_line = end_line
    return "".join(lines_keepends[start_line - 1 : end_line])


def abs_span_lines_columns(
    full_text: str,
    lines_keepends: List[str],
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
) -> Tuple[int, int]:
    """行列矩形区间 → 全文半开区间 [abs_start, abs_end)。列号 1-based，end_column 为开区间。"""
    total = len(lines_keepends)
    if start_line < 1:
        return (0, len(full_text))
    if end_line > total:
        end_line = total
    if start_line > total or end_line < 1 or start_line > end_line:
        return (0, 0)
    starts, content_lens = line_meta_keepends(lines_keepends)
    sl = start_line - 1
    el = end_line - 1
    slen = content_lens[sl]
    elen = content_lens[el]
    if start_column < 1:
        raise ValueError("start_column 必须 >= 1")
    start_col_py = start_column - 1
    if start_col_py > slen:
        raise ValueError(
            f"start_column 越界: line={start_line}, start_column={start_column}, line_len={slen}"
        )
    end_col_open_1based = resolve_end_column_open(end_column, elen)
    end_col_py = end_col_open_1based - 1
    if end_col_py < 0 or end_col_py > elen:
        raise ValueError(
            f"end_column 越界: line={end_line}, end_column={end_column}, "
            f"resolved_end_open={end_col_open_1based}, line_len={elen}"
        )
    abs_start = starts[sl] + start_col_py
    abs_end = starts[el] + end_col_py
    if abs_end < abs_start:
        raise ValueError("区间无效：结束位置早于起始位置")
    return abs_start, abs_end


def text_slice_lines_columns(
    full_text: str,
    lines_keepends: List[str],
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
) -> str:
    a0, a1 = abs_span_lines_columns(
        full_text, lines_keepends, start_line, start_column, end_line, end_column
    )
    return full_text[a0:a1]


def text_slice_offsets(full_text: str, start_idx: int, end_idx: int) -> Tuple[str, int, int]:
    """按字符下标切片；end 为非负时半开；为负时从文末倒推（与 read_file 字符区间一致）。"""
    n = len(full_text)
    if start_idx < 0:
        raise ValueError("char_start 必须 >= 0")
    start_py = n if start_idx > n else start_idx
    if end_idx < 0:
        end_py = n + end_idx + 1
    else:
        end_py = end_idx
    end_py = max(0, min(end_py, n))
    if end_py < start_py:
        raise ValueError("区间无效：结束早于起始")
    return full_text[start_py:end_py], start_py, end_py


def apply_range_replace(text: str, replacement: str, start_idx: int, end_idx: int) -> str:
    """将半开区间 [start,end) 替换为 replacement；end 规则同 text_slice_offsets。"""
    n = len(text)
    if start_idx < 0:
        raise ValueError("region_start 必须 >= 0")
    start_py = n if start_idx > n else start_idx
    if end_idx < 0:
        end_py = n + end_idx + 1
    else:
        end_py = end_idx
    end_py = max(0, min(end_py, n))
    if end_py < start_py:
        raise ValueError("region 非法：结束早于起始")
    if start_py > n:
        raise ValueError(f"region_start 越界: {start_idx}, len={n}")
    return text[:start_py] + replacement + text[end_py:]


def offset_to_line_column_onebased(lines_keepends: List[str], offset: int) -> Tuple[int, int]:
    """0-based 全文偏移 → (1-based 行, 1-based 列)，列按行 body（无 \\r\\n）计。"""
    if not lines_keepends:
        return 1, 1
    starts, _ = line_meta_keepends(lines_keepends)
    total_len = sum(len(x) for x in lines_keepends)
    o = max(0, min(offset, total_len))
    if o >= total_len:
        last_i = len(lines_keepends) - 1
        body = lines_keepends[last_i].rstrip("\r\n")
        return last_i + 1, len(body) + 1
    for i, ln in enumerate(lines_keepends):
        s = starts[i]
        line_end = s + len(ln)
        if o < line_end:
            body = ln.rstrip("\r\n")
            within = o - s
            within_body = min(within, len(body))
            return i + 1, within_body + 1
    return len(lines_keepends), 1


def offset_to_line_open_column_onebased(lines_keepends: List[str], abs_end: int) -> Tuple[int, int]:
    """半开右端 abs_end → (end_line, end_column 开区间 1-based)，与 replace 行列矩形一致。"""
    if not lines_keepends:
        return 1, 1
    starts, _ = line_meta_keepends(lines_keepends)
    total_len = sum(len(x) for x in lines_keepends)
    ae = max(0, min(abs_end, total_len))
    if ae >= total_len:
        last_i = len(lines_keepends) - 1
        body = lines_keepends[last_i].rstrip("\r\n")
        return last_i + 1, len(body) + 1
    for i, ln in enumerate(lines_keepends):
        s = starts[i]
        line_end = s + len(ln)
        if ae < line_end:
            body = ln.rstrip("\r\n")
            within = ae - s
            within_body = min(within, len(body))
            return i + 1, within_body + 1



def span_region_rowcols(
    full_text: str, region_start: int, region_end: int
) -> Tuple[int, int, int, int]:
    """全文半开区间 → start_line、start_column、end_line、end_column(开)。"""
    lines_keepends = full_text.splitlines(keepends=True)
    sl, sc = offset_to_line_column_onebased(lines_keepends, region_start)
    el, ec = offset_to_line_open_column_onebased(lines_keepends, region_end)
    return sl, sc, el, ec


# ── 版本化备份辅助函数 ──

def generate_mod_id(path: Path) -> str:
    """生成全局唯一修改流水号：{文件名}_{时间戳}_{4位随机}"""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', path.stem)
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{safe_name}_{ts}_{rand}"


def compute_sha256(content: str) -> str:
    """计算字符串的 SHA256 摘要"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def backup_dir_for_mod(mod_id: str) -> Path:
    """返回指定 mod_id 的备份目录"""
    return _ensure_backup_root() / mod_id


def create_file_backup(
    fp: Path,
    original: str,
    encoding: str,
    mode: str,
    diff_text: str,
) -> str:
    """创建版本化备份，返回 mod_id。"""
    mod_id = generate_mod_id(fp)
    bak_dir = backup_dir_for_mod(mod_id)
    bak_dir.mkdir(parents=True, exist_ok=True)

    # 备份原文件内容
    write_unicode_file(bak_dir / "original", original, encoding=encoding)

    # 写入元数据
    metadata = {
        "mod_id": mod_id,
        "path": str(fp.resolve()),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "mode": mode,
        "encoding": encoding,
        "original_sha256": compute_sha256(original),
        "diff_text": diff_text[:2000] + ("…" if len(diff_text) > 2000 else ""),
    }
    (bak_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return mod_id


def list_backups(fp: Path) -> List[dict]:
    """列出指定文件的所有备份记录，按时间降序。"""
    root = _ensure_backup_root()
    if not root.is_dir():
        return []
    target = str(fp.resolve())
    records = []
    for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        meta_file = d / "metadata.json"
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if meta.get("path") == target:
                meta["backup_dir"] = str(d)
                records.append(meta)
    return records


def restore_backup(mod_id: str) -> Tuple[str, str, str]:
    """回滚指定 mod_id 的备份。返回 (原文件路径, 备份文件内容, 备份编码)。"""
    bak_dir = backup_dir_for_mod(mod_id)
    meta_file = bak_dir / "metadata.json"
    if not meta_file.is_file():
        raise FileNotFoundError(f"备份 {mod_id} 不存在（metadata.json 未找到）")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    original_path = meta["path"]
    encoding = meta.get("encoding", "utf-8")
    backup_content = (bak_dir / "original").read_text(encoding=encoding)
    return original_path, backup_content, encoding