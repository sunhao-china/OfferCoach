"""L1: 题库查询层 — 过滤 / 关键词 / 排除 / 随机抽题 / 岗位别名。"""

from __future__ import annotations

import pytest

from app.question_bank.store import QuestionStore, normalize_position
from tests.support.question_bank import STORE_SAMPLE_RECORDS, make_record, make_store


@pytest.fixture
def store(tmp_path) -> QuestionStore:
    return make_store(tmp_path, STORE_SAMPLE_RECORDS)


def test_filter_by_category(store):
    rows = store.search(category="concurrent")
    assert len(rows) == 3
    assert all(r.category == "concurrent" for r in rows)


def test_filter_by_difficulty(store):
    rows = store.search(difficulty="entry")
    assert len(rows) == 3
    assert all(r.difficulty == "entry" for r in rows)


def test_filter_by_qtype(store):
    rows = store.search(qtype="coding")
    assert len(rows) == 1
    assert rows[0].question_id == "q_concurrent_002"


def test_combined_filters(store):
    rows = store.search(category="concurrent", difficulty="hard")
    assert len(rows) == 1
    assert rows[0].question_id == "q_concurrent_003"


def test_keywords_single(store):
    rows = store.search(keywords="线程池")
    assert {r.question_id for r in rows} == {"q_concurrent_001", "q_concurrent_003"}


def test_keywords_multiple_is_or_match(store):
    rows = store.search(keywords=["线程池", "索引"])
    assert {r.question_id for r in rows} == {"q_concurrent_001", "q_concurrent_003", "q_mysql_001"}


def test_no_match_returns_empty(store):
    assert store.search(keywords="不存在的关键词xyz") == []


def test_get_hit_and_miss(store):
    assert store.get("q_jvm_001").question_id == "q_jvm_001"
    assert store.get("q_nope_999") is None


def test_random_pick_respects_filters_and_excludes(store):
    for _ in range(5):
        row = store.random_pick(difficulty="hard")
        assert row is not None
        assert row.difficulty == "hard"


def test_random_pick_excludes_asked_ids(store):
    row = store.random_pick(
        category="concurrent", exclude_ids=["q_concurrent_001", "q_concurrent_002"]
    )
    assert row is not None
    assert row.question_id == "q_concurrent_003"


def test_random_pick_returns_none_when_exhausted(store):
    row = store.random_pick(
        category="concurrent",
        exclude_ids=["q_concurrent_001", "q_concurrent_002", "q_concurrent_003"],
    )
    assert row is None


# ---- 三期：岗位别名归一化 / 跨岗位检索 ----


def test_normalize_position_aliases():
    cases = {
        "Java 后端": "java_backend",
        "JAVA": "java_backend",
        "java_backend": "java_backend",
        "Python": "python_backend",
        " Py ": "python_backend",
        "Python 后端": "python_backend",
        "C++": "cpp_backend",
        "cpp": "cpp_backend",
        "C#": "csharp_backend",
        ".NET": "csharp_backend",
        "C": "c_backend",
        "c语言": "c_backend",
        "AI Agent": "ai_agent",
        "智能体": "ai_agent",
        "项目经理": "project_manager",
        "PM": "project_manager",
        "python_backend": "python_backend",
    }
    for raw, expected in cases.items():
        assert normalize_position(raw) == expected, raw


def test_normalize_position_unknown_passthrough():
    assert normalize_position("golang") == "golang"
    assert normalize_position("  数据  分析师  ") == "数据 分析师"


def test_search_all_positions_when_position_none(tmp_path):
    records = STORE_SAMPLE_RECORDS + [
        make_record(
            "python_basics",
            1,
            position="python_backend",
            source="ai_seed_v3",
            question="Python 的 GIL 是什么",
        )
    ]
    store = make_store(tmp_path, records)
    rows = store.search(keywords="GIL", position=None)
    assert [r.question_id for r in rows] == ["q_python_basics_001"]
    # 显式岗位过滤仍生效
    assert store.search(keywords="GIL", position="java_backend") == []
    assert store.search(keywords="GIL", position="python_backend")[0].question_id == (
        "q_python_basics_001"
    )
