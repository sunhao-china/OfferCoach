"""练习计划服务层单测：计划 / 每日题单 / 逐题判分 / 进度统计（AC1-3）。

判分走 score.v2 模板（真实模板资产）+ StubLLM；时钟可注入（假时钟控制跨天）。
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, DrillAttempt, DrillPlan, DrillSet, build_engine
from app.drill.service import (
    DAILY_COUNT_MAX,
    EMPTY_BANK_TEXT,
    MISSING_PLAN_TEXT,
    DrillService,
)
from tests.support.question_bank import make_record, make_store

CLOCK = [datetime(2026, 10, 25, 9, 0)]

LONG_ANSWER = "这个回答覆盖了线程池的核心参数与拒绝策略的细节，并给出了生产实践建议"


def score_json(**overrides) -> str:
    item = {
        "question_id": "q_any",
        "correctness": 4,
        "depth": 4,
        "expression": 4,
        "comment": "整体不错",
        "strengths": ["结构清晰"],
        "weaknesses": ["深度不足"],
    }
    item.update(overrides)
    return json.dumps({"results": [item]}, ensure_ascii=False)


@pytest.fixture
def drill_env(tmp_path, registry):
    """会话库（含 drill 三表）+ 题库（4 条 python_backend + 1 条 java_backend/spring）+ 服务工厂。

    两个 LLM 桩分档注入：responses=判分（strong）、classify_responses=纠正分类（cheap），
    互不串用（误用零响应的那一档会立即 AssertionError）。
    """
    CLOCK[0] = datetime(2026, 10, 25, 9, 0)  # 每个用例重置假时钟
    engine = build_engine(f"sqlite:///{(tmp_path / 'sessions.db').as_posix()}")
    Base.metadata.create_all(engine)
    records = [
        make_record("python_basics", seq, position="python_backend") for seq in range(1, 5)
    ] + [make_record("spring", 1)]  # 默认 java_backend：纠正分类的落点岗位
    store = make_store(tmp_path, records)

    def make(responses: list[str], classify_responses: list[str] | None = None) -> DrillService:
        llm = SimpleNamespace(
            calls=[],
            invoke=lambda messages, **kwargs: _scripted_invoke(llm, messages, responses),
        )
        classify = SimpleNamespace(
            calls=[],
            invoke=lambda messages, **kwargs: _scripted_invoke(
                classify, messages, classify_responses or []
            ),
        )
        return DrillService(store, engine, registry, llm, classify, now=lambda: CLOCK[0])

    return SimpleNamespace(engine=engine, store=store, make=make, tmp_path=tmp_path)


def _scripted_invoke(llm, messages, responses):
    llm.calls.append(list(messages))
    if not responses:
        raise AssertionError("StubLLM 无可用响应（调用次数超过预设序列）")
    return SimpleNamespace(content=responses.pop(0))


def save_plan(service: DrillService, user_id: str = "u1", **overrides) -> dict:
    kwargs = {"position": "python_backend", "difficulty": "medium", "daily_count": 3}
    kwargs.update(overrides)
    return service.save_plan(user_id, **kwargs)


# ---- AC1：计划 ----


def test_save_and_get_plan_roundtrip(drill_env):
    service = drill_env.make([])
    saved = save_plan(service, position="Python", daily_count=5)
    assert saved == {"position": "python_backend", "difficulty": "medium", "daily_count": 5}
    assert service.get_plan("u1") == saved


def test_plan_all_position_tokens_normalize_to_none(drill_env):
    service = drill_env.make([])
    for token in ("全部", "all", "", "  "):
        assert save_plan(service, position=token)["position"] is None


def test_save_plan_validates_difficulty_and_count(drill_env):
    service = drill_env.make([])
    with pytest.raises(ValueError, match="难度"):
        save_plan(service, difficulty="easy")
    with pytest.raises(ValueError, match="每日题量"):
        save_plan(service, daily_count=0)
    with pytest.raises(ValueError, match="每日题量"):
        save_plan(service, daily_count=DAILY_COUNT_MAX + 1)


def test_save_plan_updates_existing_single_row(drill_env):
    service = drill_env.make([])
    save_plan(service)
    save_plan(service, difficulty="hard", daily_count=10)
    assert service.get_plan("u1") == {
        "position": "python_backend",
        "difficulty": "hard",
        "daily_count": 10,
    }
    with Session(drill_env.engine) as session:
        assert session.scalar(select(func.count()).select_from(DrillPlan)) == 1


def test_plan_isolated_per_user(drill_env):
    service = drill_env.make([])
    save_plan(service, user_id="u1")
    assert service.get_plan("u1") is not None
    assert service.get_plan("u2") is None


# ---- AC2：每日题单 ----


def test_today_set_picks_and_reuses_same_day(drill_env):
    service = drill_env.make([])
    save_plan(service, daily_count=3)
    first = service.today_view("u1")
    assert first["total"] == 3 and first["done"] == 0 and first["completed"] is False
    assert first["shortage"] is False and first["plan_date"] == "2026-10-25"
    assert len(first["remaining"]) == 3
    assert set(first["remaining"][0]) == {
        "question_id",
        "position",
        "category",
        "difficulty",
        "question",
    }
    # 同日复用：同一题单、同题序，不重抽
    again = service.today_view("u1")
    assert [r["question_id"] for r in again["remaining"]] == [
        r["question_id"] for r in first["remaining"]
    ]
    with Session(drill_env.engine) as session:
        assert session.scalar(select(func.count()).select_from(DrillSet)) == 1


def test_today_set_abandons_unfinished_old_sets(drill_env):
    service = drill_env.make([])
    save_plan(service, daily_count=3)
    day1 = service.today_view("u1")
    CLOCK[0] = datetime(2026, 10, 26, 9, 0)
    day2 = service.today_view("u1")
    with Session(drill_env.engine) as session:
        statuses = {row.plan_date: row.status for row in session.scalars(select(DrillSet))}
    assert statuses == {"2026-10-25": "abandoned", "2026-10-26": "active"}
    assert day1["plan_date"] != day2["plan_date"]


def test_today_set_shortage_when_bank_runs_out(drill_env):
    service = drill_env.make([])
    save_plan(service, daily_count=10)  # 题库只有 4 条 python_backend
    view = service.today_view("u1")
    assert view["total"] == 4 and view["shortage"] is True


def test_today_set_requires_plan(drill_env):
    service = drill_env.make([])
    with pytest.raises(ValueError, match=MISSING_PLAN_TEXT[:6]):
        service.today_view("u1")


def test_today_set_empty_bank_raises(drill_env):
    service = drill_env.make([])
    save_plan(service, position="项目经理")  # 题库无此岗位
    with pytest.raises(ValueError, match=EMPTY_BANK_TEXT[:6]):
        service.today_view("u1")


def test_remaining_excludes_answered(drill_env):
    service = drill_env.make([score_json()])
    save_plan(service, daily_count=3)
    view = service.today_view("u1")
    qid = view["remaining"][0]["question_id"]
    service.answer("u1", qid, LONG_ANSWER)
    view = service.today_view("u1")
    assert view["done"] == 1
    assert qid not in {r["question_id"] for r in view["remaining"]}


def test_last_answer_completes_set(drill_env):
    service = drill_env.make([score_json()])
    save_plan(service, daily_count=1)
    qid = service.today_view("u1")["remaining"][0]["question_id"]
    result = service.answer("u1", qid, LONG_ANSWER)
    assert result["completed"] is True and result["progress"] == {"done": 1, "total": 1}
    view = service.today_view("u1")
    assert view["completed"] is True and view["remaining"] == []
    with Session(drill_env.engine) as session:
        drill_set = session.scalar(select(DrillSet).where(DrillSet.user_id == "u1"))
        assert drill_set.status == "completed" and drill_set.completed_at is not None


# ---- AC3：判分与进度 ----


def test_answer_too_short_raises(drill_env):
    service = drill_env.make([])
    save_plan(service)
    qid = service.today_view("u1")["remaining"][0]["question_id"]
    with pytest.raises(ValueError, match="太短"):
        service.answer("u1", qid, "太短了")


def test_answer_not_in_set_raises(drill_env):
    service = drill_env.make([])
    save_plan(service)
    service.today_view("u1")
    with pytest.raises(ValueError, match="题单"):
        service.answer("u1", "q_other", LONG_ANSWER)


def test_answer_parse_failure_writes_nothing(drill_env):
    service = drill_env.make(["这不是 JSON"])
    save_plan(service)
    qid = service.today_view("u1")["remaining"][0]["question_id"]
    with pytest.raises(RuntimeError, match="解析失败"):
        service.answer("u1", qid, LONG_ANSWER)
    with Session(drill_env.engine) as session:
        assert session.scalar(select(func.count()).select_from(DrillAttempt)) == 0


def test_answer_persists_score_and_reference_points(drill_env):
    service = drill_env.make(
        [score_json(correctness=5, depth=2, expression=3, strengths=["a", "b"], weaknesses=["c"])]
    )
    save_plan(service)
    qid = service.today_view("u1")["remaining"][0]["question_id"]
    result = service.answer("u1", qid, LONG_ANSWER)
    assert result["score"] == {"correctness": 5, "depth": 2, "expression": 3}
    assert result["feedback"] == "整体不错"
    assert result["strengths"] == ["a", "b"]
    assert result["weaknesses"] == ["c"]
    assert result["reference_points"] == ["要点一", "要点二", "要点三"]
    with Session(drill_env.engine) as session:
        attempt = session.scalar(select(DrillAttempt))
        assert attempt.question_id == qid
        assert attempt.answer == LONG_ANSWER
        assert attempt.feedback == "整体不错"
        assert attempt.correctness == 5


def test_answer_clamps_out_of_range_scores(drill_env):
    service = drill_env.make([score_json(correctness=9, depth="x", expression=0)])
    save_plan(service)
    qid = service.today_view("u1")["remaining"][0]["question_id"]
    result = service.answer("u1", qid, LONG_ANSWER)
    assert result["score"]["correctness"] == 5
    assert result["score"]["depth"] == 3
    assert result["score"]["expression"] == 1


def test_answer_upserts_same_question(drill_env):
    service = drill_env.make([score_json(correctness=2), score_json(correctness=5)])
    save_plan(service)
    qid = service.today_view("u1")["remaining"][0]["question_id"]
    service.answer("u1", qid, LONG_ANSWER)
    result = service.answer("u1", qid, LONG_ANSWER + "补充")
    assert result["score"]["correctness"] == 5
    with Session(drill_env.engine) as session:
        assert session.scalar(select(func.count()).select_from(DrillAttempt)) == 1


def test_progress_empty_state(drill_env):
    service = drill_env.make([])
    progress = service.progress("u1")
    assert progress == {
        "plan": None,
        "today": {"total": 0, "done": 0, "completed": False, "shortage": False},
        "total_attempts": 0,
        "total_completed_sets": 0,
        "streak": 0,
    }


def test_progress_totals_and_streak(drill_env):
    service = drill_env.make([score_json()])
    save_plan(service, daily_count=1)
    # 昨天、前天各完成一份（直接写库），今天完成一份
    with Session(drill_env.engine) as session:
        for day, day_date in (
            ("d1", datetime(2026, 10, 24, 8, 0)),
            ("d2", datetime(2026, 10, 23, 8, 0)),
        ):
            session.add(
                DrillSet(
                    id=day,
                    user_id="u1",
                    plan_date=day_date.date().isoformat(),
                    question_ids=["q_old"],
                    status="completed",
                    completed_at=day_date,
                )
            )
        session.commit()
    qid = service.today_view("u1")["remaining"][0]["question_id"]
    service.answer("u1", qid, LONG_ANSWER)  # needs score response
    progress = service.progress("u1")
    assert progress["plan"] == {
        "position": "python_backend",
        "difficulty": "medium",
        "daily_count": 1,
    }
    assert progress["today"]["completed"] is True
    assert progress["total_attempts"] == 1
    assert progress["total_completed_sets"] == 3
    assert progress["streak"] == 3


def test_streak_from_yesterday_when_today_incomplete(drill_env):
    service = drill_env.make([])
    with Session(drill_env.engine) as session:
        session.add(
            DrillSet(
                id="d1",
                user_id="u1",
                plan_date="2026-10-24",
                question_ids=["q_old"],
                status="completed",
                completed_at=datetime(2026, 10, 24, 8, 0),
            )
        )
        session.commit()
    progress = service.progress("u1")
    assert progress["streak"] == 1


# ---- 错题纠正（本规格 AC1-2） ----


def reclassify_json(**overrides) -> str:
    item = {"position": "java_backend", "category": "spring"}
    item.update(overrides)
    return json.dumps(item, ensure_ascii=False)


def _today_qid(service, user_id="u1") -> str:
    save_plan(service, user_id=user_id)
    return service.today_view(user_id)["remaining"][0]["question_id"]


def test_reclassify_moves_position_and_category_keeps_question_id(drill_env):
    service = drill_env.make([], [reclassify_json()])
    qid = _today_qid(service)
    result = service.reclassify("u1", qid)
    assert result == {
        "question_id": qid,
        "position": "java_backend",
        "category": "spring",
        "changed": True,
        "previous": {"position": "python_backend", "category": "python_basics"},
    }
    row = drill_env.store.get(qid)
    assert (row.position, row.category) == ("java_backend", "spring")
    assert row.question_id == qid  # 题 ID 不变：练习答题引用不悬挂


def test_reclassify_same_result_returns_unchanged_no_write(drill_env):
    service = drill_env.make(
        [], [reclassify_json(position="python_backend", category="python_basics")]
    )
    qid = _today_qid(service)
    result = service.reclassify("u1", qid)
    assert result["changed"] is False
    assert result["previous"] == {"position": "python_backend", "category": "python_basics"}
    row = drill_env.store.get(qid)
    assert (row.position, row.category) == ("python_backend", "python_basics")


def test_reclassify_uses_classify_tier_not_score_llm(drill_env):
    # 判分档零响应、分类档有响应 → 正常走通：说明调用的是 llm_classify
    service = drill_env.make([], [reclassify_json()])
    qid = _today_qid(service)
    assert service.reclassify("u1", qid)["changed"] is True
    # 分类档零响应 → 立即失败（若误用判分档不会走到这里）
    service = drill_env.make([], [])
    with pytest.raises(AssertionError, match="无可用响应"):
        service.reclassify("u1", qid)


def test_reclassify_not_in_active_set_raises(drill_env):
    service = drill_env.make([], [reclassify_json()])
    save_plan(service)
    service.today_view("u1")
    with pytest.raises(ValueError, match="题单"):
        service.reclassify("u1", "q_other")


def test_reclassify_question_missing_from_bank_raises(drill_env):
    service = drill_env.make([], [reclassify_json()])
    _today_qid(service)
    with Session(drill_env.engine) as session:
        drill_set = session.scalar(select(DrillSet).where(DrillSet.user_id == "u1"))
        drill_set.question_ids = [*drill_set.question_ids, "q_deleted"]
        session.commit()
    with pytest.raises(ValueError, match="不存在"):
        service.reclassify("u1", "q_deleted")


def test_reclassify_invalid_output_raises_without_write(drill_env):
    # 非 JSON / 缺字段 / 岗位越界 / 类别不属于该岗位 → RuntimeError 且不写库
    bad_responses = [
        "这不是 JSON",
        reclassify_json(position="rust_backend"),
        reclassify_json(category="python_web"),
        json.dumps({"position": "java_backend"}, ensure_ascii=False),
    ]
    for bad in bad_responses:
        service = drill_env.make([], [bad])
        qid = _today_qid(service)
        with pytest.raises(RuntimeError):
            service.reclassify("u1", qid)
        row = drill_env.store.get(qid)
        assert (row.position, row.category) == ("python_backend", "python_basics")


def test_reclassify_idempotent_after_move(drill_env):
    # 纠正后再识别一次（同结果）→ changed=False，分类保持纠正后的值
    service = drill_env.make([], [reclassify_json(), reclassify_json()])
    qid = _today_qid(service)
    assert service.reclassify("u1", qid)["changed"] is True
    again = service.reclassify("u1", qid)
    assert again["changed"] is False
    assert again["previous"] == {"position": "java_backend", "category": "spring"}
