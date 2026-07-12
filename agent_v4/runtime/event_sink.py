# -*- coding: utf-8 -*-
"""事件投递接口：不直接发全局 SSE（发通道仅 turn_runner）。"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional


class EventSink:
    """Runtime 内部发事件的薄接口；禁止直接写全局 SSE。"""

    def emit(self, ev: Dict[str, Any]) -> None:
        raise NotImplementedError


class NullEventSink(EventSink):
    """兼容保留：生产热路径已不再使用（_emit 直接 return）。"""

    def emit(self, ev: Dict[str, Any]) -> None:
        return


class YieldEventSink(EventSink):
    """收集事件供测试断言；从不写全局 SSE。勿用于生产热路径。"""

    def __init__(self) -> None:
        self._buf = []  # type: List[Dict[str, Any]]

    def emit(self, ev: Dict[str, Any]) -> None:
        if isinstance(ev, dict):
            self._buf.append(dict(ev))

    def drain(self) -> List[Dict[str, Any]]:
        out = self._buf
        self._buf = []
        return out

    def iter_drain(self) -> Iterator[Dict[str, Any]]:
        for ev in self.drain():
            yield ev


def optional_sink_emit(sink: Optional[EventSink], ev: Dict[str, Any]) -> None:
    if sink is not None:
        sink.emit(ev)
