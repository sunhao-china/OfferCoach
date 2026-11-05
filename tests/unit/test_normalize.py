"""L1: 二期规范化 — ID 确定性 / resume / 跨源去重 / cap / 失败收集 / 幂等重跑。

依据 specs/02-in-progress/question_bank/java-guide-import.md 的 AC3。
"""

from __future__ import annotations

import json

from app.question_bank.llm_json import parse_llm_json, validate_llm_output
from app.question_bank.md_extract import RawQuestion
from app.question_bank.normalize import make_question_id, normalize_candidates
from app.question_bank.validator import norm_text, validate_records
from tests.conftest import StubLLM


def make_candidate(category: str = "concurrent", question: str = "什么是线程池？", **overrides):
    fields = {
        "category": category,
        "question": question,
        "answer": "线程池是一组预创建的线程，通过复用线程降低创建销毁开销，"
        "核心参数包括核心线程数、最大线程数、队列容量与拒绝策略。",
    }
    fields.update(overrides)
    return RawQuestion(**fields)


def llm_json(**overrides) -> str:
    data = {
        "question": "什么是线程池？",
        "reference_points": ["核心参数", "拒绝策略", "参数之间的关系"],
        "followups": ["参数如何设置", "实际线上案例"],
        "difficulty": "medium",
        "type": "knowledge",
        "keywords": ["线程池", "拒绝策略", "核心参数"],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def render(c) -> str:
    return f"{c.question}|{c.answer}"


def test_make_question_id_deterministic_and_guide_pattern():
    qid = make_question_id("concurrent", "什么是线程池？")
    assert qid == make_question_id("concurrent", "什么是线程池？")
    assert qid.startswith("q_concurrent_g")
    assert len(qid.removeprefix("q_concurrent_g")) == 8
    # 空白差异不影响 ID（归一化后哈希），重跑稳定
    assert qid == make_question_id("concurrent", "什么 是 线程池？")


def test_normalize_writes_valid_record_and_reports(tmp_path):
    llm = StubLLM([llm_json()])
    candidate = make_candidate()
    report = normalize_candidates(
        llm,
        [candidate],
        existing_ids=set(),
        existing_questions=set(),
        cap=40,
        output_dir=tmp_path,
        render=render,
    )
    assert report.total == 1
    assert report.per_category == {"concurrent": 1}
    assert report.failures == []
    assert report.input_chars > 0 and report.output_chars > 0
    record = json.loads((tmp_path / "concurrent.jsonl").read_text(encoding="utf-8"))
    assert record["question_id"] == make_question_id("concurrent", "什么是线程池？")
    assert record["source"] == "java_guide"
    assert record["position"] == "java_backend"
    assert record["category"] == "concurrent"
    assert validate_records([record]) == []


def test_resume_skips_existing_and_written_ids_with_zero_llm_calls(tmp_path):
    candidate = make_candidate()
    qid = make_question_id(candidate.category, candidate.question)
    # 已入库：零 LLM 调用直接跳过
    report = normalize_candidates(
        StubLLM([]),
        [candidate],
        existing_ids={qid},
        existing_questions=set(),
        cap=40,
        output_dir=tmp_path,
        render=render,
    )
    assert report.total == 0 and report.skipped_existing == 1
    # 已写盘（上次运行产出但未入库）：同样跳过
    (tmp_path / "concurrent.jsonl").write_text(
        json.dumps({"question_id": qid}) + "\n", encoding="utf-8"
    )
    report = normalize_candidates(
        StubLLM([]),
        [candidate],
        existing_ids=set(),
        existing_questions=set(),
        cap=40,
        output_dir=tmp_path,
        render=render,
    )
    assert report.total == 0 and report.skipped_existing == 1


def test_dedup_against_existing_questions_by_norm_text(tmp_path):
    report = normalize_candidates(
        StubLLM([]),
        [make_candidate(question="什么是 线程池？")],
        existing_ids=set(),
        existing_questions={norm_text("什么是线程池？")},
        cap=40,
        output_dir=tmp_path,
        render=render,
    )
    assert report.total == 0 and report.skipped_duplicate == 1


def test_cap_stops_at_limit_per_category(tmp_path):
    llm = StubLLM([llm_json()])
    candidates = [make_candidate(question=f"问题 {i}？") for i in (1, 2)]
    report = normalize_candidates(
        llm,
        candidates,
        existing_ids=set(),
        existing_questions=set(),
        cap=1,
        output_dir=tmp_path,
        render=render,
    )
    assert report.total == 1
    assert report.skipped_cap == 1
    assert len(llm.calls) == 1  # 超 cap 的在 LLM 之前拦截


def test_invalid_llm_output_recorded_as_failure_and_not_written(tmp_path):
    llm = StubLLM(['{"question": "缺字段"}'])
    report = normalize_candidates(
        llm,
        [make_candidate()],
        existing_ids=set(),
        existing_questions=set(),
        cap=40,
        output_dir=tmp_path,
        render=render,
    )
    assert report.total == 0
    assert len(report.failures) == 1 and "缺少字段" in report.failures[0]
    assert (tmp_path / "concurrent.jsonl").read_text(encoding="utf-8") == ""


def test_cap_is_cumulative_across_reruns(tmp_path):
    # 首跑 cap=1：写 1 条、第 2 条超 cap；重跑预算 = cap - 已写盘 = 0，不再补发
    candidates = [make_candidate(question=f"问题 {i}？") for i in (1, 2)]
    first = normalize_candidates(
        StubLLM([llm_json()]),
        candidates,
        existing_ids=set(),
        existing_questions=set(),
        cap=1,
        output_dir=tmp_path,
        render=render,
    )
    assert first.total == 1 and first.skipped_cap == 1
    second = normalize_candidates(
        StubLLM([]),
        candidates,
        existing_ids=set(),
        existing_questions=set(),
        cap=1,
        output_dir=tmp_path,
        render=render,
    )
    assert second.total == 0 and second.skipped_cap == 1  # 仍被累计 cap 拦住，零 LLM 调用


def test_limit_stops_after_n_records(tmp_path):
    llm = StubLLM([llm_json(question="问题 1？"), llm_json(question="问题 2？")])
    candidates = [make_candidate(question=f"问题 {i}？") for i in (1, 2)]
    report = normalize_candidates(
        llm,
        candidates,
        existing_ids=set(),
        existing_questions=set(),
        cap=40,
        output_dir=tmp_path,
        render=render,
        limit=1,
    )
    assert report.total == 1
    assert len(llm.calls) == 1


def test_rerun_is_idempotent(tmp_path):
    candidate = make_candidate()
    first = normalize_candidates(
        StubLLM([llm_json()]),
        [candidate],
        existing_ids=set(),
        existing_questions=set(),
        cap=40,
        output_dir=tmp_path,
        render=render,
    )
    assert first.total == 1
    # 重跑：写入 ID 已在盘上，零 LLM 调用、不追加新行
    second = normalize_candidates(
        StubLLM([]),
        [candidate],
        existing_ids=set(),
        existing_questions=set(),
        cap=40,
        output_dir=tmp_path,
        render=render,
    )
    assert second.total == 0 and second.skipped_existing == 1
    lines = [
        line
        for line in (tmp_path / "concurrent.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1


def testparse_llm_json_strips_fences():
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_llm_json('```\n{"a": 1}\n```') == {"a": 1}
    assert parse_llm_json("不是 JSON") is None


def testvalidate_llm_output_bounds():
    assert validate_llm_output(json.loads(llm_json())) == []
    errors = validate_llm_output(json.loads(llm_json(reference_points=["a"])))
    assert any("reference_points" in e for e in errors)
    errors = validate_llm_output(json.loads(llm_json(keywords=["a", "b"])))
    assert any("keywords" in e for e in errors)
    errors = validate_llm_output(json.loads(llm_json(difficulty="简单")))
    assert any("difficulty 非法" in e for e in errors)
    errors = validate_llm_output(json.loads(llm_json(type="essay")))
    assert any("type 非法" in e for e in errors)
