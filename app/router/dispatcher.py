"""路由分发器：规则快路径 → LLM 分类 → 阈值/降级 → 钩子事件。"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.hooks.bus import EVENT_ROUTE_DECIDE
from app.prompts.registry import PromptRegistry
from app.router.intent import IntentClassifier
from app.router.routes import resolve_target
from app.router.rules import build_rule_decision, match_rules
from app.schemas.route import Intent, InterviewSlots, RouteDecision


class RouteDispatcher:
    """组合规则与 LLM 分类器，产出 RouteDecision 并触发 route.decide 钩子。"""

    def __init__(
        self,
        llm: Any,
        registry: PromptRegistry,
        *,
        bus: Any = None,
        confidence_threshold: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._llm = llm
        self._bus = bus
        threshold = (
            settings.intent_confidence_threshold
            if confidence_threshold is None
            else confidence_threshold
        )
        retries = settings.intent_max_retries if max_retries is None else max_retries
        self._threshold = threshold
        self._classifier = IntentClassifier(llm, registry, max_retries=retries, bus=bus)

    def route(self, user_message: str, history_summary: str = "", config=None) -> RouteDecision:
        if not user_message.strip():
            return self._publish(
                RouteDecision(
                    intent=Intent.CHITCHAT,
                    confidence=1.0,
                    slots=InterviewSlots(),
                    source="rule",
                    target_graph=resolve_target(Intent.CHITCHAT),
                )
            )

        match = match_rules(user_message)
        if match is not None and not match.ambiguous:
            assert match.rule is not None
            return self._publish(build_rule_decision(user_message, match.rule))

        result = self._classifier.classify(user_message, history_summary, config=config)
        if result is None:
            return self._publish(self._fallback(InterviewSlots(), 0.0))
        if result.intent is Intent.CLARIFY or result.confidence < self._threshold:
            return self._publish(
                RouteDecision(
                    intent=Intent.CLARIFY,
                    confidence=result.confidence,
                    slots=result.slots,
                    source="llm",
                    target_graph=resolve_target(Intent.CLARIFY),
                )
            )
        return self._publish(
            RouteDecision(
                intent=result.intent,
                confidence=result.confidence,
                slots=result.slots,
                source="llm",
                target_graph=resolve_target(result.intent),
            )
        )

    def _fallback(self, slots: InterviewSlots, confidence: float) -> RouteDecision:
        """分类器重试耗尽：降级 clarify。"""
        return RouteDecision(
            intent=Intent.CLARIFY,
            confidence=confidence,
            slots=slots,
            source="fallback",
            target_graph=resolve_target(Intent.CLARIFY),
        )

    def _publish(self, decision: RouteDecision) -> RouteDecision:
        if self._bus is not None:
            self._bus.fire(
                EVENT_ROUTE_DECIDE,
                payload=decision,
                metadata={
                    "intent": decision.intent.value,
                    "confidence": decision.confidence,
                    "source": decision.source,
                    "target_graph": decision.target_graph,
                },
            )
        return decision
