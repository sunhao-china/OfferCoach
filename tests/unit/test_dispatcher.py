"""L1: 路由分发器 — 规则快路径 / 阈值 / 降级 / 钩子事件。"""

from __future__ import annotations

from app.router.dispatcher import RouteDispatcher
from app.schemas.route import Intent


def _dispatcher(registry, llm, bus):
    return RouteDispatcher(llm, registry, bus=bus)


def test_empty_message_is_chitchat_without_llm(registry, make_llm, bus):
    llm = make_llm([])
    decision = _dispatcher(registry, llm, bus).route("   ")
    assert decision.intent is Intent.CHITCHAT
    assert decision.source == "rule"
    assert decision.target_graph == "react_agent"
    assert llm.calls == []


def test_rule_hit_skips_llm(registry, make_llm, bus):
    llm = make_llm([])
    decision = _dispatcher(registry, llm, bus).route("帮我模拟面试，3年经验")
    assert decision.intent is Intent.MOCK_INTERVIEW
    assert decision.source == "rule"
    assert decision.confidence == 1.0
    assert decision.target_graph == "mock_interview"
    assert decision.slots.years == 3
    assert llm.calls == []


def test_rule_conflict_falls_back_to_llm(registry, make_llm, bus):
    llm = make_llm(['{"intent": "resume_analysis", "confidence": 0.9, "slots": {}}'])
    decision = _dispatcher(registry, llm, bus).route("先看看简历再模拟面试")
    assert decision.intent is Intent.RESUME_ANALYSIS
    assert decision.source == "llm"
    assert len(llm.calls) == 1


def test_high_confidence_llm_result_is_adopted(registry, make_llm, bus):
    llm = make_llm(['{"intent": "interview_review", "confidence": 0.9, "slots": {}}'])
    decision = _dispatcher(registry, llm, bus).route("给我分析分析昨天那场面试的情况")
    assert decision.intent is Intent.INTERVIEW_REVIEW
    assert decision.source == "llm"
    assert decision.target_graph == "interview_review"


def test_low_confidence_goes_to_clarify(registry, make_llm, bus):
    llm = make_llm(['{"intent": "mock_interview", "confidence": 0.4, "slots": {}}'])
    decision = _dispatcher(registry, llm, bus).route("随便说点什么")
    assert decision.intent is Intent.CLARIFY
    assert decision.source == "llm"
    assert decision.target_graph == "clarify"


def test_explicit_clarify_wins_even_with_high_confidence(registry, make_llm, bus):
    llm = make_llm(['{"intent": "clarify", "confidence": 0.9, "slots": {}}'])
    decision = _dispatcher(registry, llm, bus).route("帮我看看")
    assert decision.intent is Intent.CLARIFY
    assert decision.target_graph == "clarify"


def test_fallback_on_retry_exhaustion(registry, make_llm, bus):
    llm = make_llm(["坏", "还是坏"])
    decision = _dispatcher(registry, llm, bus).route("什么是红黑树")
    assert decision.intent is Intent.CLARIFY
    assert decision.source == "fallback"
    assert decision.confidence == 0.0
    assert len(bus.events["error"]) == 1


def test_route_decide_fired_on_every_path(registry, make_llm, bus):
    dispatcher = _dispatcher(registry, make_llm([]), bus)
    dispatcher.route("帮我模拟面试")  # 规则路径
    dispatcher.route("   ")  # 空白路径
    fired = bus.events["route.decide"]
    assert len(fired) == 2
    assert fired[0].metadata == {
        "intent": "mock_interview",
        "confidence": 1.0,
        "source": "rule",
        "target_graph": "mock_interview",
    }
    assert fired[1].metadata["intent"] == "chitchat"


def test_llm_slots_are_passed_through(registry, make_llm, bus):
    llm = make_llm(
        [
            '{"intent": "mock_interview", "confidence": 0.95, "slots": '
            '{"position": "java_backend", "years": 3, "round": "一面", "difficulty": "中等"}}'
        ]
    )
    decision = _dispatcher(registry, llm, bus).route("我下周要面字节后端")
    assert decision.slots.position == "java_backend"
    assert decision.slots.years == 3
    assert decision.slots.round == "一面"
    assert decision.slots.difficulty == "中等"
