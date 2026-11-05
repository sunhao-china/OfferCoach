"""L1: 规则路由 — 命中 / 冲突 / 词边界 / 年限提取。"""

from __future__ import annotations

from app.router.rules import build_rule_decision, extract_years, match_rules
from app.schemas.route import Intent


def test_single_hit():
    match = match_rules("帮我模拟面试，Java 后端")
    assert match is not None and not match.ambiguous
    assert match.rule is not None and match.rule.intent is Intent.MOCK_INTERVIEW


def test_casefold_latin_match():
    match = match_rules("Resume 帮我看看")
    assert match is not None and not match.ambiguous
    assert match.rule is not None and match.rule.intent is Intent.RESUME_ANALYSIS


def test_latin_word_boundary_prevents_false_positive():
    # "hi" 是 "this" 的子串，词边界必须挡住
    assert match_rules("what is this about") is None


def test_conflicting_rules_are_ambiguous():
    match = match_rules("先看看简历再模拟面试")
    assert match is not None and match.ambiguous
    assert len(match.conflicts) == 2


def test_no_match_returns_none():
    assert match_rules("什么是红黑树") is None


def test_extract_years():
    assert extract_years("帮我模拟面试，3年经验") == 3
    assert extract_years("三年经验") is None
    assert extract_years("帮我模拟面试") is None


def test_rule_decision_shape():
    match = match_rules("帮我模拟面试，3 年经验")
    assert match is not None and match.rule is not None
    decision = build_rule_decision("帮我模拟面试，3 年经验", match.rule)
    assert decision.intent is Intent.MOCK_INTERVIEW
    assert decision.confidence == 1.0
    assert decision.source == "rule"
    assert decision.target_graph == "mock_interview"
    assert decision.slots.years == 3
    assert decision.slots.position is None
