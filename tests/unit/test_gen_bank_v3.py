"""L1: 三期题库生成 — 批量生成 / 续号 / resume / 去重 / 失败不重试 / 模板快照。

依据 specs/02-in-progress/question_bank/multi-position-expansion.md 的 AC3。
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, Question, build_engine
from app.question_bank.generate_v3 import PROMPT_GENERATE, _type_guidance, generate_v3
from tests.conftest import TEMPLATES_DIR, StubLLM
from tests.support.question_bank import make_record

POSITION = "python_backend"


def _batch(*questions, difficulty="entry") -> str:
    """构造一批合法 LLM 输出（questions 数组）。"""
    items = [
        {
            "question": q,
            "reference_points": ["要点一", "要点二", "要点三"],
            "followups": ["追问一", "追问二"],
            "difficulty": difficulty,
            "type": "knowledge",
            "keywords": ["kw1", "kw2", "kw3"],
        }
        for q in questions
    ]
    return json.dumps({"questions": items}, ensure_ascii=False)


def _engine(tmp_path: Path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _read_file(output_base: Path, position: str, category: str) -> list[dict]:
    path = output_base / f"v3_{position}" / f"{category}.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _db_count(engine) -> int:
    with Session(engine) as session:
        return session.execute(select(func.count()).select_from(Question)).scalar_one()


def test_generate_writes_sequential_records(tmp_path):
    """批次 JSON 解析 + 字段落盘 + q_{category}_{seq:03d} 顺序续号 + dry-run 校验。"""
    output_base = tmp_path / "out"
    llm = StubLLM(
        [_batch("Python 的 GIL 是什么", "装饰器的原理是什么"), _batch("深拷贝与浅拷贝的区别")]
    )
    report = generate_v3(
        output_base,
        TEMPLATES_DIR,
        positions=(POSITION,),
        categories=("python_basics",),
        batch_size=2,
        difficulty_counts={"entry": 3, "medium": 0, "hard": 0},
        llm=llm,
    )
    assert report.total == 3
    assert report.per_position[POSITION] == 3
    assert report.failures == []
    assert report.validated == 3
    records = _read_file(output_base, POSITION, "python_basics")
    assert [r["question_id"] for r in records] == [
        "q_python_basics_001",
        "q_python_basics_002",
        "q_python_basics_003",
    ]
    assert all(r["position"] == POSITION for r in records)
    assert all(r["category"] == "python_basics" for r in records)
    assert all(r["difficulty"] == "entry" for r in records)
    assert all(r["source"] == "ai_seed_v3" for r in records)
    assert all(len(r["reference_points"]) == 3 for r in records)


def test_resume_numbers_after_existing_and_skips_filled_slots(tmp_path):
    """已有 3 题（entry 已满）→ entry 槽位零 LLM 调用；medium 新题从 004 续号。"""
    output_base = tmp_path / "out"
    dir_path = output_base / f"v3_{POSITION}" / "python_basics.jsonl"
    dir_path.parent.mkdir(parents=True)
    existing = [
        {
            "question_id": f"q_python_basics_{seq:03d}",
            "position": POSITION,
            "category": "python_basics",
            "difficulty": "entry",
            "type": "knowledge",
            "question": f"已有题目 {seq}",
            "reference_points": ["a", "b", "c"],
            "followups": ["d", "e"],
            "keywords": ["k1", "k2", "k3"],
            "source": "ai_seed_v3",
        }
        for seq in (1, 2, 3)
    ]
    dir_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in existing) + "\n", encoding="utf-8"
    )
    llm = StubLLM([_batch("新题一枚", difficulty="medium")])
    report = generate_v3(
        output_base,
        TEMPLATES_DIR,
        positions=(POSITION,),
        categories=("python_basics",),
        batch_size=2,
        difficulty_counts={"entry": 3, "medium": 1, "hard": 0},
        llm=llm,
    )
    assert report.total == 1
    assert len(llm.calls) == 1  # entry 已满不调 LLM
    records = _read_file(output_base, POSITION, "python_basics")
    assert records[-1]["question_id"] == "q_python_basics_004"
    assert records[-1]["difficulty"] == "medium"


def test_rerun_fully_filled_is_zero_llm(tmp_path):
    output_base = tmp_path / "out"
    llm = StubLLM([_batch("占位1", "占位2")])
    first = generate_v3(
        output_base,
        TEMPLATES_DIR,
        positions=(POSITION,),
        categories=("python_basics",),
        batch_size=2,
        difficulty_counts={"entry": 2, "medium": 0, "hard": 0},
        llm=llm,
    )
    assert first.total == 2
    second = generate_v3(
        output_base,
        TEMPLATES_DIR,
        positions=(POSITION,),
        categories=("python_basics",),
        batch_size=2,
        difficulty_counts={"entry": 2, "medium": 0, "hard": 0},
        llm=llm,
    )
    assert second.total == 0
    assert len(llm.calls) == 1  # 重跑零 LLM 调用


def test_norm_text_dedup_across_categories(tmp_path):
    """跨文件去重：LLM 输出与另一类别已写盘题面重复 → skipped_duplicate 不落盘。"""
    output_base = tmp_path / "out"
    other = output_base / f"v3_{POSITION}" / "python_db.jsonl"
    other.parent.mkdir(parents=True)
    other.write_text(
        json.dumps(
            {
                "question_id": "q_python_db_001",
                "position": POSITION,
                "category": "python_db",
                "difficulty": "entry",
                "type": "knowledge",
                "question": "重复题面X",
                "reference_points": ["a", "b", "c"],
                "followups": ["d", "e"],
                "keywords": ["k1", "k2", "k3"],
                "source": "ai_seed_v3",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    llm = StubLLM([_batch("重复题面X"), _batch("全新题面Y")])
    report = generate_v3(
        output_base,
        TEMPLATES_DIR,
        positions=(POSITION,),
        categories=("python_basics", "python_db"),
        batch_size=1,
        difficulty_counts={"entry": 1, "medium": 0, "hard": 0},
        llm=llm,
    )
    assert report.total == 1
    assert report.skipped_duplicate == 1
    records = _read_file(output_base, POSITION, "python_basics")
    assert [r["question"] for r in records] == ["全新题面Y"]


def test_norm_text_dedup_against_db(tmp_path):
    """库内已有题面（如 seed）→ 生成输出重复时跳过。"""
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(Question(**make_record("java_basics", 1, question="库内已有题面Z")))
        session.commit()
    output_base = tmp_path / "out"
    llm = StubLLM([_batch("库内已有题面Z"), _batch("避开后的新题")])
    report = generate_v3(
        output_base,
        TEMPLATES_DIR,
        engine=engine,
        positions=(POSITION,),
        categories=("python_basics",),
        batch_size=1,
        difficulty_counts={"entry": 1, "medium": 0, "hard": 0},
        llm=llm,
    )
    assert report.total == 1
    assert report.skipped_duplicate == 1


def test_invalid_output_fails_without_retry_and_slot_aborts(tmp_path):
    """非法输出记失败不重试；连续 3 批无产出放弃该槽位（防死循环）。"""
    output_base = tmp_path / "out"
    llm = StubLLM(["不是 JSON", "也不是 JSON", "还是不是 JSON"])
    report = generate_v3(
        output_base,
        TEMPLATES_DIR,
        positions=(POSITION,),
        categories=("python_basics",),
        batch_size=2,
        difficulty_counts={"entry": 2, "medium": 0, "hard": 0},
        llm=llm,
    )
    assert report.total == 0
    assert len(llm.calls) == 3  # 不重试，槽位放弃
    assert any("连续 3 批无产出" in f for f in report.failures)


def test_wrong_difficulty_rejected(tmp_path):
    output_base = tmp_path / "out"
    llm = StubLLM([_batch("难度不符的题", difficulty="hard")] * 3)
    report = generate_v3(
        output_base,
        TEMPLATES_DIR,
        positions=(POSITION,),
        categories=("python_basics",),
        batch_size=1,
        difficulty_counts={"entry": 1, "medium": 0, "hard": 0},
        llm=llm,
    )
    assert report.total == 0
    assert len(llm.calls) == 3  # 不重试，槽位放弃
    assert any("难度不符" in f for f in report.failures)
    assert _read_file(output_base, POSITION, "python_basics") == []


def test_overproduction_truncated_to_batch(tmp_path):
    """LLM 超产（3 条 vs batch 2）截断，守住难度槽位契约。"""
    output_base = tmp_path / "out"
    llm = StubLLM([_batch("题1", "题2", "题3")])
    report = generate_v3(
        output_base,
        TEMPLATES_DIR,
        positions=(POSITION,),
        categories=("python_basics",),
        batch_size=2,
        difficulty_counts={"entry": 2, "medium": 0, "hard": 0},
        llm=llm,
    )
    assert report.total == 2
    assert len(_read_file(output_base, POSITION, "python_basics")) == 2


def test_type_guidance_scales_to_batch():
    mix = {"knowledge": 16, "scenario": 10, "coding": 10, "system_design": 4}
    guidance = _type_guidance(mix, 5)
    assert "knowledge 2 题" in guidance
    assert "system_design 1 题" in guidance
    assert sum(int(part.split()[1]) for part in guidance.split("、")) == 5
    assert _type_guidance({"knowledge": 0, "scenario": 0}, 5) == "不限定"


def test_generate_prompt_snapshot(snapshot, registry):
    rendered = registry.render(
        PROMPT_GENERATE,
        position="Python 后端",
        category="python_basics",
        difficulty="entry",
        count="5",
        type_guidance="knowledge 2 题、scenario 2 题、coding 1 题",
        existing="1. 已有题目 A",
    )
    assert rendered == snapshot
