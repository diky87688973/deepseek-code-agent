# -*- coding: utf-8
"""agent_v3.core.modes_kb"""
from __future__ import annotations

from agent_v3.core.deps import *  # noqa: F403
from agent_v3.core.shared_state import *  # noqa: F403

def _build_kb_system_messages(cid: str) -> List[str]:
    """每个勾选的知识库文件独占一条 system 消息内容。"""
    if not KB_BASE_DIR or not KB_BASE_DIR.is_dir():
        return []
    with _KB_CHECKED_LOCK:
        checked = _KB_CHECKED_STATE.get(cid, set())
        if not checked:
            _kb_load_single_cid_checked(cid)
            checked = _KB_CHECKED_STATE.get(cid, set())
    if not checked:
        return []
    out: List[str] = []
    for rel in sorted(checked):
        fpath = _kb_safe_resolve_rel(rel)
        if not fpath or not _kb_file_allowed_when_checked(fpath):
            continue
        try:
            text = _read_kb_file_text(fpath).strip()
        except Exception:
            continue
        if not text:
            continue
        out.append(f"【知识库：{rel}】\n{text}")
    return out

def _extract_dispatch_title(content: Optional[str], max_len: int = 20) -> Optional[str]:
    if not content:
        return None
    text = str(content)
    m = re.search(r"\[\[TOOL_TITLE\]\]\s*(.+)", text, flags=re.IGNORECASE)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    if not title:
        return None
    # 安全截断，避免前端标题过长撑破布局
    if len(title) > max_len:
        title = title[:max_len].rstrip() + "…"
    return title

def _has_explicit_mode_command(text: str, commands: Tuple[str, ...]) -> bool:
    raw = str(text or "")
    parts = [x.strip().lower() for x in re.split(r"[\s,;，。]+", raw) if x.strip()]
    return any(p in commands for p in parts)

def _kb_attached_file_count(cid: str) -> int:
    """与 _build_kb_system_messages 中实际参与组包的文件数量一致（已勾选、存在、未超大小）。"""
    if not KB_BASE_DIR or not KB_BASE_DIR.is_dir():
        return 0
    with _KB_CHECKED_LOCK:
        checked = _KB_CHECKED_STATE.get(cid, set())
        if not checked:
            _kb_load_single_cid_checked(cid)
            checked = _KB_CHECKED_STATE.get(cid, set())
    if not checked:
        return 0
    n = 0
    for rel in sorted(checked):
        fpath = _kb_safe_resolve_rel(rel)
        if not fpath or not _kb_file_allowed_when_checked(fpath):
            continue
        n += 1
    return n

def _kb_file_allowed_when_checked(fpath: Path) -> bool:
    """勾选时校验：存在、大小上限；表格类需已安装 openpyxl。"""
    if KB_BASE_DIR:
        try:
            rel = fpath.resolve().relative_to(KB_BASE_DIR.resolve())
            if _kb_rel_has_hidden_segment(str(rel).replace("\\", "/")):
                return False
        except (ValueError, OSError):
            return False
    try:
        if fpath.stat().st_size > _KB_MAX_FILE_SIZE:
            return False
    except OSError:
        return False
    ext = fpath.suffix.lower()
    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            return False
    return True

def _kb_rel_has_hidden_segment(rel: str) -> bool:
    """路径任一分段以 . 开头视为隐藏（.svn、.git、.env 等），不参与列表与勾选。"""
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p and p != "."]
    return any(p.startswith(".") for p in parts)

def _kb_safe_resolve_rel(rel: str) -> Optional[Path]:
    """将相对路径解析为 KB 根下的真实文件路径；禁止 .. 与越界。"""
    if not KB_BASE_DIR:
        return None
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or _kb_rel_has_hidden_segment(raw):
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return None
    try:
        root = KB_BASE_DIR.resolve()
        candidate = (KB_BASE_DIR.joinpath(*parts)).resolve()
        candidate.relative_to(root)
    except (ValueError, OSError):
        return None
    if not candidate.is_file():
        return None
    return candidate

def _read_kb_file_text(fpath: Path) -> str:
    ext = fpath.suffix.lower()
    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(fpath, read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                sheet_name = ws.title
                rows.append(f"[Sheet: {sheet_name}]")
                for row in ws.iter_rows(values_only=True):
                    row_vals = [str(v) if v is not None else "" for v in row]
                    rows.append("\t".join(row_vals))
            wb.close()
            return "\n".join(rows)
        except ImportError:
            return "[需安装 openpyxl 才能读取: pip install openpyxl]"
    if ext == ".csv":
        return fpath.read_text(encoding="utf-8", errors="replace")
    return fpath.read_text(encoding="utf-8", errors="replace")

def _resolve_conversation_mode(conversation_id: str, user_text: str, mode_hint: str = "") -> str:
    t = str(user_text or "").lower()
    full_text = str(user_text or "")
    mode = CONVERSATION_MODES.get(conversation_id, "auto")
    hint = str(mode_hint or "").strip().lower()
    if hint in {"auto", "plan", "execute"}:
        mode = hint
        if hint == "execute":
            CONVERSATION_AUDIT_ONLY.pop(conversation_id, None)
        elif _is_audit_only_intent(full_text):
            CONVERSATION_AUDIT_ONLY[conversation_id] = True
            mode = "plan"
    elif _is_audit_only_intent(full_text):
        CONVERSATION_AUDIT_ONLY[conversation_id] = True
        mode = "plan"
    elif _has_explicit_mode_command(t, EXECUTE_MODE_COMMANDS) or any(k in t for k in EXECUTE_MODE_KEYS):
        CONVERSATION_AUDIT_ONLY.pop(conversation_id, None)
        mode = "execute"
    elif _has_explicit_mode_command(t, PLAN_MODE_COMMANDS):
        mode = "plan"
    elif any(k in t for k in PLAN_MODE_KEYS):
        mode = "plan"
    CONVERSATION_MODES[conversation_id] = mode
    return mode

def list_kb_files_for_api() -> List[Dict[str, Any]]:
    """列出知识库根下可浏览文件（排除隐藏路径段与 __pycache__）。"""
    if not KB_BASE_DIR or not KB_BASE_DIR.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(KB_BASE_DIR.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        try:
            rel = p.relative_to(KB_BASE_DIR)
        except ValueError:
            continue
        if _kb_rel_has_hidden_segment(str(rel).replace("\\", "/")):
            continue
        rel_s = str(rel).replace("\\", "/")
        out.append({"path": rel_s, "name": p.name, "mtime": p.stat().st_mtime})
    return out

