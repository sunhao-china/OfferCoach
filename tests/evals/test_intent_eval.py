"""L4: 意图评估器本身的可测性 — 用桩模型验证准确率计算与逐例错误。"""

from __future__ import annotations

import pytest

from app.evaluation.intent import EvalCase, load_cases, run_eval


def test_accuracy_and_failures(registry, make_llm):
    cases = [
        EvalCase(id="1", message="帮我模拟面试", expected_intent="mock_interview"),  # 规则命中
        EvalCase(id="2", message="你好", expected_intent="chitchat"),  # 规则命中
        EvalCase(id="3", message="什么是红黑树", expected_intent="knowledge_qa"),  # LLM，桩答错
    ]
    llm = make_llm(['{"intent": "chitchat", "confidence": 0.9, "slots": {}}'])
    report = run_eval(llm, registry, cases)
    assert report.total == 3
    assert report.correct == 2
    assert report.accuracy == pytest.approx(2 / 3)
    assert len(report.failures) == 1
    assert report.failures[0].id == "3"
    assert report.failures[0].actual == "chitchat"


def test_load_cases_from_file(tmp_path):
    path = tmp_path / "intent.jsonl"
    path.write_text(
        '{"id": "a", "message": "你好", "expected_intent": "chitchat"}\n'
        '{"id": "b", "message": "模拟面试", "expected_intent": "mock_interview"}\n'
        "\n",
        encoding="utf-8",
    )
    cases = load_cases(path)
    assert len(cases) == 2
    assert cases[1].id == "b"


def test_empty_eval_has_zero_accuracy(registry, make_llm):
    report = run_eval(make_llm([]), registry, [])
    assert report.total == 0
    assert report.accuracy == 0.0
