"""L4: 会话总结器（AC2）。

依据 specs/02-in-progress/architecture/three-tier-memory.md 的 AC2。
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage

from app.core.embedding import DeterministicEmbedder
from app.memory.store import (
    COLLECTION_CANDIDATES,
    COLLECTION_LONG_TERM,
    COLLECTION_PROFILE,
    FakeMemoryStore,
)
from app.memory.summarizer import SessionSummarizer, build_excerpt
from tests.conftest import StubLLM

SUMMARY_JSON = json.dumps(
    {
        "topic": "Java 并发模拟面试",
        "importance": 0.8,
        "summary": {
            "key_content": [{"point": "线程池核心参数", "importance": 0.9}],
            "weak_points": ["对 CAS 原理理解浅"],
            "strengths": ["HashMap 扩容讲得清"],
            "next_practice_suggestions": ["练 JUC 源码"],
        },
        "profile_candidates": [
            {
                "category": "skill_weakness",
                "statement": "CAS 原理薄弱",
                "evidence": [{"session_id": "s1", "round": 2, "quote": "我不太清楚 CAS"}],
            }
        ],
    },
    ensure_ascii=False,
)


def _summarizer(store, responses, registry):
    return SessionSummarizer(store, DeterministicEmbedder(), StubLLM(responses), registry)


def _messages():
    return [
        HumanMessage(content="请讲讲 CAS 的原理"),
        AIMessage(content="我不太清楚 CAS"),
        AIMessage(content="那我们来聊聊线程池"),
    ]


def test_summarize_creates_long_term_and_candidate_points(registry):
    store = FakeMemoryStore()
    store.ensure_collections(32)
    summarizer = _summarizer(store, [SUMMARY_JSON], registry)
    result = summarizer.summarize("u1", "s1", "mock_interview", _messages(), now=1000.0)
    assert result["created"] is True
    point = store.get(COLLECTION_LONG_TERM, "u1:mock_interview:s1")
    assert point is not None
    payload = point["payload"]
    assert payload["topic"] == "Java 并发模拟面试"
    assert payload["importance"] == 0.8
    assert payload["status"] == "active"
    assert payload["session_type"] == "mock_interview"
    assert payload["session_id"] == "s1"
    assert payload["summary"]["weak_points"] == ["对 CAS 原理理解浅"]
    candidates = store.list_by_user(COLLECTION_CANDIDATES, "u1")
    assert len(candidates) == 1
    assert candidates[0]["payload"]["category"] == "skill_weakness"
    assert candidates[0]["payload"]["evidence"][0]["quote"] == "我不太清楚 CAS"
    assert store.list_by_user(COLLECTION_PROFILE, "u1") == []  # 1 条证据不晋升


def test_three_session_types_each_summarized(registry):
    store = FakeMemoryStore()
    store.ensure_collections(32)
    summarizer = _summarizer(store, [SUMMARY_JSON, SUMMARY_JSON, SUMMARY_JSON], registry)
    for session_type in ("chat", "mock_interview", "interview_review"):
        result = summarizer.summarize("u1", "s1", session_type, _messages(), now=1000.0)
        assert result["created"] is True
        assert store.get(COLLECTION_LONG_TERM, f"u1:{session_type}:s1") is not None


def test_summarize_idempotent_second_call_skipped(registry):
    store = FakeMemoryStore()
    store.ensure_collections(32)
    llm = StubLLM([SUMMARY_JSON])
    summarizer = SessionSummarizer(store, DeterministicEmbedder(), llm, registry)
    assert summarizer.summarize("u1", "s1", "chat", _messages(), now=1.0)["created"]
    second = summarizer.summarize("u1", "s1", "chat", _messages(), now=2.0)
    assert second == {"skipped": True, "reason": "exists"}
    assert len(llm.calls) == 1  # 第二次未触发 LLM
    assert len(store.list_by_user(COLLECTION_LONG_TERM, "u1")) == 1


def test_candidate_merge_promotes_on_second_session(registry):
    store = FakeMemoryStore()
    store.ensure_collections(32)
    second_json = json.dumps(
        {
            "topic": "CAS 复盘",
            "importance": 0.6,
            "summary": {
                "key_content": [],
                "weak_points": ["CAS 仍不熟"],
                "strengths": [],
                "next_practice_suggestions": [],
            },
            "profile_candidates": [
                {
                    "category": "skill_weakness",
                    "statement": "CAS 原理薄弱",
                    "evidence": [{"session_id": "s2", "round": 1, "quote": "CAS 我还没掌握"}],
                }
            ],
        },
        ensure_ascii=False,
    )
    summarizer = _summarizer(store, [SUMMARY_JSON, second_json], registry)
    assert summarizer.summarize("u1", "s1", "chat", _messages(), now=1.0)["created"]
    result = summarizer.summarize("u1", "s2", "chat", _messages(), now=2.0)
    assert result["created"] is True
    assert result["promoted"] == 1  # 同语句两场会话 → 证据合并 → 晋升
    assert store.list_by_user(COLLECTION_CANDIDATES, "u1") == []  # 候选已删
    profiles = store.list_by_user(COLLECTION_PROFILE, "u1")
    assert len(profiles) == 1
    assert len(profiles[0]["payload"]["evidence_chain"]) == 2


def test_parse_failure_falls_back_without_candidates(registry):
    store = FakeMemoryStore()
    store.ensure_collections(32)
    summarizer = _summarizer(store, ["这不是 JSON，模型犯迷糊了"], registry)
    result = summarizer.summarize("u1", "s1", "chat", _messages(), now=3.0)
    assert result["created"] is True
    point = store.get(COLLECTION_LONG_TERM, "u1:chat:s1")
    assert point["payload"]["importance"] == 0.2  # 降级重要性
    assert store.list_by_user(COLLECTION_CANDIDATES, "u1") == []


def test_summarize_empty_messages_skipped(registry):
    store = FakeMemoryStore()
    store.ensure_collections(32)
    summarizer = _summarizer(store, [], registry)
    assert summarizer.summarize("u1", "s1", "chat", [], now=1.0)["skipped"] is True


def test_build_excerpt_roles_and_caps():
    messages = [HumanMessage(content="问题" * 100)] * 45
    lines = build_excerpt(messages).split("\n")
    assert len(lines) == 40  # 条数上限
    assert lines[0].startswith("Human: ")
    assert len(lines[0]) <= 300 + len("Human: ")  # 单条长度上限
