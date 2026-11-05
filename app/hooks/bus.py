"""钩子事件总线：注册表式、按优先级链式执行、异常隔离（钩子异常不影响主流程）。"""

from __future__ import annotations

import traceback
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# 事件名常量
EVENT_ROUTE_DECIDE = "route.decide"
EVENT_ERROR = "error"
EVENT_MOCK_INTERVIEW_END = "mock_interview.end"
EVENT_RESUME_ANALYSIS_COMPLETE = "resume_analysis.complete"
EVENT_INTERVIEW_REVIEW_COMPLETE = "interview_review.complete"

Handler = Callable[["HookEvent"], Any]


@dataclass
class HookEvent:
    """一次钩子触发事件。"""

    name: str
    payload: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class HookBus:
    """事件总线：fire 按优先级执行处理器，处理器异常被记录并隔离。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[int, Handler]]] = defaultdict(list)
        self._events: dict[str, list[HookEvent]] = defaultdict(list)
        self._errors: list[tuple[str, str]] = []

    def register(self, event: str, handler: Handler, priority: int = 100) -> None:
        """注册处理器，priority 越小越先执行。"""
        self._handlers[event].append((priority, handler))

    def fire(
        self, event: str, payload: Any = None, metadata: dict[str, Any] | None = None
    ) -> list[Any]:
        hook_event = HookEvent(name=event, payload=payload, metadata=metadata or {})
        self._events[event].append(hook_event)
        results: list[Any] = []
        for _, handler in sorted(self._handlers[event], key=lambda item: item[0]):
            try:
                results.append(handler(hook_event))
            except Exception:
                self._errors.append((event, traceback.format_exc()))
        return results

    @property
    def events(self) -> dict[str, list[HookEvent]]:
        """已触发事件记录（测试断言用）。"""
        return dict(self._events)

    @property
    def errors(self) -> list[tuple[str, str]]:
        """被隔离的处理器异常列表。"""
        return list(self._errors)
