# -*- coding: utf-8
"""agent_v3.core.tool_runtime"""
from __future__ import annotations

from agent_v3.core.deps import *  # noqa: F403
from agent_v3.core.shared_state import *  # noqa: F403
from agent_v3.core.shared_state import (
    _CATALOG_TOOL_DESCRIPTION_MAX_CHARS,
    _HOST_DRY_RUN_NOTICE_ZH,
    _TOOL_HELP_COMPACT_MAX_CHARS,
    _TOOL_HELP_MAX_CHARS,
)
from agent_v3.core.turn_control import (  # noqa: F401
    _turn_abort_requested,
    _user_stopped_tool_result_dict,
)

def _agent_main_param_name(raw_key: str) -> str:
    key = str(raw_key or "").strip().lstrip("-")
    return _camel_to_snake(key)

def _build_direct_preview_message(script_name: str, result: dict, user_text: str) -> Optional[str]:
    # 仅在用户明确提出“预览/原文”时，才允许把预览内容直出到主对话。
    if not _is_preview_intent(user_text):
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    sn = (script_name or "").lower()
    if ("replace_in_file" in sn or "write_file" in sn or "apply_patch" in sn):
        return None
    return None

def _camel_to_snake(name: str) -> str:
    name = str(name or "").strip().lstrip("-").replace("-", "_")
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

def _format_catalog_tool_examples(examples: Any, *, max_examples: int = 2) -> str:
    """将 tool_list_agent.json 中的 examples 并入工具 description / tool_help，便于模型对齐用法。"""
    if not isinstance(examples, list) or not examples:
        return ""
    if max_examples > 0:
        examples = examples[:max_examples]
    blocks: List[str] = []
    for i, ex in enumerate(examples, 1):
        if isinstance(ex, str) and str(ex).strip():
            blocks.append(f"示例{i}：\n{str(ex).strip()}")
            continue
        if not isinstance(ex, dict):
            continue
        title = str(ex.get("title") or ex.get("name") or f"示例{i}").strip()
        note = ex.get("note") or ex.get("description")
        note_s = str(note).strip() if note is not None else ""
        args = ex.get("args")
        lines = [f"示例{i}：{title}"]
        if note_s:
            lines.append(note_s)
        if isinstance(args, dict) and args:
            try:
                dumped = json.dumps(args, ensure_ascii=False, indent=2)
            except TypeError:
                dumped = str(args)
            lines.append("建议 arguments（键名与 function 参数一致，布尔用小写 true/false）：")
            lines.append(dumped)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

def _catalog_tool_full_description(entry: dict, script_fn: str) -> str:
    base = str(entry.get("purpose") or script_fn).strip()
    ext = entry.get("extended_description")
    if isinstance(ext, str) and ext.strip():
        base = f"{base}\n\n{ext.strip()}"
    ex_text = _format_catalog_tool_examples(entry.get("examples"))
    if ex_text:
        base = f"{base}\n\n—— 调用示例 ——\n{ex_text}"
    if len(base) > _CATALOG_TOOL_DESCRIPTION_MAX_CHARS:
        base = base[: _CATALOG_TOOL_DESCRIPTION_MAX_CHARS - 2] + "\n…"
    return base

def _capture_tool_help_from_catalog(script_name: str) -> Optional[str]:
    try:
        cat = load_catalog()
        for t in cat.get("tools", []):
            if str(t.get("name", "")).strip() == script_name:
                lines = [str(t.get("purpose", "")), "", "参数摘要:"]
                for a in t.get("args", []) or []:
                    lines.append(f'  {a.get("flag", "")} — {a.get("description", "")}')
                ex_text = _format_catalog_tool_examples(t.get("examples"))
                if ex_text:
                    lines.extend(["", "调用示例:", ex_text])
                return "\n".join(lines)
    except Exception:
        pass
    return None

def _capture_tool_help_from_module(mod: Any) -> Optional[str]:
    bp = getattr(mod, "build_parser", None)
    if not callable(bp):
        return None
    try:
        import tool_help_share as _ths

        return _ths.capture_help(bp())
    except Exception:
        return None

def _catalog_public_arg_names(script_name: str) -> Set[str]:
    out: Set[str] = {"step_title"}
    try:
        cat = load_catalog()
        for t in cat.get("tools", []):
            if str(t.get("name", "")).strip() != script_name:
                continue
            for a in t.get("args", []) or []:
                flag = str(a.get("flag", "")).strip()
                if not flag:
                    continue
                out.add(flag[2:] if flag.startswith("--") else flag)
            break
    except Exception:
        pass
    return out

def _chat_diff_markdown_for_tool(script_name: str, result: dict, exec_args: Dict[str, Any]) -> Optional[str]:
    sn = (script_name or "").lower()
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    if "text_diff" in sn:
        dm = data.get("diff_markdown")
        return dm if isinstance(dm, str) and dm.strip() else None
    if "skill_manage" in sn:
        notifs = data.get("notifications")
        if isinstance(notifs, list) and notifs:
            lines = []
            for n in notifs:
                if isinstance(n, str):
                    lines.append("> " + n)
            usage = data.get("side_usage")
            if usage and usage.get("total_tokens"):
                lines.append("> (旁支模型消耗 " + str(usage["total_tokens"]) + " tokens)")
            return "\n".join(lines)
        return None
    if "replace_in_file" in sn or "write_file" in sn or "apply_patch" in sn:
        dt = data.get("diff_text")
        if isinstance(dt, str) and dt.strip():
            path_hint = data.get("path") or exec_args.get("path") or exec_args.get("patch_file") or ""
            if path_hint:
                dt = _normalize_diff_display_headers(dt, str(path_hint))
            return "```diff\n" + dt + "\n```" if not dt.strip().startswith("```") else dt
        return None
    return None

def _coerce_tool_arguments_for_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    """将误传的 JSON 字符串解析为 list/dict，保证进程内 agent_main 收到 Python 原生类型。"""
    if not isinstance(args, dict):
        return {}
    out = dict(args)
    for k in _COERCE_JSON_CONTAINER_KEYS:
        v = out.get(k)
        if not isinstance(v, str):
            continue
        s = v.strip()
        if len(s) < 2 or s[0] not in "[{":
            continue
        try:
            parsed = json.loads(s)
        except Exception:
            continue
        out[k] = parsed
    _normalize_nested_tool_arg_keys(out)
    return out

def _execute_tool_agent_main(script_name: str, mod: Any, args: Dict[str, Any]) -> dict:
    """仅调用模块的 agent_main（不向 main()/CLI _stdout 降级）。"""
    import inspect

    fn = getattr(mod, "agent_main", None)
    if not callable(fn):
        return {
            "ok": False,
            "data": None,
            "error": {"type": "ToolError", "message": f"{script_name} 未定义可调用的 agent_main。"},
        }

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as e:
        return {
            "ok": False,
            "data": None,
            "error": {"type": "ToolError", "message": f"{script_name} agent_main 签名无效: {e}"},
        }

    params = sig.parameters
    kw_target_kinds = frozenset(
        {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    )
    accepted = {n for n, p in params.items() if p.kind in kw_target_kinds}
    varkw = next((n for n, p in params.items() if p.kind == inspect.Parameter.VAR_KEYWORD), None)

    arg_copy = dict(args)
    kwargs: Dict[str, Any] = {}
    unknown: List[str] = []

    for k, v in arg_copy.items():
        if v is None:
            continue
        pn = _agent_main_param_name(k)
        if pn == "json_out":
            continue
        # 宿主注入参数：有对应形参或 **kwargs 才传入，否则静默丢弃（避免 read_file 等无 varkw 工具报错）
        if pn in _HOST_INJECTED_TOOL_ARG_NAMES:
            if pn in accepted:
                kwargs[pn] = v
            elif varkw is not None:
                kwargs[pn] = v
            continue
        # 宿主注入的 _progress_dict 必须传入 agent_main，否则 file_search/grep_files 等无法上报进度
        if pn.startswith("_"):
            if pn in accepted:
                kwargs[pn] = v
            continue
        if pn in accepted:
            kwargs[pn] = v
            continue

        if varkw is not None:
            kwargs[pn] = v
            continue

        unknown.append(f"{str(k)!r}(→{pn})")

    if unknown:
        allow_list = sorted(x for x in accepted if x != "parser_for_help")
        return {
            "ok": False,
            "data": None,
            "error": {
                "type": "BadToolArguments",
                "message": (
                    f"{script_name} agent_main 不识别下列参数（请对照 function schema / tools/tool_list_agent.json）："
                    f"{', '.join(sorted(set(unknown)))}。"
                    f"允许的形参名：{', '.join(allow_list)}。"
                ),
            },
        }

    if "parser_for_help" in accepted and "parser_for_help" not in kwargs:
        build_parser = getattr(mod, "build_parser", None)
        if callable(build_parser):
            try:
                kwargs["parser_for_help"] = build_parser()
            except Exception:
                pass

    allow_list_hint = sorted(x for x in accepted if x != "parser_for_help")
    try:
        result = fn(**kwargs)
    except TypeError as e:
        return {
            "ok": False,
            "data": None,
            "error": {
                "type": "TypeError",
                "message": (
                    f"{script_name} agent_main 参数不匹配或缺失必填项：{e}；"
                    f"本轮已解析关键字={sorted(kwargs.keys())}；"
                    f"形参清单={allow_list_hint}"
                ),
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "data": None,
            "error": {"type": "ToolError", "message": f"{script_name} agent_main 执行异常: {e}"},
        }

    if isinstance(result, dict):
        return _strip_internal_tool_result(result)
    return {
        "ok": False,
        "data": None,
        "error": {"type": "ToolError", "message": f"{script_name} agent_main 须返回 dict，实际：{type(result).__name__}"},
    }

def _execute_tool_script_locked(script_name: str, args: Dict[str, Any]) -> dict:
    """execute_tool_script 的加锁实现；仅调用 agent_main，不劫持 sys.argv/stdout。"""
    import importlib

    script_path = _resolve_tool_script_path(script_name)
    if script_path is None:
        hint = [str(TOOLS_DIR / script_name)]
        try:
            c1 = [p.name for p in TOOLS_DIR.glob("*.py")]
            cands = sorted(set(c1))[:50]
            hint.append("\n\n工具脚本（节选）：" + ", ".join(cands))
        except Exception:
            pass
        return attach_tool_help_on_failure(
            script_name,
            None,
            {"ok": False, "data": None, "error": {"type": "ToolNotFound", "message": "\n".join(hint)}},
        )

    _ensure_tools_sys_path()

    # ── kling_generate 确认 ID 拦截（放在参数校验之前；catalog/agent_main 含 confirm_id 供模型重试） ──
    if script_name == "kling_generate.py":
        action = str(args.get("action", "") or "")
        if action in _KLING_GENERATE_ACTIONS:
            raw_cid = args.get("confirm_id")
            has_cid = raw_cid is not None and str(raw_cid).strip() != ""
            if has_cid:
                confirm_id = str(raw_cid).strip()
                info = consume_confirm_id(confirm_id)
                if info is None:
                    # 尝试自动确认（ID 存在但未确认时，直接确认后消耗）
                    try:
                        from agent_v3.live_state import mark_confirmed
                        mark_confirmed(confirm_id)
                    except Exception:
                        pass
                    info = consume_confirm_id(confirm_id)
                if info and info.get("action") == action:
                    args.pop("confirm_id", None)  # 消耗后移除，避免传给 agent_main
                    pass  # 拦截消耗通过
                else:
                    return {
                        "ok": False,
                        "data": None,
                        "error": {
                            "type": "E_INVALID_CONFIRM_ID",
                            "code": "E_INVALID_CONFIRM_ID",
                            "message": "确认ID无效或与请求的操作不匹配。请先通过 kling_generate 获取确认ID并完成确认。"
                        },
                    }
            else:
                import json as _json
                new_id = create_confirm_id(action, dict(args))
                cost_info = _kling_estimate_cost(action, args)
                action_cn = {"text2video":"文生视频","image2video":"图生视频","text2image":"文生图","image2image":"图生图"}.get(action, action)
                return {
                    "ok": False,
                    "data": {
                        "title": "确认使用可灵" + action_cn + "（" + cost_info + "）",
                        "confirms": ["确认生成", "取消"],
                        "confirm_id": new_id,
                        "preview": {"action": action, "estimated_cost": cost_info},
                    },
                    "error": {
                        "code": "E_USER_CONFIRM_REQUIRED",
                        "type": "UserConfirmRequired",
                        "message": (
                            "预览 - " + action + "\n"
                            "费用预估: " + cost_info + "\n\n"
                            "参数:\n" + _json.dumps(args, ensure_ascii=False, indent=2) + "\n\n"
                            "确认ID: " + new_id + "\n\n"
                            "此操作需要用户确认。"
                        ),
                        "hint": "前端弹窗显示 title/confirms；用户确认后传入 confirm_id 重新调用 kling_generate",
                        "retryable": False,
                    },
                }

    public_arg_error = _validate_public_tool_args(script_name, args)
    if public_arg_error is not None:
        return attach_tool_help_on_failure(script_name, None, public_arg_error)

    args = _coerce_tool_arguments_for_agent(args)
    mod_name = script_name.replace('.py', '')

    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:
        return attach_tool_help_on_failure(
            script_name,
            None,
            {"ok": False, "data": None, "error": {"type": "ImportError", "message": f"进程内加载 {script_name} 失败: {e}"}},
        )

    agent_result = _execute_tool_agent_main(script_name, mod, args)
    return attach_tool_help_on_failure(script_name, mod, agent_result)

def _execute_tool_script_stoppable(
    conversation_id: str,
    run_id: str,
    script_name: str,
    exec_args: Dict[str, Any],
    *,
    progress: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """线程执行工具并在轮询中响应用户停止（避免同步 execute 阻塞时无法检查 stop 标志）。"""
    if _turn_abort_requested(conversation_id, run_id):
        return _user_stopped_tool_result_dict()
    args = dict(exec_args)
    if progress is not None:
        args["_progress_dict"] = progress
    holder: Dict[str, Any] = {}
    wall_deadline = time.monotonic() + _tool_host_wall_timeout_sec(script_name, args)
    kill_sent = False

    def _run() -> None:
        try:
            holder["r"] = execute_tool_script(script_name, args, conversation_id=conversation_id)
        except Exception as exc:
            holder["r"] = {
                "ok": False,
                "data": None,
                "error": {"type": "ToolError", "message": str(exc)},
            }

    import threading as _thr

    t = _thr.Thread(target=_run, daemon=True)
    t.start()
    while t.is_alive():
        if _turn_abort_requested(conversation_id, run_id):
            if progress is not None:
                progress["_abort"] = True
            if script_name == "run_command.py":
                try:
                    from command_safety import force_kill_active_shell_process

                    force_kill_active_shell_process()
                except Exception:
                    pass
            for _ in range(40):
                if not t.is_alive():
                    break
                t.join(timeout=0.25)
            if t.is_alive():
                return _user_stopped_tool_result_dict()
            break
        if time.monotonic() >= wall_deadline:
            if not kill_sent and script_name == "run_command.py":
                try:
                    from command_safety import force_kill_active_shell_process

                    force_kill_active_shell_process()
                except Exception:
                    pass
                kill_sent = True
                wall_deadline = time.monotonic() + 20.0
            elif kill_sent and time.monotonic() >= wall_deadline:
                return {
                    "ok": False,
                    "data": {"exit_code": -1, "stdout": "", "stderr": "", "timeout": True},
                    "error": {
                        "type": "HostTimeout",
                        "message": "命令执行超过宿主等待上限，请点停止或重启服务；若 winget 仍在运行请在任务管理器中结束",
                    },
                }
        t.join(timeout=0.5)
    if _turn_abort_requested(conversation_id, run_id):
        if server_shutting_down() or "r" not in holder:
            return _user_stopped_tool_result_dict()
    got = holder.get("r")
    return got if isinstance(got, dict) else _user_stopped_tool_result_dict()

def _fenced_diff_from_unified_lines(lines: List[str]) -> str:
    body = "\n".join(lines)
    if len(body) > _CHAT_DIFF_BODY_MAX:
        body = body[:_CHAT_DIFF_BODY_MAX] + "\n…"
    return "```diff\n" + body + "\n```"


def _normalize_diff_display_headers(diff_text: str, display_path: str) -> str:
    """新建文件时 diff 常含 --- /dev/null；统一为 a/文件名 b/文件名 供前端标题解析。"""
    from pathlib import Path

    raw = str(diff_text or "")
    path_raw = str(display_path or "").strip()
    if not raw.strip() or not path_raw:
        return raw
    label = Path(path_raw.replace("\\", "/")).name
    if not label:
        return raw
    out: List[str] = []
    for line in raw.splitlines():
        if line.startswith("--- ") and "dev/null" in line.replace("\\", "/").lower():
            out.append(f"--- a/{label}")
        elif line.startswith("+++ "):
            out.append(f"+++ b/{label}")
        else:
            out.append(line)
    return "\n".join(out)


def _get_catalog_hints_system_prompt() -> str:
    global _CATALOG_HINTS_SYSTEM_CACHE
    if _CATALOG_HINTS_SYSTEM_CACHE is None:
        try:
            _CATALOG_HINTS_SYSTEM_CACHE = build_catalog_hints_system_prompt(load_catalog())
        except Exception:
            _CATALOG_HINTS_SYSTEM_CACHE = build_catalog_hints_system_prompt({})
    return _CATALOG_HINTS_SYSTEM_CACHE or ""

def _is_audit_only_intent(user_text: str) -> bool:
    t = str(user_text or "")
    if not t.strip():
        return False
    return any(k in t for k in AUDIT_INTENT_KEYS)

def _is_preview_intent(user_text: str) -> bool:
    t = str(user_text or "")
    return any(k in t for k in PREVIEW_INTENT_KEYS)

def _is_user_confirm_required(result: dict) -> bool:
    if not isinstance(result, dict) or result.get("ok"):
        return False
    err = result.get("error") or {}
    return err.get("code") == "E_USER_CONFIRM_REQUIRED"

def _kling_estimate_cost(action: str, args: dict) -> str:
    duration = str(args.get("duration", "5"))
    mode = str(args.get("mode", "std"))
    num_images = int(args.get("num_images", 1))
    if action in ("text2image", "image2image", "omni_image"):
        return "约 " + str(0.1 * num_images) + " 元"
    if action == "text2audio":
        return "按音频时长计费"
    if action in ("virtual_try_on",):
        return "按次计费"
    try:
        d = int(duration or 5)
    except ValueError:
        d = 5
    rate = {"std": 0.6, "pro": 0.8, "4k": 3.0}.get(mode, 0.6)
    return "约 " + str(rate * d) + " 元"

def _merge_confirm_into_user_confirm_args(exec_args: Dict[str, Any], confirm: str) -> Dict[str, Any]:
    """回填用户确认：仅扁平 confirm。"""
    out = dict(exec_args)
    out["confirm"] = confirm
    return out

def _normalize_nested_tool_arg_keys(out: Dict[str, Any]) -> None:
    """规范嵌套对象参数：将 rules 数组内键名转为 snake_case（与 agent_main 一致）。"""
    rules = out.get("rules")
    if isinstance(rules, list):
        norm_rules: List[Any] = []
        for item in rules:
            if not isinstance(item, dict):
                norm_rules.append(item)
                continue
            norm_rules.append(
                {
                    _agent_main_param_name(k): v
                    for k, v in item.items()
                }
            )
        out["rules"] = norm_rules

def _openai_tools_sort_key(t: dict) -> Tuple[int, str]:
    """OpenAI tools list order: shell 类工具排后（隐式降低被选概率）。"""
    name = str((t.get("function") or {}).get("name") or "")
    deprioritize = 1 if name in ("run_command", "python_inline") else 0
    return (deprioritize, name)

def _strip_internal_tool_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if not str(k).startswith("_")}

def _tool_host_wall_timeout_sec(script_name: str, exec_args: Dict[str, Any]) -> float:
    """宿主等待工具线程的上限（略大于 run_command 的 timeout_sec）。"""
    if script_name in ("run_command.py", "python_inline.py"):
        try:
            t = int(exec_args.get("timeout_sec") or 300)
        except (TypeError, ValueError):
            t = 300
        return float(max(5, min(t, 3600))) + 45.0
    return 900.0

def _tool_progress_sse_event(
    progress: Dict[str, Any],
    *,
    conversation_id: str,
    tool_call_id: str,
    script: str,
) -> Dict[str, Any]:
    if script in ("run_command.py", "python_inline.py"):
        if not isinstance(progress, dict):
            return {}
        tail = progress.get("stdout_tail") or ""
        awaiting = progress.get("awaiting_input")
        if not tail and not awaiting and not progress.get("_seq"):
            return {}
        try:
            from command_safety import (
                STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS,
                STREAM_OUTPUT_TAIL_MAX_CHARS,
            )
        except Exception:
            STREAM_OUTPUT_TAIL_MAX_CHARS = 12000
            STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS = 4000
        phase = "run_command" if script == "run_command.py" else "python_inline"
        ev: Dict[str, Any] = {
            "type": "tool_progress",
            "conversation_id": conversation_id,
            "tool_call_id": tool_call_id,
            "phase": phase,
            "stdout_tail": str(tail)[:STREAM_OUTPUT_TAIL_MAX_CHARS],
            "stderr_tail": str(progress.get("stderr_tail") or "")[:STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS],
            "elapsed_sec": progress.get("elapsed_sec"),
        }
        if isinstance(awaiting, dict) and awaiting:
            ev["awaiting_input"] = awaiting
            ck = progress.get("command_input_key")
            if ck:
                ev["command_input_key"] = ck
        return ev
    _sp = progress.get("scanned")
    if _sp is None:
        return {}
    _cf = progress.get("current_file", "")
    if not isinstance(_cf, str):
        _cf = str(_cf) if _cf is not None else ""
    return {
        "type": "tool_progress",
        "conversation_id": conversation_id,
        "tool_call_id": tool_call_id,
        "scanned": _sp,
        "current_file": _cf,
    }

def _truncate_large_values(d: dict, budget: int, level: int = 0) -> None:
    """递归截断 dict 中的大字符串/大列表（就地修改）。

    level=0 时单段 extract.text 优先占满 budget（预留 JSON 开销），避免数千字全文被误标「截断」；
    level>=1 强截断（200 字封顶）。
    host_quality 为宿主回灌关键字段：保留结构，仅压缩 detail/results 长文本。
    """
    limit = 200 if level >= 1 else max(budget // 4, 200)
    extract_text = level == 0 and str(d.get("type")) == "extract"
    for k, v in list(d.items()):
        if k == "host_quality" and isinstance(v, dict):
            _slim_host_quality_inplace(v, budget)
            continue
        if isinstance(v, str):
            eff = limit
            if level == 0 and k == "diff_markdown":
                eff = max(200, budget - 800)
            elif extract_text and k == "text":
                eff = max(200, budget - 800)
            if len(v) > eff:
                orig_len = len(v)
                d[k] = v[:eff] + f"\n[…截断，原文 {orig_len} 字]"
        elif isinstance(v, list) and v:
            sample = str(v[0]) if v else ""
            item_len = len(sample)
            keep_max = 5 if level >= 1 else max(1, budget // 3 // max(item_len, 1))
            if (item_len > 0 and len(v) * item_len > budget // 3) or level >= 1:
                keep = min(keep_max, len(v))
                tail = [] if keep >= len(v) else [f"[…剩余 {len(v) - keep} 项已截断]"]
                d[k] = v[:keep] + tail
        elif isinstance(v, dict):
            _truncate_large_values(v, budget, level)


def _slim_host_quality_inplace(hq: dict, budget: int) -> None:
    """压缩 host_quality，但保留 overall/checks 摘要/actions，避免回灌被截没。"""
    checks = hq.get("checks")
    if isinstance(checks, list):
        slim_checks = []
        for c in checks[:12]:
            if not isinstance(c, dict):
                continue
            item = {
                "id": c.get("id"),
                "status": c.get("status"),
                "summary": str(c.get("summary") or "")[:400],
            }
            if c.get("host_instruction"):
                item["host_instruction"] = str(c.get("host_instruction"))[:300]
            # detail/results 可能很大，只留短错误
            detail = c.get("detail")
            if isinstance(detail, dict):
                item["detail_summary"] = str((detail.get("data") or {}).get("summary") or detail.get("ok"))[:200]
            results = c.get("results")
            if isinstance(results, list):
                item["results"] = [
                    {
                        "path": r.get("path"),
                        "ok": r.get("ok"),
                        "check": r.get("check"),
                        "error": str(r.get("error") or "")[:300],
                    }
                    for r in results[:6]
                    if isinstance(r, dict)
                ]
            slim_checks.append(item)
        hq["checks"] = slim_checks
    actions = hq.get("required_next_actions")
    if isinstance(actions, list):
        hq["required_next_actions"] = [str(a)[:200] for a in actions[:10]]
    mi = hq.get("model_instruction")
    if isinstance(mi, str) and len(mi) > 500:
        hq["model_instruction"] = mi[:500] + "…"
    # 控制整体体积
    raw = json.dumps(hq, ensure_ascii=False)
    if len(raw) > max(4000, budget // 3):
        hq.pop("model_instruction", None)
        for c in hq.get("checks") or []:
            if isinstance(c, dict):
                c.pop("detail_summary", None)
                if isinstance(c.get("results"), list) and len(c["results"]) > 2:
                    c["results"] = c["results"][:2]


def _truncate_tool_result(result: dict, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """截断工具 result，最终以 JSON 字符串作为 tool message content（接口要求 string）。

    流程：先在 dict 上完成 host_quality 等字段挂载与截断，再 dumps 一次。
    禁止对已是字符串的 content 再拼接后再 dumps（双重转义）。
    """
    if not isinstance(result, dict):
        result = {"ok": False, "data": None, "error": {"type": "InvalidResult", "message": repr(result)}}

    raw = json.dumps(result, ensure_ascii=False)
    if len(raw) <= max_chars:
        return raw

    truncated = copy.deepcopy(result)
    data = truncated.get("data")
    if isinstance(data, dict):
        _truncate_large_values(data, max_chars, level=0)

    rebuilt = json.dumps(truncated, ensure_ascii=False)
    if len(rebuilt) <= max_chars:
        return rebuilt

    # 兜底：仍超限时至少保留宿主质量短摘要，避免回灌完全丢失
    hq = None
    if isinstance(result.get("data"), dict):
        hq = result["data"].get("host_quality")
    hq_brief = None
    if isinstance(hq, dict):
        hq_brief = {
            "overall": hq.get("overall"),
            "required_next_actions": (hq.get("required_next_actions") or [])[:6],
            "must_include_in_reply": hq.get("must_include_in_reply"),
            "checks": [
                {"id": c.get("id"), "status": c.get("status"), "summary": str(c.get("summary") or "")[:160]}
                for c in (hq.get("checks") or [])[:8]
                if isinstance(c, dict)
            ],
        }
    return json.dumps(
        {
            "ok": bool(result.get("ok")),
            "_truncated": True,
            "_notice": f"工具返回超出限额({max_chars}字符)，已截断。需完整内容请自行调用工具分批读取。",
            "result_len": len(raw),
            "host_quality_overall": result.get("host_quality_overall")
            or (hq.get("overall") if isinstance(hq, dict) else None),
            "host_quality": hq_brief,
        },
        ensure_ascii=False,
    )

def _validate_public_tool_args(script_name: str, args: Dict[str, Any]) -> Optional[dict]:
    public = _catalog_public_arg_names(script_name)
    if not public:
        return None
    bad: List[str] = []
    for k in args.keys():
        key = str(k).strip()
        if key.startswith("_"):
            continue
        bare = key[2:] if key.startswith("--") else key
        if bare in _HOST_INJECTED_TOOL_ARG_NAMES:
            continue
        if bare not in public:
            bad.append(bare)
    if not bad:
        rules = args.get("rules")
        if script_name == "replace_in_file.py" and isinstance(rules, list):
            nested_bad: List[str] = []
            for item in rules:
                if not isinstance(item, dict):
                    continue
                for nk in item.keys():
                    if str(nk) not in {"old_text", "new_text"}:
                        nested_bad.append(str(nk))
            if not nested_bad:
                return None
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "BadToolArguments",
                    "message": (
                        "replace_in_file.py 的 rules 只接受 old_text/new_text；"
                        f"不接受：{', '.join(sorted(set(nested_bad)))}。"
                    ),
                },
            }
        return None
    return {
        "ok": False,
        "data": None,
        "error": {
            "type": "BadToolArguments",
            "message": (
                f"{script_name} 不接受未公开参数：{', '.join(sorted(set(bad)))}。"
                f"请只使用 function schema / tools/tool_list_agent.json 中列出的参数。"
            ),
        },
    }

def api_function_name(script_name: str) -> str:
    return script_name[:-3] if script_name.endswith(".py") else script_name

def _compact_tool_help_for_error(err_type: str, message: str) -> bool:
    """常见参数/路径/门控错误只附简短说明，避免塞满 argparse。"""
    t = str(err_type or "").strip()
    msg = str(message or "")
    if t in {
        "Restricted",
        "invalid_args",
        "unknown_action",
        "missing_target",
        "missing_message",
        "missing_targets",
        "missing_requires_reply",
        "missing_conversation",
        "wait_without_request",
        "ModeConflict",
        "AuditOnly",
        "BadToolArguments",
        "ToolCallLimitReached",
        "OCRError",
        "MissingDependency",
    }:
        return True
    if t in ("FileNotFoundError", "PermissionError", "NotADirectoryError", "IsADirectoryError"):
        return True
    if t == "ValueError" and any(
        k in msg
        for k in (
            "缺少 path",
            "勿传",
            "废弃",
            "必须是目录",
            "不存在",
            "须为",
            "需要 ",
        )
    ):
        return True
    if t in ("OSError", "ToolError") and len(msg) < 500:
        return True
    return False


def attach_tool_help_on_failure(script_name: str, mod: Optional[Any], result: dict) -> dict:
    """工具返回 ok=false 时附带 tool_help；简单错误用精简版，复杂错误保留 catalog/argparse。"""
    if not isinstance(result, dict) or result.get("ok"):
        return result
    err = result.get("error")
    if not isinstance(err, dict):
        return result
    err_type = str(err.get("type") or "")
    err_msg = str(err.get("message") or "")
    compact = _compact_tool_help_for_error(err_type, err_msg)
    blocks: List[str] = []
    prior = err.get("tool_help")
    if isinstance(prior, str) and prior.strip():
        blocks.append(prior.strip())
    if compact:
        try:
            cat = load_catalog()
            for t in cat.get("tools", []):
                if str(t.get("name", "")).strip() == script_name:
                    purpose = str(t.get("purpose") or "").strip()
                    if purpose:
                        blocks.append(purpose[:500])
                    break
        except Exception:
            pass
        merged = "\n".join(blocks) if blocks else err_msg
        cap = _TOOL_HELP_COMPACT_MAX_CHARS
    else:
        h_cli = _capture_tool_help_from_module(mod) if mod is not None else None
        if h_cli:
            blocks.append("【--help】\n" + h_cli.strip()[:4000])
        h_cat = _capture_tool_help_from_catalog(script_name)
        if h_cat:
            blocks.append("【catalog】\n" + h_cat.strip()[:4000])
        if not blocks:
            blocks.append(
                f"未找到 {script_name} 的帮助；请核对 tools/tool_list_agent.json。"
            )
        merged = "\n\n".join(blocks)
        cap = _TOOL_HELP_MAX_CHARS
    if len(merged) > cap:
        merged = merged[:cap] + "\n…"
    return {**result, "error": {**err, "tool_help": merged}}

_READONLY_NO_RUN_TYPE_SCRIPTS: frozenset = frozenset(
    {
        "read_file.py",
        "glob_files.py",
        "grep_files.py",
        "find_in_file.py",
        "regex_locate.py",
        "file_search.py",
        "git_workspace.py",
        "web_fetch.py",
        "web_fetch_render.py",
        "unified_diagnose.py",
        "env_probe.py",
        "ip_geolocate.py",
        "open_meteo_weather.py",
        "data_table.py",
        "text_diff.py",
        "image_ocr.py",
        "session_send.py",
        "session_multisend.py",
        "session_broadcast.py",
        "session_wait.py",
        "session_list.py",
        "session_create.py",
    }
)

_READONLY_NO_RESTRICT_SCRIPTS: frozenset = _READONLY_NO_RUN_TYPE_SCRIPTS | frozenset(
    {
        "glob_files.py",
        "text_diff.py",
    }
)

_STEP_TITLE_SCHEMA_LINE = "可选 step_title（≤40 字中文侧栏标题），详见 agent_hints.step_title。"


def catalog_to_openai_tools(catalog: dict) -> Tuple[List[dict], Dict[str, str]]:
    """Return OpenAI-format tools + mapping api_name -> script filename (e.g. read_file.py)."""
    tools: List[dict] = []
    name_map: Dict[str, str] = {}
    for t in catalog.get("tools", []):
        if not isinstance(t, dict):
            continue
        fn = str(t.get("name") or "").strip()
        if not fn.endswith(".py"):
            continue
        api = api_function_name(fn)
        if api in name_map:
            continue
        name_map[api] = fn
        props: Dict[str, Any] = {}
        required: List[str] = []
        for arg in t.get("args", []):
            flag = str(arg.get("flag", ""))
            pname = flag[2:] if flag.startswith("--") else flag
            if pname == "run_type" and fn in _READONLY_NO_RUN_TYPE_SCRIPTS:
                continue
            if pname == "restrict_to_workspace" and fn in _READONLY_NO_RESTRICT_SCRIPTS:
                continue
            typ = arg.get("type", "string")
            desc = str(arg.get("description", flag))
            if typ == "integer":
                sch: Dict[str, Any] = {"type": "integer", "description": desc}
            elif typ == "boolean":
                sch = {"type": "boolean", "description": desc}
            elif typ == "enum":
                values = list(arg.get("values", []))
                sch = {"type": "string", "description": desc, "enum": values}
                if pname == "action" and len(values) == 1:
                    sch["default"] = values[0]
            elif typ == "array":
                arr_items = arg.get("array_items", {"type": "string"})
                sch = {"type": "array", "description": desc, "items": arr_items}
            elif typ in ("object", "json-object", "json_object"):
                sch = {"type": "object", "description": desc}
            else:
                sch = {"type": "string", "description": desc}
            props[pname] = sch
            arg_required = bool(arg.get("required"))
            if pname == "action" and typ == "enum" and len(arg.get("values", []) or []) == 1:
                arg_required = False
            if arg_required:
                required.append(pname)
        if "step_title" not in props:
            props["step_title"] = {
                "type": "string",
                "description": _STEP_TITLE_SCHEMA_LINE,
            }
        tools.append({
            "type": "function",
            "function": {
                "name": api,
                "description": _catalog_tool_full_description(t, fn),
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return tools, name_map

def execute_tool_script(script_name: str, args: Dict[str, Any], *, conversation_id: str = "") -> dict:
    """统一进程内执行工具（源码运行 / PyInstaller 打包后均走此路）"""
    # 黑名单工具拒绝脱离服务端直接调用
    if script_name in _RESTRICTED_TOOLS and not is_file_search_allowed(conversation_id):
        return attach_tool_help_on_failure(
            script_name,
            None,
            {"ok": False, "data": None, "error": {"type": "Restricted", "message": "file_search 禁止直接调用，请通过对话界面使用（支持实时进度展示）"}},
        )
    # ── 实时模式校验 ──
    if script_name in WRITE_TOOL_SCRIPTS:
        cid = str(conversation_id or "").strip()
        if cid:
            if CONVERSATION_AUDIT_ONLY.get(cid):
                return attach_tool_help_on_failure(
                    script_name,
                    None,
                    {
                        "ok": False,
                        "data": None,
                        "error": {"type": "AuditOnly", "message": _AUDIT_WRITE_BLOCK_MSG},
                    },
                )
    # 注入 run_type 给所有工具，让工具自己的检查逻辑决定是否拒绝
    cid = str(conversation_id or "").strip()
    if cid:
        mode = CONVERSATION_MODES.get(cid, "")
        if mode in ("plan", "execute"):
            if "run_type" not in args:
                args["run_type"] = mode
    if script_name in _SESSION_DROP_LEGACY_ACTION_SCRIPTS:
        args = dict(args)
        args.pop("action", None)
    cid = str(conversation_id or "").strip()
    lock = get_tool_exec_lock(cid) if cid else _TOOL_EXEC_LOCK
    with lock:
        if cid and "conversation_id" not in args:
            args["conversation_id"] = cid
        return _execute_tool_script_locked(script_name, args)

def load_catalog() -> dict:
    if not TOOL_LIST_JSON.exists():
        raise RuntimeError(f"missing {TOOL_LIST_JSON}")
    raw = TOOL_LIST_JSON.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"invalid JSON in {TOOL_LIST_JSON}: {e}"
        doc = getattr(e, "doc", None)
        pos = getattr(e, "pos", None)
        if isinstance(doc, str) and isinstance(pos, int):
            lo = max(0, pos - 60)
            hi = min(len(doc), pos + 60)
            snippet = doc[lo:hi].replace(chr(10), "\\n")
            msg += f"; context near {pos}: ...{repr(snippet)}..."
        raise RuntimeError(msg) from e

def maybe_attach_write_tool_host_dry_run_notice(
    script_name: str,
    result: Any,
    conversation_mode: str,
) -> Any:
    """写类工具成功返回且 data.dry_run 为预览时，向 data 写入 host_dry_run_notice；Plan 模式不加。"""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    data = result.get("data")
    if not isinstance(data, dict):
        return result
    if str(conversation_mode or "").strip().lower() == "plan":
        return result
    sn = script_name or ""
    if sn not in WRITE_TOOL_SCRIPTS:
        return result
    dr = data.get("dry_run")
    if dr is not True and dr != 1:
        return result
    if data.get("host_dry_run_notice"):
        return result
    out = dict(result)
    out_data = dict(data)
    out_data["host_dry_run_notice"] = _HOST_DRY_RUN_NOTICE_ZH
    out["data"] = out_data
    return out

def normalize_cli_args(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        key = str(k)
        if not key.startswith("--"):
            key = "--" + key
        out[key] = v
    return out

def preview_payload(d: dict, limit: int = 50000) -> str:
    """返回完整的 JSON，不截断。limit<=0 时不检查大小。"""
    if limit > 0:
        s = json.dumps(d, ensure_ascii=False)
        if len(s) <= limit:
            return json.dumps(d, ensure_ascii=False, indent=2)
    try:
        return json.dumps(d, ensure_ascii=False, indent=2)
    except Exception:
        return str(d)

def preview_tool_result(script_name: str, result: dict, text_limit: int = 12000) -> str:
    """SSE tool_end.preview：大字段单独截断并保持合法 JSON；run_command/python_inline 与 tool_progress 同为 12k 尾部。"""
    sn = (script_name or "").lower()
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        d_cmd = result["data"]
        if ("run_command" in sn or "python_inline" in sn) and isinstance(d_cmd.get("stdout"), str):
            out = d_cmd["stdout"]
            try:
                from command_safety import (
                    STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS,
                    STREAM_OUTPUT_TAIL_MAX_CHARS,
                    sanitize_command_output_for_display,
                )

                out_show = sanitize_command_output_for_display(
                    out, max_chars=min(text_limit, STREAM_OUTPUT_TAIL_MAX_CHARS)
                )
            except Exception:
                out_show = out if len(out) <= text_limit else out[:text_limit] + "\n…"
            slim_cmd: Dict[str, Any] = {
                "ok": bool(result.get("ok")),
                "data": {
                    "stdout": out_show,
                    "stdout_len": len(out),
                    "exit_code": d_cmd.get("exit_code"),
                    "timeout": d_cmd.get("timeout"),
                },
            }
            stderr = d_cmd.get("stderr")
            if isinstance(stderr, str) and stderr.strip():
                try:
                    slim_cmd["data"]["stderr"] = sanitize_command_output_for_display(
                        stderr, max_chars=STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS
                    )
                except Exception:
                    slim_cmd["data"]["stderr"] = stderr[:STREAM_OUTPUT_STDERR_TAIL_MAX_CHARS]
            if not result.get("ok") and isinstance(result.get("error"), dict):
                slim_cmd["error"] = result["error"]
            return json.dumps(slim_cmd, ensure_ascii=False)
    if isinstance(result, dict) and result.get("ok") and isinstance(result.get("data"), dict):
        d = result["data"]
        if "read_file" in sn and isinstance(d.get("content"), str):
            text = d["content"]
            snippet = text if len(text) <= text_limit else text[:text_limit] + "\n…"
            slim = {
                "ok": True,
                "data": {
                    "path": d.get("path"),
                    "content": snippet,
                    "truncated": bool(d.get("truncated")),
                    "total_chars_returned": d.get("total_chars_returned"),
                },
            }
            return json.dumps(slim, ensure_ascii=False)
        if "grep_files" in sn and isinstance(d.get("matches"), list):
            m = d["matches"][:80]
            slim = {
                "ok": True,
                "data": {
                    "match_count": d.get("match_count"),
                    "truncated": d.get("truncated"),
                    "matches": m,
                },
            }
            return json.dumps(slim, ensure_ascii=False)
        if "regex_locate" in sn and isinstance(d.get("items"), list):
            items = d["items"]
            snippets = []
            for item in items[:30]:
                fp = item.get("file", "")
                ln = item.get("line", 0)
                col = item.get("column", 0)
                mt = item.get("match", "")
                context = ""
                try:
                    if fp and ln:
                        with open(fp, "r", encoding="utf-8", errors="replace") as _f:
                            _lines = _f.readlines()
                        if 1 <= ln <= len(_lines):
                            raw = _lines[ln - 1].rstrip("\r\n")
                            s = max(0, col - 1)
                            e = min(len(raw), s + len(mt))
                            marked = raw[:s] + "【" + raw[s:e] + "】" + raw[e:]
                            context = marked
                except Exception:
                    context = ""
                snippets.append(
                    "%s [%s,%s) %s:%d:%d %s"
                    % (
                        fp.split("/")[-1] if "/" in fp else fp.split("\\")[-1] if "\\" in fp else fp,
                        item.get("region_start", ""),
                        item.get("region_end", ""),
                        fp.split("/")[-1] if "/" in fp else fp.split("\\")[-1] if "\\" in fp else fp,
                        ln,
                        col,
                        context if context else mt,
                    )
                )
            slim = {
                "ok": True,
                "data": {
                    "type": "regex_locate",
                    "count": d["count"],
                    "snippets": snippets,
                },
            }
            return json.dumps(slim, ensure_ascii=False)
        if "text_diff" in sn and isinstance(d.get("summary"), dict):
            dm = d.get("diff_markdown")
            sm = d.get("summary")
            slim_dm = dm
            if isinstance(dm, str) and len(dm) > text_limit:
                slim_dm = dm[:text_limit] + "\n…"
            slim = {"ok": True, "data": {"summary": sm, "diff_markdown": slim_dm}}
            return json.dumps(slim, ensure_ascii=False)
    return preview_payload(result)

