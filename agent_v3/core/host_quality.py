# -*- coding: utf-8 -*-
"""宿主编码质量能力：主动抬质（P0）+ 防御加固（P1）。

挂在写盘前后与 LLM 轮次前：意图注入、证据门、写后诊断/review/引用扫描回灌、
预览指纹、覆盖/漂移/大 diff 等门控。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ── 会话级状态 ──
_QUALITY_BY_CID: Dict[str, "QualityState"] = {}

_WRITE_PATH_SCRIPTS = frozenset(
    {"write_file.py", "replace_in_file.py", "read_write.py", "apply_patch.py"}
)
_EVIDENCE_SCRIPTS = frozenset(
    {"read_file.py", "grep_files.py", "find_in_file.py", "regex_locate.py", "file_search.py"}
)

_DEBUG_INTENT_RE = re.compile(
    r"(报错|异常|bug|debug|traceback|exception|stack\s*trace|"
    r"崩溃|crash|不工作|没法用|修一下|定位问题|出错|"
    r"修复\s*(?:这个|一下|下|bug|报错|问题)|把.{0,12}(?:修|改)好)",
    re.I,
)
_TROUBLESHOOT_RE = re.compile(
    r"(排查|根因|为什么|怎么回事|分析原因|多角度|从哪查)",
    re.I,
)
_STACK_FILE_RE = re.compile(
    r"""(?:File\s+[\"']([^\"']+)[\"']|([A-Za-z]:[\\/][^\s:,]+|/[^\s:,]+))(?::|,?\s*line\s+)(\d+)""",
    re.I,
)
_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+(\w+)|^\s*class\s+(\w+)|^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|"
    r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=",
    re.M,
)

_LARGE_DIFF_LINE_THRESHOLD = 80
_LARGE_DELETE_RATIO = 0.35


@dataclass
class QualityState:
    intent: str = ""  # debug | troubleshoot | ""
    evidence_ok: bool = False
    lenses_injected: bool = False
    stack_targets: List[Tuple[str, int]] = field(default_factory=list)
    stack_addressed: bool = False
    preview_fp: Dict[str, List[str]] = field(default_factory=dict)  # path -> list of fingerprints
    post_write_hash: Dict[str, str] = field(default_factory=dict)  # path -> mtime/size token
    needs_reread: Set[str] = field(default_factory=set)
    pending_review_paths: List[str] = field(default_factory=list)
    fixable_paths: List[str] = field(default_factory=list)  # 红灯时仍允许继续改的路径
    pending_diagnose_red: bool = False
    last_diagnose_summary: str = ""
    reviewed_ok: bool = True
    changeset_id: str = ""
    changeset_mod_ids: List[Dict[str, str]] = field(default_factory=list)  # {path, mod_id}
    turn_wrote: bool = False
    claim_fixed_blocked: bool = False  # 有写入未验证时限制空口「已修复」
    large_delete_flag: bool = False


def get_quality_state(cid: str) -> QualityState:
    cid = str(cid or "").strip()
    if cid not in _QUALITY_BY_CID:
        _QUALITY_BY_CID[cid] = QualityState()
    return _QUALITY_BY_CID[cid]


def reset_quality_turn_flags(cid: str) -> None:
    st = get_quality_state(cid)
    st.turn_wrote = False


def _resolve_write_path(args: Optional[dict], result: Optional[dict] = None) -> str:
    """统一解析写路径：path / dest_path / data.path / data.dest_path。"""
    args = args or {}
    data = {}
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        data = result["data"]
    raw = (
        args.get("path")
        or args.get("dest_path")
        or data.get("path")
        or data.get("dest_path")
        or ""
    )
    return _norm_path(str(raw).strip())


def _track_fixable_path(st: QualityState, path: str) -> None:
    p = _norm_path(path)
    if p and p not in st.fixable_paths:
        st.fixable_paths.append(p)


def detect_and_update_intent(cid: str, user_text: str) -> QualityState:
    st = get_quality_state(cid)
    text = str(user_text or "")
    if not text.strip():
        return st
    stacks = _extract_stack_targets(text)
    hit_trouble = bool(_TROUBLESHOOT_RE.search(text))
    hit_debug = bool(_DEBUG_INTENT_RE.search(text)) or bool(stacks)
    if hit_trouble or hit_debug:
        if hit_trouble:
            st.intent = "troubleshoot"
        else:
            st.intent = "debug"
        st.evidence_ok = False
        st.lenses_injected = False
        st.stack_addressed = False
        st.stack_targets = stacks
    return st


def _extract_stack_targets(text: str) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    for m in _STACK_FILE_RE.finditer(text or ""):
        path = (m.group(1) or m.group(2) or "").strip()
        try:
            line = int(m.group(3))
        except Exception:
            continue
        if path and line > 0:
            out.append((path, line))
    # 去重保序
    seen = set()
    uniq = []
    for p, ln in out:
        key = (p, ln)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((p, ln))
    return uniq[:8]


def note_evidence_tool(cid: str, script: str, args: dict, result: dict) -> None:
    st = get_quality_state(cid)
    sn = str(script or "").lower()
    if sn not in _EVIDENCE_SCRIPTS:
        return
    if not isinstance(result, dict) or not result.get("ok"):
        return
    st.evidence_ok = True
    path = str((args or {}).get("path") or "")
    if st.stack_targets and path:
        for sp, _ln in st.stack_targets:
            if _path_match(sp, path):
                st.stack_addressed = True
                break
    # 读过则清除该 path 的 needs_reread
    if path and sn.startswith("read_file"):
        st.needs_reread.discard(_norm_path(path))
        try:
            fp = Path(path)
            if fp.is_file():
                st.post_write_hash[_norm_path(path)] = _file_token(fp)
        except Exception:
            pass


def _norm_path(p: str) -> str:
    return str(p or "").replace("\\", "/").strip()


def _path_match(a: str, b: str) -> bool:
    aa, bb = _norm_path(a).lower(), _norm_path(b).lower()
    if not aa or not bb:
        return False
    return aa == bb or aa.endswith(bb) or bb.endswith(aa) or Path(aa).name == Path(bb).name


def _file_token(fp: Path) -> str:
    st = fp.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def _args_fingerprint(script: str, args: dict) -> str:
    payload = {
        "script": script,
        "path": _resolve_write_path(args, None),
        "new_text": args.get("new_text"),
        "old_text": args.get("old_text"),
        "content": args.get("content"),
        "line_start": args.get("line_start"),
        "line_end": args.get("line_end"),
        "region_start": args.get("region_start"),
        "region_end": args.get("region_end"),
        "line_ranges": args.get("line_ranges"),
        "regions": args.get("regions"),
        "rules": args.get("rules"),
        "patch_text": args.get("patch_text"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _is_real_write(script: str, args: dict) -> bool:
    if script not in _WRITE_PATH_SCRIPTS:
        return False
    dr = args.get("dry_run", True)
    if dr is True or dr == 1 or str(dr).strip().lower() in ("1", "true"):
        return False
    return True


def _is_dry_run(args: dict) -> bool:
    dr = args.get("dry_run", True)
    return dr is True or dr == 1 or str(dr).strip().lower() in ("1", "true")


def build_quality_ephemeral_messages(cid: str) -> List[Dict[str, Any]]:
    """已禁用：禁止以独立 system/user 消息灌入上下文（会干扰摘要截取与任务连续性）。

    质量信息只允许出现在写工具返回的 data.host_quality；催办靠写前门控错误信息。
    """
    return []


def check_pre_write_quality(
    cid: str,
    script: str,
    args: dict,
    step_title: str = "",
) -> Optional[dict]:
    """写盘前门控。返回 None 表示通过；返回 result dict 表示拒绝。"""
    if script not in _WRITE_PATH_SCRIPTS:
        return None
    st = get_quality_state(cid)
    path = _norm_path(str(args.get("path") or ""))
    real = _is_real_write(script, args)
    title = str(step_title or "")

    # P0: 调试/排查意图下无证据禁止真写
    if real and st.intent in ("debug", "troubleshoot") and not st.evidence_ok:
        return _reject(
            "HostQualityEvidenceRequired",
            "当前为排查/调试意图：请先用 read_file/grep_files 等收集证据（建议多镜头："
            "复现边界/最近变更/日志栈/配置依赖/数据权限/非代码因素），再 dry_run=false 写盘。"
            "质量信息不会以独立消息注入上下文，请以工具返回与本错误为准。",
        )
    if real and st.stack_targets and not st.stack_addressed:
        tops = ", ".join(f"{p}:{ln}" for p, ln in st.stack_targets[:3])
        return _reject(
            "HostQualityStackFirst",
            f"检测到错误栈，请先 read 栈顶文件再改：{tops}",
        )

    # P1: 诊断红灯未清 — 仅允许继续改 fixable/pending 路径，禁止扩大到无关文件
    if real and st.pending_diagnose_red and path:
        allowed_set = {_norm_path(p) for p in (st.fixable_paths or st.pending_review_paths)}
        allowed = bool(allowed_set) and (
            path in allowed_set or any(_path_match(path, p) for p in allowed_set)
        )
        if not allowed:
            return _reject(
                "HostQualityDiagnoseRed",
                "写后诊断仍有问题未处理：请先查看最近写工具返回的 data.host_quality，"
                "修复已改文件上的 fail 项，再改其他文件。",
            )

    # P1: review 未完限制跨文件
    if real and st.pending_review_paths and not st.reviewed_ok and path:
        pending = {_norm_path(p) for p in st.pending_review_paths}
        if path not in pending and not any(_path_match(path, p) for p in pending):
            return _reject(
                "HostQualityReviewPending",
                "上一次真写的 diff review 尚未完成：请先阅读写工具返回的 data.host_quality，"
                "在回复中给出「review结论」后，再跨文件继续写。",
            )

    # P1: 同文件连改需 re-read
    if real and path and path in st.needs_reread:
        return _reject(
            "HostQualityStaleEdit",
            f"文件刚被写入，请先 read_file 确认当前内容再继续修改：{path}",
        )

    # P1: 已存在文件禁止 write_file 默覆盖（须 dry_run 预览或 step_title 明示确认）
    if real and script == "write_file.py" and path:
        try:
            fp = Path(str(args.get("path") or ""))
            if fp.is_file() and fp.stat().st_size > 0:
                confirmed = (
                    "确认整文件覆盖" in title
                    or "confirm overwrite" in title.lower()
                    or path in st.preview_fp
                )
                if not confirmed:
                    return _reject(
                        "HostQualityNoBlindOverwrite",
                        "目标文件已存在：请用 replace_in_file 做局部修改；"
                        "若确需整文件覆盖，先 dry_run=true 预览，或在 step_title 写明「确认整文件覆盖」。",
                    )
        except Exception:
            pass

    # P1: 预览指纹绑定（真写参数须与最近成功 dry_run 一致）
    if real and path and script in ("replace_in_file.py", "write_file.py", "read_write.py"):
        fp_now = _args_fingerprint(script, args)
        prev_list = st.preview_fp.get(path, [])
        if prev_list and fp_now not in prev_list:
            return _reject(
                "HostQualityPreviewMismatch",
                "真写内容与该文件最近一次成功 dry_run 预览不一致：请重新 dry_run=true 预览后再提交。",
            )

    # P1: 异常大 diff（对 replace 的 new_text 行数粗检）
    if real and script == "replace_in_file.py":
        nt = args.get("new_text")
        if isinstance(nt, str) and nt.count("\n") + 1 > _LARGE_DIFF_LINE_THRESHOLD:
            ls, le = args.get("line_start"), args.get("line_end")
            span = None
            try:
                if ls is not None and le is not None:
                    span = int(le) - int(ls) + 1
            except Exception:
                span = None
            if span is None or span > _LARGE_DIFF_LINE_THRESHOLD or (nt.count("\n") + 1) > max(40, (span or 0) * 3):
                return _reject(
                    "HostQualityLargeDiff",
                    f"单次替换内容过大（>{_LARGE_DIFF_LINE_THRESHOLD} 行量级）：请拆成更小的局部 replace，避免误伤。",
                )

    return None


def note_write_tool_result(
    cid: str,
    script: str,
    args: dict,
    result: dict,
) -> None:
    """记录预览指纹 / 真写状态。"""
    if script not in _WRITE_PATH_SCRIPTS:
        return
    if not isinstance(result, dict) or not result.get("ok"):
        return
    st = get_quality_state(cid)
    path = _resolve_write_path(args, result)
    if not path:
        return
    if _is_dry_run(args):
        fps = st.preview_fp.setdefault(path, [])
        fp_new = _args_fingerprint(script, args)
        if fp_new not in fps:
            fps.append(fp_new)
        return
    if not _is_real_write(script, args):
        return
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    # 无实际写入（含 dry_run=false 但 written=false）不置位
    if data.get("written") is False:
        return
    st.turn_wrote = True
    st.reviewed_ok = False
    st.claim_fixed_blocked = True
    if path not in st.pending_review_paths:
        st.pending_review_paths.append(path)
    _track_fixable_path(st, path)
    st.needs_reread.add(path)
    mod_id = data.get("mod_id")
    if mod_id:
        entry = {"path": path, "mod_id": str(mod_id)}
        if entry not in st.changeset_mod_ids:
            st.changeset_mod_ids.append(entry)
        if not st.changeset_id:
            st.changeset_id = str(mod_id)
    try:
        fp = Path(str(args.get("path") or args.get("dest_path") or path))
        if fp.is_file():
            st.post_write_hash[path] = _file_token(fp)
    except Exception:
        pass
    # 大删检测（基于 diff_text）
    dt = data.get("diff_text") or data.get("diffText") or ""
    if isinstance(dt, str) and dt.strip():
        _maybe_flag_large_delete(st, path, dt)


def _maybe_flag_large_delete(st: QualityState, path: str, diff_text: str) -> None:
    dels = sum(1 for ln in diff_text.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    adds = sum(1 for ln in diff_text.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    if dels >= 30 and (adds == 0 or adds < dels * _LARGE_DELETE_RATIO):
        st.pending_diagnose_red = True
        st.large_delete_flag = True
        st.last_diagnose_summary = (
            f"{path}: diff 显示大量删除（-{dels}/+{adds}），请确认并非误删。"
        )
        _track_fixable_path(st, path)


def _reject(err_type: str, message: str) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": {"type": err_type, "message": message},
    }


def mark_review_done(cid: str) -> None:
    st = get_quality_state(cid)
    st.reviewed_ok = True
    st.pending_review_paths.clear()
    # 红灯未清时保留 fixable_paths，避免 review 后无法再改同一文件修诊断
    if not st.pending_diagnose_red:
        st.fixable_paths.clear()
        st.claim_fixed_blocked = False
        st.large_delete_flag = False


def build_post_write_quality_report(
    cid: str,
    written_files: Dict[str, str],
) -> Dict[str, Any]:
    """真写后质量报告（单对象，挂到写工具返回的 data.host_quality）。"""
    paths = [p for p in written_files if str(p).strip()]
    if not paths:
        return {}
    st = get_quality_state(cid)
    norm_paths = [_norm_path(p) for p in paths]
    st.pending_review_paths = list(dict.fromkeys(st.pending_review_paths + norm_paths))
    for p in norm_paths:
        _track_fixable_path(st, p)
    st.reviewed_ok = False
    st.claim_fixed_blocked = True

    checks: List[dict] = []
    actions: List[str] = []
    any_red = bool(st.large_delete_flag)
    any_degraded = False
    if st.large_delete_flag:
        checks.append({
            "id": "large_delete_warning",
            "status": "fail",
            "summary": st.last_diagnose_summary or "检测到异常大删除，请确认并非误删",
        })
        actions.append("确认大删除是否为有意改动")

    # Python 诊断
    py_paths = [p for p in paths if str(p).lower().endswith(".py")]
    if py_paths:
        try:
            import unified_diagnose as _udiag

            diag_path = (
                os.path.dirname(py_paths[0])
                if len(py_paths) == 1
                else os.path.commonpath(py_paths)
            )
            diag_result = _udiag.agent_main(path=diag_path, no_ruff=False, limit=50)
            diag_ok = _diagnose_is_clean_for_paths(diag_result, py_paths)
            slim = _slim_diagnose_for_paths(diag_result, py_paths)
            checks.append({
                "id": "auto_diagnose",
                "status": "pass" if diag_ok else "fail",
                "summary": "Python 诊断通过" if diag_ok else "Python 诊断发现 error",
                "detail": slim,
            })
            if not diag_ok:
                any_red = True
                st.last_diagnose_summary = _summarize_diagnose(slim)
                actions.append("先修复 auto_diagnose 中的 error，再扩大改动")
        except Exception as exc:
            any_degraded = True
            checks.append({
                "id": "auto_diagnose",
                "status": "degraded",
                "summary": f"诊断工具异常，已降级督办: {exc}",
            })
            actions.append("诊断工具异常：请自行对改动的 .py 做语法检查")

    # 结构：py/json 硬检；java 括号启发式
    struct_hits = _structure_check_written(paths)
    if struct_hits:
        struct_ok = all(x.get("ok") for x in struct_hits)
        checks.append({
            "id": "structure_integrity",
            "status": "pass" if struct_ok else "fail",
            "summary": "结构校验通过" if struct_ok else "结构校验失败（语法/括号等）",
            "results": struct_hits,
        })
        if not struct_ok:
            any_red = True
            actions.append("先修复 structure_integrity 失败项")

    # Python 编译
    impact = _py_compile_written(paths)
    if impact:
        impact_ok = all(x.get("ok") for x in impact)
        checks.append({
            "id": "impact_verify_py",
            "status": "pass" if impact_ok else "fail",
            "summary": "py_compile 通过" if impact_ok else "py_compile 失败",
            "results": impact,
        })
        if not impact_ok:
            any_red = True
            actions.append("先修复 py_compile 错误")

    # Java：检测 JDK，可用则 javac，否则督办降级
    java_paths = [p for p in paths if str(p).lower().endswith(".java")]
    if java_paths:
        jdk = _detect_jdk()
        if jdk.get("javac"):
            jres = _javac_compile_written(java_paths, javac=jdk["javac"])
            hard_fail = []
            soft_fail = []
            for item in jres:
                if item.get("ok"):
                    continue
                err = str(item.get("error") or "")
                if _javac_error_looks_like_classpath(err):
                    soft_fail.append(item)
                else:
                    hard_fail.append(item)
            if hard_fail:
                checks.append({
                    "id": "impact_verify_java",
                    "status": "fail",
                    "summary": "javac 语法/结构编译失败",
                    "jdk": {"java": jdk.get("java"), "javac": jdk.get("javac")},
                    "results": jres,
                })
                any_red = True
                actions.append("先根据 javac 输出修复语法错误")
            elif soft_fail:
                any_degraded = True
                checks.append({
                    "id": "impact_verify_java",
                    "status": "degraded",
                    "summary": "javac 失败更像缺依赖/classpath（不按红灯卡死跨文件）",
                    "jdk": {"java": jdk.get("java"), "javac": jdk.get("javac")},
                    "results": jres,
                    "host_instruction": "请说明依赖情况并完成 review；勿因 classpath 误报扩大改动封锁。",
                })
                actions.append("确认 Java 依赖/classpath；给出 review结论")
            else:
                checks.append({
                    "id": "impact_verify_java",
                    "status": "pass",
                    "summary": "javac 编译通过",
                    "jdk": {"java": jdk.get("java"), "javac": jdk.get("javac")},
                    "results": jres,
                })
        else:
            any_degraded = True
            checks.append({
                "id": "impact_verify_java",
                "status": "degraded",
                "summary": "本机未检测到 javac/java，已降级为督办（未做真实编译）",
                "jdk": jdk,
                "host_instruction": (
                    "请自行确认：① 语法与括号 ② import/包名 ③ 与调用方签名一致；"
                    "并在回复中给出「review结论」。"
                ),
            })
            actions.append("JDK 不可用：完成人工 Java 自检并给出 review结论")

    # 引用扫描
    ref_hits = _scan_refs_after_writes(paths)
    if ref_hits:
        checks.append({
            "id": "reference_scan",
            "status": "info",
            "summary": f"发现 {len(ref_hits)} 个符号的引用位点，请确认是否需同步修改",
            "symbols": ref_hits,
        })
        actions.append("核对 reference_scan 中的引用是否需同步")

    # 强制 review（始终）
    checks.append({
        "id": "diff_review_required",
        "status": "required",
        "summary": "必须完成 diff review 并在回复中写明「review结论」",
        "checklist": [
            "是否遗漏引用/调用点",
            "与周围风格是否一致",
            "有无调试残留/无意新依赖",
            "边界与错误路径",
            "多文件时是否只改了一半",
        ],
        "modified_files": paths[:12],
        "changeset_mod_ids": st.changeset_mod_ids[-8:],
    })
    actions.append("下一条回复须含「review结论」+ 上述 checklist 简评")

    st.pending_diagnose_red = any_red
    if any_red and not st.last_diagnose_summary:
        fails = [c for c in checks if c.get("status") == "fail"]
        st.last_diagnose_summary = json.dumps(fails, ensure_ascii=False)[:1500]
    if not any_red and not st.large_delete_flag:
        st.last_diagnose_summary = ""

    overall = "red" if any_red else ("degraded" if any_degraded else "green")
    report = {
        "host_quality": True,
        "overall": overall,
        "files": paths[:12],
        "checks": checks,
        "required_next_actions": actions,
        "must_include_in_reply": "review结论",
        "model_instruction": (
            "【宿主质量回灌】以上检查已附在本次写工具结果中（非独立 system 消息）。"
            "请逐项回应 checks：fail 必须先处理；degraded 须人工确认；"
            "required 项完成后，回复中必须出现「review结论」。"
            "在 overall=red 或未完成 review 前，禁止对用户宣称已修复/已完成。"
        ),
    }
    return report


def _javac_error_looks_like_classpath(err: str) -> bool:
    e = (err or "").lower()
    keys = (
        "package does not exist",
        "cannot find symbol",
        "找不到符号",
        "程序包不存在",
        "classpath",
        "class path",
        "package ",
    )
    # 纯语法类错误优先不算 classpath
    syntax = ("'; error", "非法的表达式", "'{' expected", "')' expected", "reached end of file")
    if any(s in e for s in syntax):
        return False
    return any(k in e for k in keys)


def attach_host_quality_to_write_result(
    cid: str,
    path: str,
    result: dict,
) -> dict:
    """把质量报告挂到写工具返回（data.host_quality），避免独占 system 消息。"""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    path = str(path or "").strip()
    if not path:
        return result
    report = build_post_write_quality_report(cid, {path: "write"})
    if not report:
        return result
    data = result.get("data")
    if not isinstance(data, dict):
        data = {} if data is None else {"value": data}
        result = dict(result)
        result["data"] = data
    else:
        # 浅拷贝，避免改到工具内部缓存对象
        data = dict(data)
        result = dict(result)
        result["data"] = data
    data["host_quality"] = report
    # 顶层也放一份短提示，截断后仍易看见
    result["host_quality_overall"] = report.get("overall")
    result["host_quality_actions"] = report.get("required_next_actions")
    return result


def build_post_write_quality_messages(
    cid: str,
    written_files: Dict[str, str],
) -> List[Dict[str, Any]]:
    """兼容旧接口：不再落盘独立 system 回灌（改挂工具返回）。"""
    return []


def note_assistant_may_complete_review(cid: str, assistant_text: str) -> None:
    """须显式给出 review 结论，才标记 review 完成。"""
    t = str(assistant_text or "")
    if not t.strip():
        return
    st = get_quality_state(cid)
    if not st.pending_review_paths:
        return
    if re.search(r"(review\s*结论|【review】|自检结论|复查结论)", t, re.I) and len(t.strip()) >= 30:
        mark_review_done(cid)


def note_assistant_claim_fixed(cid: str, assistant_text: str) -> Optional[Dict[str, Any]]:
    """空口已修复：只置位，靠 ephemeral 催办；不再落盘独立 system 消息。"""
    t = str(assistant_text or "")
    if not t.strip():
        return None
    st = get_quality_state(cid)
    if not (st.pending_diagnose_red or (st.pending_review_paths and not st.reviewed_ok)):
        st.claim_fixed_blocked = False
        return None
    if re.search(r"(已修复|已经修好|搞定了|问题已解决|可以了|修复完成|已完成修复)", t):
        st.claim_fixed_blocked = True
    return None


def _detect_jdk() -> Dict[str, Optional[str]]:
    import shutil

    return {
        "java": shutil.which("java"),
        "javac": shutil.which("javac"),
    }


def _javac_compile_written(paths: List[str], *, javac: str) -> List[dict]:
    import subprocess
    import tempfile

    out: List[dict] = []
    for p in paths[:6]:
        fp = Path(p)
        if not fp.is_file():
            continue
        tmp = tempfile.mkdtemp(prefix="hq_javac_")
        try:
            cp = subprocess.run(
                [javac, "-encoding", "UTF-8", "-Xlint:none", "-d", tmp, str(fp)],
                capture_output=True,
                text=True,
                timeout=45,
                encoding="utf-8",
                errors="replace",
            )
            err = (cp.stderr or cp.stdout or "").strip()
            if cp.returncode == 0:
                out.append({"path": str(fp), "ok": True, "check": "javac"})
            else:
                out.append({
                    "path": str(fp),
                    "ok": False,
                    "check": "javac",
                    "error": err[:2000] or f"exit={cp.returncode}",
                })
        except Exception as exc:
            out.append({"path": str(fp), "ok": False, "check": "javac", "error": str(exc)})
        finally:
            try:
                import shutil as _sh

                _sh.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass
    return out


def _diagnose_is_clean_for_paths(diag_result: Any, paths: List[str]) -> bool:
    """只把写入文件上的 error 算不干净；warning 不拉红灯。"""
    if not isinstance(diag_result, dict):
        return False
    if diag_result.get("data") is None and diag_result.get("ok") is False:
        return False
    data = diag_result.get("data")
    if not isinstance(data, dict):
        return bool(diag_result.get("ok"))
    diags = data.get("diagnostics")
    if not isinstance(diags, list):
        return bool(diag_result.get("ok", True))
    written = [_norm_path(p) for p in paths]
    for d in diags:
        if not isinstance(d, dict):
            continue
        if str(d.get("severity") or "").lower() != "error":
            continue
        f = _norm_path(str(d.get("file") or ""))
        if any(_path_match(f, w) for w in written):
            return False
    return True


def _slim_diagnose_for_paths(diag_result: Any, paths: List[str]) -> Any:
    if not isinstance(diag_result, dict):
        return diag_result
    data = diag_result.get("data")
    if not isinstance(data, dict):
        return diag_result
    diags = data.get("diagnostics")
    if not isinstance(diags, list):
        return diag_result
    written = [_norm_path(p) for p in paths]
    related = []
    for d in diags:
        if not isinstance(d, dict):
            continue
        f = _norm_path(str(d.get("file") or ""))
        if any(_path_match(f, w) for w in written):
            related.append(d)
    out = dict(diag_result)
    nd = dict(data)
    nd["diagnostics"] = related[:40]
    nd["filtered_to_written_files"] = True
    err_n = sum(1 for d in related if str(d.get("severity") or "").lower() == "error")
    warn_n = sum(1 for d in related if str(d.get("severity") or "").lower() == "warning")
    nd["summary"] = {"errors": err_n, "warnings": warn_n, "total": len(related)}
    out["data"] = nd
    out["ok"] = err_n == 0
    return out


def _brace_balance_ok(text: str) -> Tuple[bool, str]:
    """粗检括号/大括号是否配对（字符串与注释做简单剥离）。"""
    # 去掉粗略字符串与 // /* */ 注释
    t = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    t = re.sub(r"//.*?$", "", t, flags=re.M)
    t = re.sub(r'"(?:\\.|[^"\\])*"', '""', t)
    t = re.sub(r"'(?:\\.|[^'\\])*'", "''", t)
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for ch in t:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                return False, f"括号不匹配，遇到 {ch}"
            stack.pop()
    if stack:
        return False, f"未闭合符号: {''.join(stack)}"
    return True, "ok"


def _structure_check_written(paths: List[str]) -> List[dict]:
    out = []
    for p in paths[:8]:
        fp = Path(p)
        if not fp.is_file():
            continue
        suf = fp.suffix.lower()
        if suf == ".py":
            try:
                import ast

                src = fp.read_text(encoding="utf-8", errors="replace")
                ast.parse(src, filename=str(fp))
                out.append({"path": str(fp), "ok": True, "check": "ast.parse"})
            except Exception as exc:
                out.append({"path": str(fp), "ok": False, "check": "ast.parse", "error": str(exc)})
        elif suf == ".json":
            try:
                json.loads(fp.read_text(encoding="utf-8", errors="replace"))
                out.append({"path": str(fp), "ok": True, "check": "json.loads"})
            except Exception as exc:
                out.append({"path": str(fp), "ok": False, "check": "json.loads", "error": str(exc)})
        elif suf == ".java":
            try:
                src = fp.read_text(encoding="utf-8", errors="replace")
                ok, msg = _brace_balance_ok(src)
                out.append({
                    "path": str(fp),
                    "ok": ok,
                    "check": "java_brace_balance",
                    **({} if ok else {"error": msg}),
                })
            except Exception as exc:
                out.append({
                    "path": str(fp),
                    "ok": False,
                    "check": "java_brace_balance",
                    "error": str(exc),
                })
    return out


def _summarize_diagnose(diag_result: Any) -> str:
    try:
        s = json.dumps(diag_result, ensure_ascii=False)
        return s[:1500]
    except Exception:
        return str(diag_result)[:1500]


def _py_compile_written(paths: List[str]) -> List[dict]:
    out = []
    for p in paths[:8]:
        fp = Path(p)
        if fp.suffix.lower() != ".py" or not fp.is_file():
            continue
        try:
            import py_compile

            py_compile.compile(str(fp), doraise=True)
            out.append({"path": str(fp), "ok": True, "check": "py_compile"})
        except Exception as exc:
            out.append({"path": str(fp), "ok": False, "check": "py_compile", "error": str(exc)})
    return out


def _scan_refs_after_writes(paths: List[str]) -> List[dict]:
    """从刚写入文件中提取定义名，在同目录做一次简单引用计数提示。"""
    java_def = re.compile(
        r"^\s*(?:public|private|protected|static|final|abstract|\s)*"
        r"(?:class|interface|enum)\s+(\w+)"
        r"|^\s*(?:public|private|protected|static|final|\s)*"
        r"[\w.<>,\[\]]+\s+(\w+)\s*\(",
        re.M,
    )
    symbols: List[str] = []
    for p in paths[:6]:
        fp = Path(p)
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _DEF_RE.finditer(text):
            name = next((g for g in m.groups() if g), None)
            if name and len(name) > 2:
                symbols.append(name)
        if fp.suffix.lower() == ".java":
            for m in java_def.finditer(text):
                name = next((g for g in m.groups() if g), None)
                if name and len(name) > 2 and name not in (
                    "if", "for", "while", "switch", "catch", "return", "new"
                ):
                    symbols.append(name)
    symbols = list(dict.fromkeys(symbols))[:12]
    if not symbols:
        return []
    roots = []
    for p in paths[:6]:
        try:
            roots.append(str(Path(p).resolve().parent))
        except Exception:
            pass
    if not roots:
        return []
    root = os.path.commonpath(roots) if len(roots) > 1 else roots[0]
    hits = []
    for sym in symbols:
        count, samples = _rg_count(root, sym)
        if count >= 1:
            hits.append({"symbol": sym, "approx_hits": count, "samples": samples[:5]})
    return hits


def _rg_count(root: str, symbol: str) -> Tuple[int, List[str]]:
    samples: List[str] = []
    count = 0
    try:
        import subprocess

        cp = subprocess.run(
            ["rg", "-n", "--no-heading", "-m", "20", rf"\b{re.escape(symbol)}\b", root],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
        )
        lines = [ln for ln in (cp.stdout or "").splitlines() if ln.strip()]
        count = len(lines)
        samples = lines[:5]
    except Exception:
        try:
            for fp in Path(root).rglob("*.*"):
                if fp.suffix.lower() not in (".py", ".java", ".js", ".ts", ".tsx"):
                    continue
                if fp.stat().st_size > 400_000:
                    continue
                try:
                    txt = fp.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                n = len(re.findall(rf"\b{re.escape(symbol)}\b", txt))
                if n:
                    count += n
                    samples.append(f"{fp}:{n}")
                if count > 50:
                    break
        except Exception:
            pass
    return count, samples
