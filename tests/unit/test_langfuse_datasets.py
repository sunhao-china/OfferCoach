"""Langfuse 测试集构建单测（AC3）：三数据源 + 幂等推送（零网络）。"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import Base, Question, build_engine
from app.observability.langfuse_datasets import (
    item_id,
    load_b1_qa_items,
    load_intent_items,
    load_judge_items,
    sync_dataset,
)
from tests.support.langfuse import FakeLangfuse

EVALS_DIR = Path(__file__).resolve().parents[2] / "data" / "evals"


def _engine_with(records: list[dict]) -> tuple:
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for record in records:
            session.add(Question(**record))
        session.commit()
    return engine


def _question(
    question_id: str,
    position: str,
    category: str,
    difficulty: str,
    *,
    points: list[str] | None = None,
) -> dict:
    return {
        "question_id": question_id,
        "position": position,
        "category": category,
        "difficulty": difficulty,
        "type": "knowledge",
        "question": f"{position}-{category}-{question_id} 的题面",
        "reference_points": points or [f"{question_id}-要点一", f"{question_id}-要点二"],
        "followups": [],
        "keywords": [],
        "source": "ai_seed_v3",
    }


# ---------- 数据源加载 ----------


def test_load_intent_items():
    items = load_intent_items(EVALS_DIR / "intent.jsonl")
    assert len(items) == 15
    first = items[0]
    assert first["input"] == {
        "user_message": "帮我模拟面试，Java 后端，3 年经验",
        "history_summary": "（无）",
    }
    assert first["expected_output"] == {"intent": "mock_interview"}
    assert first["metadata"]["source_id"] == "r01"
    for item in items:
        assert set(item["input"]) == {"user_message", "history_summary"}
        assert item["expected_output"]["intent"] in {
            "mock_interview",
            "resume_analysis",
            "interview_review",
            "knowledge_qa",
            "chitchat",
        }


def test_load_judge_items():
    items = load_judge_items(EVALS_DIR / "judge.jsonl")
    assert len(items) == 20
    for item in items:
        data = item["input"]
        assert set(data) == {
            "question",
            "reference_points",
            "bank_followups",
            "answer",
            "followups",
            "followup_count",
            "max_followups",
        }
        assert isinstance(data["reference_points"], list) and data["reference_points"]
        assert isinstance(data["bank_followups"], list)
        assert isinstance(data["followups"], list)
        assert isinstance(data["followup_count"], int)
        assert isinstance(data["max_followups"], int)
        assert item["expected_output"]["action"] in {"move_on", "followup", "end"}
        assert data["followup_count"] == len(data["followups"])
    # 覆盖三种期望动作；source_id 唯一
    actions = {item["expected_output"]["action"] for item in items}
    assert actions == {"move_on", "followup", "end"}
    ids = [item["metadata"]["source_id"] for item in items]
    assert len(set(ids)) == len(ids)


def test_load_judge_items_missing_field_raises(tmp_path):
    bad = tmp_path / "judge.jsonl"
    record = {"id": "x1", "question": "题", "expected_action": "end"}
    bad.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    try:
        load_judge_items(bad)
    except ValueError as exc:
        assert "answer" in str(exc)
    else:
        raise AssertionError("字段缺失未抛错")


def test_load_b1_qa_items_deterministic_and_complete():
    records = [
        _question("j1", "java_backend", "concurrent", "entry"),
        _question("j2", "java_backend", "concurrent", "medium"),
        _question("j3", "java_backend", "jvm", "hard"),
        _question("p1", "python_backend", "python_basics", "entry"),
        _question("p2", "python_backend", "python_basics", "medium"),
        _question("p3", "python_backend", "python_db", "hard"),
    ]
    engine = _engine_with(records)
    first = load_b1_qa_items(engine, target=6)
    second = load_b1_qa_items(engine, target=6)
    assert first == second
    assert len(first) == 6
    assert {item["input"]["position"] for item in first} == {"java_backend", "python_backend"}
    assert {item["metadata"]["difficulty"] for item in first} == {"entry", "medium", "hard"}
    for item in first:
        assert set(item["input"]) == {"question", "position"}
        assert isinstance(item["expected_output"]["reference_points"], list)
        assert item["metadata"]["question_id"]


def test_load_b1_qa_items_quota_follows_size():
    # java 12 题 vs python 4 题：target 6 → java 4 / python 2（最大余数法）
    records = [_question(f"j{i}", "java_backend", "concurrent", "entry") for i in range(1, 11)] + [
        _question("j11", "java_backend", "jvm", "hard"),
        _question("j12", "java_backend", "jvm", "hard"),
        *[_question(f"p{i}", "python_backend", "python_basics", "medium") for i in range(1, 5)],
    ]
    items = load_b1_qa_items(_engine_with(records), target=6)
    positions = [item["input"]["position"] for item in items]
    assert len(items) == 6
    assert positions.count("java_backend") == 4
    assert positions.count("python_backend") == 2


def test_load_b1_qa_items_empty_bank():
    assert load_b1_qa_items(_engine_with([])) == []


# ---------- 幂等推送 ----------


def test_item_id_stable_and_content_sensitive():
    base = {"input": {"a": 1}, "expected_output": {"b": 2}, "metadata": {"x": 1}}
    same = {"expected_output": {"b": 2}, "input": {"a": 1}}  # 键序不同
    changed = {"input": {"a": 1}, "expected_output": {"b": 3}}
    assert item_id(base) == item_id(same)
    assert item_id(base) != item_id(changed)
    assert len(item_id(base)) == 12


def test_sync_dataset_creates_and_pushes():
    client = FakeLangfuse()
    items = [
        {"input": {"user_message": "你好"}, "expected_output": {"intent": "chitchat"}},
        {"input": {"user_message": "面试"}, "expected_output": {"intent": "mock_interview"}},
    ]
    report = sync_dataset(client, "oc-intent-v1", items)
    assert report.created_dataset and report.pushed == 2 and report.skipped == 0
    dataset = client.get_dataset("oc-intent-v1")
    assert [item.id for item in dataset.items] == [item_id(i) for i in items]


def test_sync_dataset_idempotent():
    client = FakeLangfuse()
    items = [{"input": {"q": "1"}, "expected_output": {"a": "1"}}]
    sync_dataset(client, "ds", items)
    second = sync_dataset(client, "ds", items)
    assert second.created_dataset is False
    assert second.pushed == 0 and second.skipped == 1
    assert len(client.dataset_item_creates) == 1  # 零新增


def test_sync_dataset_only_pushes_missing():
    client = FakeLangfuse()
    old = [{"input": {"q": "1"}, "expected_output": {"a": "1"}}]
    new = old + [{"input": {"q": "2"}, "expected_output": {"a": "2"}}]
    sync_dataset(client, "ds", old)
    report = sync_dataset(client, "ds", new)
    assert report.pushed == 1 and report.skipped == 1
    assert len(client.dataset_item_creates) == 2
