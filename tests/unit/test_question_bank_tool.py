"""L1: 题库检索工具 — 命中 / 空结果 / 参数过滤 / 跨岗位检索。"""

from __future__ import annotations

import json

import pytest

from app.tools.question_bank import (
    EMPTY_RESULT_TEXT,
    _retry_tokens,
    build_question_bank_tool,
)
from tests.support.question_bank import STORE_SAMPLE_RECORDS, make_record, make_store

EXPECTED_FIELDS = {
    "question_id",
    "category",
    "difficulty",
    "question",
    "reference_points",
    "followups",
}


@pytest.fixture
def tool(tmp_path):
    return build_question_bank_tool(make_store(tmp_path, STORE_SAMPLE_RECORDS))


def test_hit_returns_json_rows(tool):
    text = tool.invoke({"keywords": "线程池"})
    rows = json.loads(text)
    assert [r["question_id"] for r in rows] == ["q_concurrent_001", "q_concurrent_003"]
    assert set(rows[0]) == EXPECTED_FIELDS
    assert "线程池" in rows[0]["question"]


def test_empty_result_returns_hint(tool):
    assert tool.invoke({"keywords": "不存在的关键词xyz"}) == EMPTY_RESULT_TEXT


def test_category_and_difficulty_filters_apply(tool):
    text = tool.invoke({"keywords": "线程池", "category": "concurrent", "difficulty": "hard"})
    rows = json.loads(text)
    assert [r["question_id"] for r in rows] == ["q_concurrent_003"]


def test_filtered_out_returns_hint(tool):
    assert tool.invoke({"keywords": "线程池", "category": "mysql"}) == EMPTY_RESULT_TEXT


# ---- 三期：position 可选参数（省略 = 全岗位检索） ----


@pytest.fixture
def multi_tool(tmp_path):
    records = STORE_SAMPLE_RECORDS + [
        make_record(
            "python_basics",
            1,
            position="python_backend",
            source="ai_seed_v3",
            question="Python 的 GIL 是什么",
        ),
        make_record(
            "python_concurrent",
            1,
            position="python_backend",
            source="ai_seed_v3",
            question="asyncio 事件循环的工作原理",
        ),
    ]
    return build_question_bank_tool(make_store(tmp_path, records))


def test_position_omitted_searches_all_positions(multi_tool):
    text = multi_tool.invoke({"keywords": "GIL"})
    rows = json.loads(text)
    assert [r["question_id"] for r in rows] == ["q_python_basics_001"]


def test_position_spoken_alias_filters(multi_tool):
    text = multi_tool.invoke({"keywords": "GIL", "position": "Python"})
    rows = json.loads(text)
    assert [r["question_id"] for r in rows] == ["q_python_basics_001"]
    # 限定其他岗位时同关键词不再命中
    assert multi_tool.invoke({"keywords": "GIL", "position": "java_backend"}) == EMPTY_RESULT_TEXT


# ---- 三期：整句 keywords 兜底拆词（真实冒烟发现 cheap 模型把原句当 keywords 传入） ----


def test_retry_tokens_helper_edges():
    assert _retry_tokens("GIL") is None  # 精炼单关键词不折腾
    assert _retry_tokens("什么 是") is None  # 全是停用词
    assert _retry_tokens("Python 的 GIL 是什么？它为什么还存在？") == [
        "Python",
        "GIL",
        "它为什么还存在",
    ]


def test_sentence_keywords_retry_splits_and_hits(multi_tool):
    text = multi_tool.invoke({"keywords": "Python 的 GIL 是什么？它为什么还存在？"})
    rows = json.loads(text)
    assert [r["question_id"] for r in rows] == ["q_python_basics_001"]


def test_retry_drops_position_filter_when_primary_misses(multi_tool):
    # 主查询：position=java_backend 过滤掉唯一命中 → 拆词重试放弃过滤，全岗位命中
    text = multi_tool.invoke({"keywords": "GIL 是什么", "position": "java_backend"})
    rows = json.loads(text)
    assert [r["question_id"] for r in rows] == ["q_python_basics_001"]


def test_retry_still_misses_returns_hint(multi_tool):
    assert multi_tool.invoke({"keywords": "世界上 不存在的 概念"}) == EMPTY_RESULT_TEXT
