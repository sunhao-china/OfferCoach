"""L1: 钩子事件总线 — 优先级 / 异常隔离 / 事件记录。"""

from __future__ import annotations

from app.hooks.bus import HookBus


def test_fire_executes_handlers_in_priority_order(bus: HookBus):
    order: list[str] = []
    bus.register("e", lambda event: order.append("low"), priority=50)
    bus.register("e", lambda event: order.append("high"), priority=10)
    bus.fire("e")
    assert order == ["high", "low"]


def test_handler_exception_is_isolated(bus: HookBus):
    results: list[str] = []

    def broken(event):
        raise RuntimeError("boom")

    bus.register("e", broken)
    bus.register("e", lambda event: results.append("ok"))
    bus.fire("e")
    assert results == ["ok"]
    assert len(bus.errors) == 1
    assert bus.errors[0][0] == "e"


def test_events_are_recorded_with_payload_and_metadata(bus: HookBus):
    bus.fire("route.decide", payload={"a": 1}, metadata={"intent": "mock_interview"})
    fired = bus.events["route.decide"]
    assert len(fired) == 1
    assert fired[0].payload == {"a": 1}
    assert fired[0].metadata == {"intent": "mock_interview"}
