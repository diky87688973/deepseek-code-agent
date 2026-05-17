# -*- coding: utf-8 -*-
"""ContextState.java 的 Python 落地：Round / ContextChunk / ContextManager。

与 deepseek_code_agent2 配合：
- 展平 / tryLoadPending：与 _build_api_messages_for_model、_merge_pending_excerpts 同语义；
- 摘要调度：dialogue_summary_excerpt_half_open_for_messages、adjust_excerpt_range_half_open 由本模块提供，
  阈值与区间一律经 ContextManager 入口（_maybe_schedule_summarization）。
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional, Tuple

# 块名与 ContextState.java / 前端 context_layout 标签一致
CHUNK_SYSTEM = "系统提示词"
CHUNK_KB = "知识库"
CHUNK_MEM = "记忆文件"
CHUNK_PURE = "远期记忆"
CHUNK_FULL = "近期记忆"
CHUNK_MODE = "模式"
CHUNK_ORDER = (CHUNK_SYSTEM, CHUNK_KB, CHUNK_MEM, CHUNK_PURE, CHUNK_FULL, CHUNK_MODE)


class Round:
    """一轮对话：从一条 user 起，到下一 user 之前（与伪代码 Round 一致，消息为 OpenAI dict）。"""

    __slots__ = ("messages", "round_id")

    def __init__(self, round_id: Optional[str] = None) -> None:
        self.messages: List[Dict[str, Any]] = []
        self.round_id = round_id

    def append_message_dict(self, msg: Dict[str, Any]) -> None:
        self.messages.append(msg)

    def append(self, role: str, content: str, **extra: Any) -> bool:
        m: Dict[str, Any] = {"role": role, "content": content}
        m.update(extra)
        self.messages.append(m)
        return True

    def calculate_tokens(self, approx_msg_fn: Callable[[Dict[str, Any]], int]) -> int:
        return sum(approx_msg_fn(m) for m in self.messages)


class ContextChunk:
    """命名块；rounds 顺序 = 展平时该块内顺序。"""

    __slots__ = ("name", "file_dir", "rounds")

    def __init__(self, name: str, file_dir: Optional[str] = None) -> None:
        self.name = name
        self.file_dir = file_dir
        self.rounds: List[Round] = []

    def append(self, role: str, content: str, is_new_msg: bool, **extra: Any) -> bool:
        if is_new_msg or not self.rounds:
            self.rounds.append(Round())
        self.rounds[-1].append(role, content, **extra)
        return True

    def get_count(self) -> int:
        return len(self.rounds)

    def calculate_tokens(self, approx_msg_fn: Callable[[Dict[str, Any]], int]) -> int:
        t = 0
        for r in self.rounds:
            t += r.calculate_tokens(approx_msg_fn)
        return t

    def to_llm(self) -> List[Round]:
        return self.rounds


def _flat_messages_to_user_rounds(msgs: List[Dict[str, Any]]) -> List[Round]:
    """将扁平消息按 user 起轮切成 Round 列表（用于 extractOveredRounds 伪代码）。"""
    rounds: List[Round] = []
    i = 0
    n = len(msgs)
    while i < n:
        if msgs[i].get("role") != "user":
            i += 1
            continue
        r = Round()
        rid = str(msgs[i].get("_agent_round_id") or "").strip() or None
        if rid:
            r.round_id = rid
        while i < n:
            r.append_message_dict(msgs[i])
            i += 1
            if i < n and msgs[i].get("role") == "user":
                break
        rounds.append(r)
    return rounds


def _copy_round_list(src: List[Round], start: int, count: int) -> List[Round]:
    out: List[Round] = []
    end = min(len(src), start + max(0, count))
    for j in range(start, end):
        nr = Round(round_id=src[j].round_id)
        for m in src[j].messages:
            nr.messages.append(copy.deepcopy(m))
        out.append(nr)
    return out


def find_first_user_index(messages: List[Dict[str, Any]]) -> Optional[int]:
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            return i
    return None


def adjust_excerpt_range_half_open(messages: List[Dict[str, Any]], start: int, end: int) -> Tuple[int, int]:
    """0-based 半开区间 [start, end)。扩展以尽量覆盖完整 tool 链（与 deepseek_code_agent 现网一致）。"""
    n = len(messages)
    start = max(0, min(start, n))
    end = max(start, min(end, n))
    if start >= end:
        return start, end
    if start < n and messages[start].get("role") == "tool":
        t = start
        while t > 0 and messages[t - 1].get("role") == "tool":
            t -= 1
        if t > 0:
            prev = messages[t - 1]
            if prev.get("role") == "assistant" and prev.get("tool_calls"):
                start = t - 1
    i = start
    while i < end:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tc_list = m.get("tool_calls") or []
            need_ids = {
                str((tc or {}).get("id") or "")
                for tc in tc_list
                if isinstance(tc, dict) and (tc or {}).get("id")
            }
            need_ids.discard("")
            if not need_ids:
                i += 1
                continue
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                j += 1
            if j > end:
                end = min(j, n)
        i += 1
    return start, min(end, n)


def dialogue_summary_excerpt_half_open_for_messages(
    messages: List[Dict[str, Any]],
    full_user_rounds: int,
    pure_user_rounds: int,
) -> Optional[Tuple[int, int]]:
    """摘要截取区间 [start,end)：与现网 _dialogue_summary_excerpt_half_open 同语义，迁入 Context 模块。"""
    fu = find_first_user_index(messages)
    if fu is None:
        return None
    user_idxs = [i for i, m in enumerate(messages) if m.get("role") == "user" and i >= fu]
    k = len(user_idxs)
    fn = max(1, int(full_user_rounds))
    pn = max(0, int(pure_user_rounds))
    reserve = fn + pn
    if reserve <= 0:
        return None
    if k <= reserve:
        if k <= fn:
            return None
        end_cap = user_idxs[k - fn]
    else:
        end_cap = user_idxs[k - reserve]
    start0 = fu
    if start0 >= end_cap:
        return None
    s_adj, e_adj = adjust_excerpt_range_half_open(messages, start0, end_cap)
    e_adj = min(e_adj, end_cap)
    if s_adj >= e_adj:
        return None
    return s_adj, e_adj


class ContextManager:
    """会话级上下文管理（ContextState.java / ContextManager）。"""

    __slots__ = (
        "session_id",
        "chunks",
        "name_map",
        "pending_mem_file_path",
        "pending_over_rounds",
        "pure_user_rounds",
        "full_user_rounds",
        "summary_token_threshold",
        "_anchor_cache_ref",
        "_sys_base",
        "_kb_part",
        "_summaries",
        "_pure_folded",
        "_full_pre",
        "_full_suf",
        "_mode_tail",
    )

    def __init__(
        self,
        session_id: str,
        pure_user_rounds: int,
        full_user_rounds: int,
        summary_token_threshold: int,
        anchor_cache_ref: Dict[str, int],
    ) -> None:
        self.session_id = session_id
        self.pure_user_rounds = int(pure_user_rounds)
        self.full_user_rounds = int(full_user_rounds)
        self.summary_token_threshold = int(summary_token_threshold)
        self._anchor_cache_ref = anchor_cache_ref
        self.pending_mem_file_path: Optional[str] = None
        self.pending_over_rounds: Optional[List[Round]] = None
        self.chunks: List[ContextChunk] = []
        self.name_map: Dict[str, ContextChunk] = {}
        for nm in CHUNK_ORDER:
            c = ContextChunk(nm, None)
            self.chunks.append(c)
            self.name_map[nm] = c
        self._sys_base = ""
        self._kb_part = ""
        self._summaries: List[Dict[str, Any]] = []
        self._pure_folded: List[Dict[str, Any]] = []
        self._full_pre: List[Dict[str, Any]] = []
        self._full_suf: List[Dict[str, Any]] = []
        self._mode_tail: Dict[str, Any] = {}

    def add(self, chunk: ContextChunk) -> bool:
        """扩展点：除默认六块外追加自定义块（保持与伪代码 add 一致）。"""
        self.chunks.append(chunk)
        self.name_map[chunk.name] = chunk
        return True

    def append_new_msg(self, role: str, content: str, **extra: Any) -> bool:
        """新开一轮 Round；首条建议 role=user（见 ContextState.java 头注释）。"""
        return self.name_map[CHUNK_FULL].append(role, content, True, **extra)

    def append_msg(self, role: str, content: str, **extra: Any) -> bool:
        """追加到当前最后一轮。"""
        return self.name_map[CHUNK_FULL].append(role, content, False, **extra)

    def rebuild_from_persisted(
        self,
        persisted: List[Dict[str, Any]],
        build_segments_fn: Callable[[List[Dict[str, Any]], str], Tuple[Any, ...]],
        conversation_id: str,
    ) -> None:
        """用与现网 _build_context_segments 相同的拆分结果填充内部块与展平缓存。"""
        t = build_segments_fn(persisted, conversation_id)
        (
            sys_base,
            kb_part,
            summaries,
            pure_folded,
            full_pre,
            full_suf,
            _pure_user_turns,
            mode_tail,
        ) = t
        self._sys_base = str(sys_base or "")
        self._kb_part = str(kb_part or "")
        self._summaries = list(summaries or [])
        self._pure_folded = list(pure_folded or [])
        self._full_pre = list(full_pre or [])
        self._full_suf = list(full_suf or [])
        self._mode_tail = dict(mode_tail or {})

        # --- 将展平源数据映射到 ContextChunk.rounds（便于 extractOveredRounds 按「轮」统计）---
        self.name_map[CHUNK_SYSTEM].rounds.clear()
        self.name_map[CHUNK_KB].rounds.clear()
        self.name_map[CHUNK_MEM].rounds.clear()
        self.name_map[CHUNK_PURE].rounds.clear()
        self.name_map[CHUNK_FULL].rounds.clear()
        self.name_map[CHUNK_MODE].rounds.clear()

        if self._sys_base.strip():
            r = Round()
            r.append("system", self._sys_base)
            self.name_map[CHUNK_SYSTEM].rounds.append(r)
        if self._kb_part.strip():
            r = Round()
            r.append("system", self._kb_part)
            self.name_map[CHUNK_KB].rounds.append(r)
        for sm in self._summaries:
            r = Round()
            r.append_message_dict(copy.deepcopy(sm))
            self.name_map[CHUNK_MEM].rounds.append(r)
        for pr in _flat_messages_to_user_rounds(self._pure_folded):
            self.name_map[CHUNK_PURE].rounds.append(pr)
        for pr in _flat_messages_to_user_rounds(self._full_pre + self._full_suf):
            self.name_map[CHUNK_FULL].rounds.append(pr)
        if self._mode_tail:
            r = Round()
            r.append_message_dict(copy.deepcopy(self._mode_tail))
            self.name_map[CHUNK_MODE].rounds.append(r)

    def flatten_to_api_messages(
        self, sanitize_fn: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """等同 _build_api_messages_for_model：系统+KB 合并为首条 system，再 summaries + full_pre + pure + full_suf + mode。"""
        sys_content = self._sys_base
        if self._kb_part:
            sys_content += "\n\n" + self._kb_part
        prefix = [{"role": "system", "content": sys_content}]
        tail = list(self._full_pre) + list(self._pure_folded) + list(self._full_suf)
        built = prefix + list(self._summaries) + tail + [copy.deepcopy(self._mode_tail)]
        return sanitize_fn(built)

    def to_llm(self, sanitize_fn: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]) -> str:
        """伪代码返回 JSON 字符串；此处提供同名方法供调试。生产路径请用 flatten_to_api_messages。"""
        import json

        return json.dumps(self.flatten_to_api_messages(sanitize_fn), ensure_ascii=False)

    def total_token_estimate(self, approx_msg_fn: Callable[[Dict[str, Any]], int]) -> int:
        s = 0
        s += _approx_tokens_text_local(self._sys_base, approx_msg_fn)
        s += _approx_tokens_text_local(self._kb_part, approx_msg_fn)
        for m in self._summaries:
            s += approx_msg_fn(m)
        for m in self._pure_folded:
            s += approx_msg_fn(m)
        for m in self._full_pre:
            s += approx_msg_fn(m)
        for m in self._full_suf:
            s += approx_msg_fn(m)
        s += approx_msg_fn(self._mode_tail) if self._mode_tail else 0
        return int(s)

    def calculate_tokens(self, approx_msg_fn: Callable[[Dict[str, Any]], int]) -> Dict[str, int]:
        """各块 token 估算（与伪代码 calculateTokens map 对齐）。"""
        return {
            CHUNK_SYSTEM: _approx_tokens_text_local(self._sys_base, approx_msg_fn),
            CHUNK_KB: _approx_tokens_text_local(self._kb_part, approx_msg_fn),
            CHUNK_MEM: sum(approx_msg_fn(m) for m in self._summaries),
            CHUNK_PURE: sum(approx_msg_fn(m) for m in self._pure_folded),
            CHUNK_FULL: sum(approx_msg_fn(m) for m in self._full_pre) + sum(approx_msg_fn(m) for m in self._full_suf),
            CHUNK_MODE: approx_msg_fn(self._mode_tail) if self._mode_tail else 0,
        }

    def calculate_round_count(self) -> Dict[str, int]:
        """各块「轮」数（user 起算），与伪代码 getCount 对齐。"""
        return {c.name: c.get_count() for c in self.chunks}

    def extract_over_rounds(self) -> List[Round]:
        """与 ContextState.java extractOveredRounds 同构：按 pure+full 窗口从块上取应摘要的轮。"""
        pure = self.name_map[CHUNK_PURE]
        full = self.name_map[CHUNK_FULL]
        window = int(self.pure_user_rounds) + int(self.full_user_rounds)
        pure_size = len(pure.rounds)
        full_size = len(full.rounds)
        over: List[Round] = []

        if full_size > window:
            over.extend(_copy_round_list(pure.rounds, 0, pure_size))
            over.extend(_copy_round_list(full.rounds, 0, full_size - window))
            return over
        if full_size == window:
            over.extend(_copy_round_list(pure.rounds, 0, pure_size))
            return over
        if full_size < window:
            excess = full_size + pure_size - window
            if excess > 0:
                over.extend(_copy_round_list(pure.rounds, 0, excess))
                return over
        return over

    def put_new_mem_file(self, path: str, over_rounds: List[Round]) -> bool:
        self.pending_mem_file_path = path
        self.pending_over_rounds = over_rounds
        return True

    def try_load_pending_mem_file(
        self,
        messages: List[Dict[str, Any]],
        merge_fn: Callable[[str, List[Dict[str, Any]]], None],
    ) -> bool:
        """发 LLM 前调用：委托 merge_fn（与 _merge_pending_excerpts_for_conversation 同语义），再清空 pending。"""
        merge_fn(self.session_id, messages)
        self.pending_mem_file_path = None
        self.pending_over_rounds = None
        return True

    def dialogue_summary_excerpt_half_open(self, messages: List[Dict[str, Any]]) -> Optional[Tuple[int, int]]:
        """asyncExcerpt 前：在扁平 transcript 上计算半开区间（与现网摘要调度一致）。"""
        return dialogue_summary_excerpt_half_open_for_messages(
            messages, self.full_user_rounds, self.pure_user_rounds
        )

    def estimate_persisted_flat_token_total(
        self,
        messages: List[Dict[str, Any]],
        approx_msg_fn: Callable[[Dict[str, Any]], int],
    ) -> int:
        """整段会话持久化列表的 token 估算（用于摘要阈值，与 sum(_approx_tokens_message) 一致）。"""
        return sum(approx_msg_fn(m) for m in messages)


def _approx_tokens_text_local(s: str, approx_msg_fn: Callable[[Dict[str, Any]], int]) -> int:
    return approx_msg_fn({"role": "system", "content": str(s or "")})


def excerpt_slice_to_rounds(
    messages: List[Dict[str, Any]],
    full_user_rounds: int,
    pure_user_rounds: int,
) -> List[Round]:
    """由摘要半开区间得到 Round 列表（供 putNewMemFile / 校验与伪代码对齐）。"""
    rng = dialogue_summary_excerpt_half_open_for_messages(messages, full_user_rounds, pure_user_rounds)
    if not rng:
        return []
    s, e = int(rng[0]), int(rng[1])
    if s >= e or s >= len(messages):
        return []
    e = min(e, len(messages))
    return _flat_messages_to_user_rounds(messages[s:e])
