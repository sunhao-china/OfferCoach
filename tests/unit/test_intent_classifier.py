"""L1: LLM 意图分类器 — 解析容错 / 重试 / 降级 / 错误钩子。"""

from __future__ import annotations

import pytest

from app.hooks.bus import HookBus
from app.router.intent import IntentClassifier, IntentParseError, parse_intent_json
from app.schemas.route import Intent

VALID = '{"intent": "knowledge_qa", "confidence": 0.9, "slots": {}}'


def test_parse_plain_json():
    result = parse_intent_json(VALID)
    assert result.intent is Intent.KNOWLEDGE_QA
    assert result.confidence == 0.9


def test_parse_json_with_code_fence():
    assert parse_intent_json(f"```json\n{VALID}\n```").intent is Intent.KNOWLEDGE_QA


def test_parse_json_with_surrounding_text():
    assert parse_intent_json(f"好的，分类结果是：{VALID}，请查收").intent is Intent.KNOWLEDGE_QA


def test_parse_empty_response_raises():
    with pytest.raises(IntentParseError):
        parse_intent_json("")


def test_parse_no_json_raises():
    with pytest.raises(IntentParseError):
        parse_intent_json("完全不是 JSON")


def test_classify_valid_response(registry, make_llm):
    llm = make_llm([VALID])
    classifier = IntentClassifier(llm, registry)
    result = classifier.classify("什么是红黑树")
    assert result is not None and result.intent is Intent.KNOWLEDGE_QA
    assert len(llm.calls) == 1


def test_classify_retries_then_succeeds(registry, make_llm):
    llm = make_llm(["不是 JSON", VALID])
    classifier = IntentClassifier(llm, registry)
    result = classifier.classify("什么是红黑树")
    assert result is not None and result.intent is Intent.KNOWLEDGE_QA
    assert len(llm.calls) == 2
    assert "重新" in llm.calls[1][-1]


def test_unknown_intent_value_triggers_retry(registry, make_llm):
    llm = make_llm(['{"intent": "unknown_intent", "confidence": 0.5, "slots": {}}', VALID])
    classifier = IntentClassifier(llm, registry)
    assert classifier.classify("x") is not None
    assert len(llm.calls) == 2


def test_confidence_out_of_range_triggers_retry(registry, make_llm):
    llm = make_llm(['{"intent": "chitchat", "confidence": 1.5, "slots": {}}', VALID])
    classifier = IntentClassifier(llm, registry)
    assert classifier.classify("x") is not None
    assert len(llm.calls) == 2


def test_retry_exhausted_returns_none_and_fires_error(registry, make_llm, bus: HookBus):
    llm = make_llm(["坏响应", "还是坏响应"])
    classifier = IntentClassifier(llm, registry, bus=bus)
    assert classifier.classify("x") is None
    assert len(llm.calls) == 2
    assert len(bus.events["error"]) == 1
    assert bus.events["error"][0].metadata["component"] == "IntentClassifier"


# ---- router/intent.v2（R4 上下文规则，react-b1-v2 规格模块三增补） ----


def test_history_summary_flows_into_prompt(registry, make_llm):
    """R4：dispatcher 传入的历史摘要渲染进 intent 提示词，分类器可见报告上下文。"""
    llm = make_llm([VALID])
    classifier = IntentClassifier(llm, registry)
    classifier.classify(
        "报告里说的量化项目经历怎么写？",
        history_summary="助手：……量化项目经历建议用 STAR 结构改写……",
    )
    rendered = llm.calls[0][-1]
    assert "量化项目经历建议用 STAR 结构改写" in rendered
    assert "报告里说的量化项目经历怎么写？" in rendered
    assert "只有用户提供了新的简历原文" in rendered  # v2 新增上下文规则
