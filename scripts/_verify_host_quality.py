# -*- coding: utf-8 -*-
"""host_quality 自验证：可重复跑 ≥10 轮，任一轮失败即非零退出。"""
from __future__ import annotations

import os
import sys
import json
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from agent_v4.core import host_quality as hq  # noqa: E402
from agent_v4.version import AGENT_APP_VERSION  # noqa: E402


def _clear():
    hq._QUALITY_BY_CID.clear()


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def case_01_evidence_gate():
    _clear()
    cid = "c01"
    st = hq.detect_and_update_intent(cid, "这个报错怎么修？Traceback Exception")
    _assert(st.intent == "debug", f"intent={st.intent}")
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "a.py", "dry_run": False, "new_text": "x\n"}
    )
    _assert(r and r["error"]["type"] == "HostQualityEvidenceRequired", r)
    hq.note_evidence_tool(cid, "read_file.py", {"path": "a.py"}, {"ok": True, "data": {}})
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "a.py", "dry_run": False, "new_text": "x\n"}
    )
    _assert(r is None, r)


def case_02_stack_first():
    _clear()
    cid = "c02"
    hq.detect_and_update_intent(
        cid, 'File "D:/proj/foo.py", line 42, in bar\nValueError: boom'
    )
    st = hq.get_quality_state(cid)
    _assert(st.stack_targets, f"no stack: {st.stack_targets}")
    hq.note_evidence_tool(cid, "grep_files.py", {"path": "other"}, {"ok": True})
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "z.py", "dry_run": False, "new_text": "x"}
    )
    _assert(r and r["error"]["type"] == "HostQualityStackFirst", r)
    hq.note_evidence_tool(cid, "read_file.py", {"path": "D:/proj/foo.py"}, {"ok": True})
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "D:/proj/foo.py", "dry_run": False, "new_text": "x"}
    )
    _assert(r is None, r)


def case_03_troubleshoot_lenses():
    _clear()
    cid = "c03"
    hq.detect_and_update_intent(cid, "请排查一下为什么失败，从多角度分析")
    _assert(hq.get_quality_state(cid).intent == "troubleshoot", hq.get_quality_state(cid).intent)
    # 禁止以独立消息灌入；ephemeral 必须为空
    msgs = hq.build_quality_ephemeral_messages(cid)
    _assert(msgs == [], msgs)
    # 无证据时真写仍被门控拦住（催办在错误信息里，不在独立消息）
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "a.py", "dry_run": False, "new_text": "x"}
    )
    _assert(r and r["error"]["type"] == "HostQualityEvidenceRequired", r)


def case_04_preview_fingerprint():
    _clear()
    cid = "c04"
    args_ok = {"path": "b.py", "dry_run": True, "new_text": "aaa"}
    hq.note_write_tool_result(
        cid, "replace_in_file.py", args_ok, {"ok": True, "data": {"dry_run": True}}
    )
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "b.py", "dry_run": False, "new_text": "bbb"}
    )
    _assert(r and r["error"]["type"] == "HostQualityPreviewMismatch", r)
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "b.py", "dry_run": False, "new_text": "aaa"}
    )
    _assert(r is None, r)


def case_05_stale_edit_and_reread():
    _clear()
    cid = "c05"
    hq.note_write_tool_result(
        cid,
        "replace_in_file.py",
        {"path": "c.py", "dry_run": False, "new_text": "x"},
        {"ok": True, "data": {"written": True, "mod_id": "m1"}},
    )
    st = hq.get_quality_state(cid)
    _assert("c.py" in st.needs_reread, st.needs_reread)
    _assert(st.changeset_mod_ids and st.changeset_mod_ids[0]["mod_id"] == "m1", st.changeset_mod_ids)
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "c.py", "dry_run": False, "new_text": "y"}
    )
    _assert(r and r["error"]["type"] == "HostQualityStaleEdit", r)
    hq.note_evidence_tool(cid, "read_file.py", {"path": "c.py"}, {"ok": True})
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "c.py", "dry_run": False, "new_text": "y"}
    )
    _assert(r is None, r)


def case_06_review_and_cross_file():
    _clear()
    cid = "c06"
    st = hq.get_quality_state(cid)
    st.pending_review_paths = ["c.py"]
    st.reviewed_ok = False
    st.evidence_ok = True
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "d.py", "dry_run": False, "new_text": "x"}
    )
    _assert(r and r["error"]["type"] == "HostQualityReviewPending", r)
    hq.note_assistant_may_complete_review(cid, "随便 review 一下")
    _assert(hq.get_quality_state(cid).reviewed_ok is False, "weak review should not clear")
    hq.note_assistant_may_complete_review(
        cid, "review结论：无遗漏引用，风格一致，无调试残留，边界与错误路径OK。"
    )
    _assert(hq.get_quality_state(cid).reviewed_ok is True, "explicit review should clear")
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "d.py", "dry_run": False, "new_text": "x"}
    )
    _assert(r is None, r)


def case_07_diagnose_filter_and_red():
    _clear()
    import unified_diagnose as u

    td = tempfile.mkdtemp()
    bad = Path(td) / "bad.py"
    good = Path(td) / "good.py"
    bad.write_text("def x(\n", encoding="utf-8")
    good.write_text("def y():\n    return 1\n", encoding="utf-8")
    diag = u.agent_main(path=td, no_ruff=True, limit=20)
    _assert(hq._diagnose_is_clean_for_paths(diag, [str(good)]) is True, "good should be clean")
    _assert(hq._diagnose_is_clean_for_paths(diag, [str(bad)]) is False, "bad should be red")

    cid = "c07"
    report = hq.build_post_write_quality_report(cid, {str(good): "t"})
    _assert(report.get("overall") in ("green", "degraded"), report)
    _assert(any(c.get("id") == "diff_review_required" for c in report.get("checks") or []), report)
    st = hq.get_quality_state(cid)
    _assert(st.pending_diagnose_red is False, st.last_diagnose_summary)
    _assert(st.claim_fixed_blocked is True, "should block claim after write")

    st.pending_diagnose_red = True
    st.pending_review_paths = ["a.py"]
    st.evidence_ok = True
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "b.py", "dry_run": False, "new_text": "x"}
    )
    _assert(r and r["error"]["type"] == "HostQualityDiagnoseRed", r)
    st.pending_review_paths = []
    r = hq.check_pre_write_quality(
        cid, "replace_in_file.py", {"path": "a.py", "dry_run": False, "new_text": "x"}
    )
    _assert(r and r["error"]["type"] == "HostQualityDiagnoseRed", r)


def case_08_attach_to_tool_result():
    _clear()
    cid = "c08"
    td = tempfile.mkdtemp()
    bad = Path(td) / "broken.py"
    bad.write_text("def z(\n", encoding="utf-8")
    result = {"ok": True, "data": {"written": True, "path": str(bad)}}
    result = hq.attach_host_quality_to_write_result(cid, str(bad), result)
    hq_obj = (result.get("data") or {}).get("host_quality") or {}
    _assert(hq_obj.get("overall") == "red", hq_obj)
    _assert(result.get("host_quality_overall") == "red", result)
    _assert(hq.build_post_write_quality_messages(cid, {str(bad): "t"}) == [], "no system dump")
    m = hq.note_assistant_claim_fixed(cid, "已经修好了，可以了")
    _assert(m is None, "no system claim msg")
    _assert(hq.get_quality_state(cid).claim_fixed_blocked is True, "flag set")


def case_09_overwrite_and_large_diff():
    _clear()
    cid = "c09"
    td = tempfile.mkdtemp()
    fp = Path(td) / "exist.py"
    fp.write_text("print(1)\n", encoding="utf-8")
    r = hq.check_pre_write_quality(
        cid,
        "write_file.py",
        {"path": str(fp), "dry_run": False, "content": "print(2)\n"},
    )
    _assert(r and r["error"]["type"] == "HostQualityNoBlindOverwrite", r)
    hq.note_write_tool_result(
        cid,
        "write_file.py",
        {"path": str(fp), "dry_run": True, "content": "print(2)\n"},
        {"ok": True, "data": {"dry_run": True}},
    )
    r = hq.check_pre_write_quality(
        cid,
        "write_file.py",
        {"path": str(fp), "dry_run": False, "content": "print(2)\n"},
        step_title="",
    )
    _assert(r is None, r)

    big = "\n".join(f"line{i}" for i in range(120))
    r = hq.check_pre_write_quality(
        cid,
        "replace_in_file.py",
        {"path": "big.py", "dry_run": False, "new_text": big},
    )
    _assert(r and r["error"]["type"] == "HostQualityLargeDiff", r)


def case_10_agent_turn_wiring_and_gates():
    _clear()
    from agent_v4.core import agent_turn as at

    _assert(hasattr(at, "_apply_host_quality_write_gate"), "missing write gate helper")
    _assert(hasattr(at, "_host_quality"), "missing host_quality bind")
    from agent_v4.runtime.host_policy import HostPolicy
    from agent_v4.runtime.agent_runtime import AgentRuntime

    _assert(callable(HostPolicy), "HostPolicy missing")
    _assert(callable(AgentRuntime), "AgentRuntime missing")
    _assert(AGENT_APP_VERSION == "v1.5", AGENT_APP_VERSION)

    previewed, written = {}, {}
    r = at._check_write_preview(
        "replace_in_file.py",
        {"path": "p.py", "dry_run": True},
        "t",
        previewed,
        written,
    )
    # 门控仅放行 dry_run；previewed 由成功返回后写入（见 agent_runtime）
    _assert(r is None and "p.py" not in previewed, (r, previewed))
    previewed["p.py"] = "t"
    r = at._check_write_preview(
        "replace_in_file.py",
        {"path": "q.py", "dry_run": False},
        "t",
        previewed,
        written,
    )
    _assert(r and r["error"]["type"] == "PreviewRequired", r)

    cid = "c10"
    hq.detect_and_update_intent(cid, "报错了帮我修")
    r = at._apply_host_quality_write_gate(
        cid,
        "replace_in_file.py",
        {"path": "p.py", "dry_run": False, "new_text": "x"},
        "t",
        previewed,
        written,
    )
    _assert(r and r["error"]["type"] == "HostQualityEvidenceRequired", r)
    _assert(at._build_post_write_diagnostic({"a.py": "t"}, cid) == [], "no system msgs")


def case_11_dry_run_variants_and_path_norm():
    _clear()
    cid = "c11"
    hq.note_write_tool_result(
        cid,
        "replace_in_file.py",
        {"path": r"D:\work\a.py", "dry_run": 0, "new_text": "x"},
        {"ok": True, "data": {"written": True}},
    )
    st = hq.get_quality_state(cid)
    _assert("D:/work/a.py" in st.needs_reread, st.needs_reread)
    r = hq.check_pre_write_quality(
        cid,
        "replace_in_file.py",
        {"path": "D:/work/a.py", "dry_run": "false", "new_text": "y"},
    )
    _assert(r and r["error"]["type"] == "HostQualityStaleEdit", r)
    _clear()
    cid = "c11b"
    hq.note_write_tool_result(
        cid,
        "replace_in_file.py",
        {"path": "z.py", "dry_run": False, "new_text": "x"},
        {"ok": True, "data": {"written": False, "dry_run": True}},
    )
    st = hq.get_quality_state(cid)
    _assert(st.turn_wrote is False, st)


def case_12_real_replace_fingerprint_and_postwrite():
    _clear()
    import replace_in_file as rif

    td = tempfile.mkdtemp()
    fp = Path(td) / "sample.py"
    fp.write_text("def hello():\n    return 1\n", encoding="utf-8")
    cid = "c12"
    args_preview = {
        "path": str(fp),
        "dry_run": True,
        "line_start": 1,
        "line_end": 2,
        "new_text": "def hello():\n    return 2\n",
    }
    prev = rif.agent_main(**args_preview)
    _assert(prev.get("ok") is True, prev)
    hq.note_write_tool_result(cid, "replace_in_file.py", args_preview, prev)
    bad = dict(args_preview)
    bad["dry_run"] = False
    bad["new_text"] = "def hello():\n    return 9\n"
    r = hq.check_pre_write_quality(cid, "replace_in_file.py", bad)
    _assert(r and r["error"]["type"] == "HostQualityPreviewMismatch", r)
    good = dict(args_preview)
    good["dry_run"] = False
    r = hq.check_pre_write_quality(cid, "replace_in_file.py", good)
    _assert(r is None, r)
    committed = rif.agent_main(**good)
    _assert(committed.get("ok") is True, committed)
    attached = hq.attach_host_quality_to_write_result(cid, str(fp), committed)
    _assert((attached.get("data") or {}).get("host_quality"), attached)
    _assert(fp.read_text(encoding="utf-8").startswith("def hello():\n    return 2"), fp.read_text(encoding="utf-8"))


def case_13_win_traceback_and_large_delete():
    _clear()
    cid = "c13"
    text = (
        r"Traceback (most recent call last):" + "\n"
        + r'  File "C:\Users\Fan\app\main.py", line 88, in <module>' + "\n"
        + r"RuntimeError: boom"
    )
    st = hq.detect_and_update_intent(cid, text)
    _assert(st.intent == "debug", st.intent)
    _assert(st.stack_targets, st.stack_targets)
    st2 = hq.get_quality_state("c13b")
    diff = "\n".join(["--- a", "+++ b"] + [f"-old{i}" for i in range(40)] + ["+keep"])
    hq._maybe_flag_large_delete(st2, "x.py", diff)
    _assert(st2.pending_diagnose_red is True, st2)


def case_14_ephemeral_claim_review():
    _clear()
    cid = "c14"
    td = tempfile.mkdtemp()
    fp = Path(td) / "ok.py"
    fp.write_text("x = 1\n", encoding="utf-8")
    hq.build_post_write_quality_report(cid, {str(fp): "t"})
    # 禁止独立消息灌入
    _assert(hq.build_quality_ephemeral_messages(cid) == [], "ephemeral must be empty")
    _assert(hq.build_post_write_quality_messages(cid, {str(fp): "t"}) == [], "no system dump")
    hq.note_assistant_claim_fixed(cid, "修复完成，已修复")
    _assert(hq.get_quality_state(cid).claim_fixed_blocked is True, "flag only, no message")
    hq.note_assistant_may_complete_review(
        cid, "review结论：①无遗漏 ②风格一致 ③无调试残留 ④边界OK ⑤单文件无需交叉。"
    )
    _assert(hq.get_quality_state(cid).reviewed_ok is True, "review should be done")
    # agent_turn 不得再拼 ephemeral
    from agent_v4.core import agent_turn as at
    src = Path(at.__file__).read_text(encoding="utf-8")
    _assert("build_quality_ephemeral_messages" not in src, "agent_turn must not inject ephemeral msgs")


def case_15_preview_path_norm_and_plan_dryrun():
    _clear()
    from agent_v4.core import agent_turn as at

    previewed, written = {}, {}
    r = at._check_write_preview(
        "replace_in_file.py",
        {"path": r"D:\proj\x.py", "dry_run": True},
        "t",
        previewed,
        written,
    )
    _assert(r is None, r)
    _assert("D:/proj/x.py" not in previewed, previewed)
    previewed["D:/proj/x.py"] = "t"
    r = at._check_write_preview(
        "replace_in_file.py",
        {"path": "D:/proj/x.py", "dry_run": False},
        "t",
        previewed,
        written,
    )
    _assert(r is None, r)

    previewed2 = {}
    cid = "c15"
    exec_args = {"path": r"D:\proj\y.py", "dry_run": True, "new_text": "a"}
    result = {"ok": True, "data": {"dry_run": True}}
    hq.note_write_tool_result(cid, "replace_in_file.py", exec_args, result)
    _pp = hq._norm_path(str(exec_args.get("path") or "").strip())
    previewed2[_pp] = "plan-dry"
    r = at._check_write_preview(
        "replace_in_file.py",
        {"path": "D:/proj/y.py", "dry_run": False, "new_text": "a"},
        "t",
        previewed2,
        {},
    )
    _assert(r is None, r)
    r = hq.check_pre_write_quality(
        cid,
        "replace_in_file.py",
        {"path": "D:/proj/y.py", "dry_run": False, "new_text": "a"},
    )
    _assert(r is None, r)


def case_17_review_red_no_deadlock_and_dest_path():
    """review 完成后红灯仍在时，应仍可改 fixable 路径；dest_path 可解析。"""
    _clear()
    cid = "c17"
    td = tempfile.mkdtemp()
    bad = Path(td) / "broken.py"
    bad.write_text("def z(\n", encoding="utf-8")
    hq.attach_host_quality_to_write_result(
        cid, str(bad), {"ok": True, "data": {"written": True, "path": str(bad)}}
    )
    st = hq.get_quality_state(cid)
    _assert(st.pending_diagnose_red is True, st)
    _assert(st.fixable_paths, st.fixable_paths)
    hq.note_assistant_may_complete_review(
        cid, "review结论：结构仍有问题，将继续修复语法错误，暂不扩大改动。"
    )
    st = hq.get_quality_state(cid)
    _assert(st.reviewed_ok is True, st)
    _assert(st.pending_diagnose_red is True, "red should remain")
    # 同文件应可继续改
    hq.note_evidence_tool(cid, "read_file.py", {"path": str(bad)}, {"ok": True})
    r = hq.check_pre_write_quality(
        cid,
        "replace_in_file.py",
        {"path": str(bad), "dry_run": False, "new_text": "def z():\n    return 1\n"},
    )
    _assert(r is None, r)
    # 无关文件仍拦
    r = hq.check_pre_write_quality(
        cid,
        "replace_in_file.py",
        {"path": str(Path(td) / "other.py"), "dry_run": False, "new_text": "x=1\n"},
    )
    _assert(r and r["error"]["type"] == "HostQualityDiagnoseRed", r)

    # dest_path 解析
    _clear()
    cid2 = "c17b"
    dp = str(Path(td) / "out.py")
    Path(dp).write_text("x=1\n", encoding="utf-8")
    hq.note_write_tool_result(
        cid2,
        "read_write.py",
        {"dest_path": dp, "dry_run": False},
        {"ok": True, "data": {"written": True, "dest_path": dp}},
    )
    st2 = hq.get_quality_state(cid2)
    _assert(hq._norm_path(dp) in st2.needs_reread, st2.needs_reread)

    # written=false 不置位
    _clear()
    cid3 = "c17c"
    hq.note_write_tool_result(
        cid3,
        "replace_in_file.py",
        {"path": "z.py", "dry_run": False, "new_text": "x"},
        {"ok": True, "data": {"written": False}},
    )
    _assert(hq.get_quality_state(cid3).turn_wrote is False, "no write no flag")


def case_18_truncate_keeps_host_quality():
    from agent_v4.core.tool_runtime import _truncate_tool_result

    huge = "x" * 50000
    result = {
        "ok": True,
        "data": {
            "diff_text": huge,
            "host_quality": {
                "overall": "red",
                "required_next_actions": ["fix a", "fix b"],
                "must_include_in_reply": "review结论",
                "checks": [
                    {"id": "auto_diagnose", "status": "fail", "summary": "bad", "detail": {"big": huge}},
                ],
                "model_instruction": "keep me",
            },
        },
        "host_quality_overall": "red",
    }
    s = _truncate_tool_result(result, max_chars=8000)
    _assert(isinstance(s, str), f"API content must be str, got {type(s)}")
    obj = json.loads(s)
    overall = obj.get("host_quality_overall")
    if overall is None and isinstance(obj.get("data"), dict):
        hq = obj["data"].get("host_quality") or {}
        overall = hq.get("overall")
    if overall is None and isinstance(obj.get("host_quality"), dict):
        overall = obj["host_quality"].get("overall")
    _assert(overall == "red", obj)


def case_19_attach_is_dict_before_model():
    """挂载在 dict 上完成；写入 tool content 前只 dumps 一次成 string。"""
    _clear()
    td = tempfile.mkdtemp()
    fp = Path(td) / "ok.py"
    fp.write_text("x=1\n", encoding="utf-8")
    raw = {"ok": True, "data": {"written": True, "path": str(fp), "diff_text": "+x=1\n"}}
    out = hq.attach_host_quality_to_write_result("c19", str(fp), raw)
    _assert(isinstance(out, dict), out)
    _assert(isinstance(out.get("data"), dict), out)
    _assert(isinstance(out["data"].get("host_quality"), dict), out)
    from agent_v4.core.tool_runtime import _truncate_tool_result

    stored = _truncate_tool_result(out)
    _assert(isinstance(stored, str), stored)
    parsed = json.loads(stored)
    _assert(isinstance(parsed, dict), parsed)
    _assert(
        (isinstance(parsed.get("data"), dict) and parsed["data"].get("host_quality"))
        or parsed.get("host_quality_overall")
        or parsed.get("host_quality"),
        parsed,
    )


def case_16_java_jdk_degrade_or_compile():
    _clear()
    td = tempfile.mkdtemp()
    jp = Path(td) / "Hello.java"
    jp.write_text(
        "public class Hello {\n  public static void main(String[] args) {\n    System.out.println(1);\n  }\n}\n",
        encoding="utf-8",
    )
    report = hq.build_post_write_quality_report("c16", {str(jp): "t"})
    checks = {c.get("id"): c for c in report.get("checks") or []}
    _assert("impact_verify_java" in checks, checks)
    jdk = hq._detect_jdk()
    if jdk.get("javac"):
        _assert(checks["impact_verify_java"]["status"] in ("pass", "fail", "degraded"), checks)
    else:
        _assert(checks["impact_verify_java"]["status"] == "degraded", checks)
        _assert("督办" in str(checks["impact_verify_java"].get("summary")), checks)
    bad = Path(td) / "Bad.java"
    bad.write_text("public class Bad { void x( {\n", encoding="utf-8")
    _clear()
    report2 = hq.build_post_write_quality_report("c16b", {str(bad): "t"})
    struct = [c for c in report2.get("checks") or [] if c.get("id") == "structure_integrity"]
    _assert(struct and struct[0].get("status") == "fail", report2)


CASES = [
    ("01_evidence_gate", case_01_evidence_gate),
    ("02_stack_first", case_02_stack_first),
    ("03_troubleshoot_lenses", case_03_troubleshoot_lenses),
    ("04_preview_fingerprint", case_04_preview_fingerprint),
    ("05_stale_edit_reread", case_05_stale_edit_and_reread),
    ("06_review_cross_file", case_06_review_and_cross_file),
    ("07_diagnose_filter_red", case_07_diagnose_filter_and_red),
    ("08_attach_tool_result", case_08_attach_to_tool_result),
    ("09_overwrite_large_diff", case_09_overwrite_and_large_diff),
    ("10_agent_turn_wiring", case_10_agent_turn_wiring_and_gates),
    ("11_dryrun_path_norm", case_11_dry_run_variants_and_path_norm),
    ("12_real_replace_postwrite", case_12_real_replace_fingerprint_and_postwrite),
    ("13_win_traceback_large_del", case_13_win_traceback_and_large_delete),
    ("14_ephemeral_claim_review", case_14_ephemeral_claim_review),
    ("15_preview_norm_plan_dryrun", case_15_preview_path_norm_and_plan_dryrun),
    ("16_java_jdk_degrade", case_16_java_jdk_degrade_or_compile),
    ("17_review_red_no_deadlock", case_17_review_red_no_deadlock_and_dest_path),
    ("18_truncate_keeps_hq", case_18_truncate_keeps_host_quality),
    ("19_attach_dict_before_model", case_19_attach_is_dict_before_model),
]


def run_one_round(round_idx: int) -> None:
    print(f"\n===== ROUND {round_idx} =====", flush=True)
    for name, fn in CASES:
        try:
            fn()
            print(f"  PASS {name}", flush=True)
        except Exception:
            print(f"  FAIL {name}", flush=True)
            traceback.print_exc()
            raise


def main() -> int:
    rounds = int(os.environ.get("HQ_VERIFY_ROUNDS", "10"))
    for i in range(1, rounds + 1):
        run_one_round(i)
    print(f"\nALL_GREEN rounds={rounds} cases={len(CASES)} version={AGENT_APP_VERSION}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
