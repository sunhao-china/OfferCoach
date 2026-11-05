"""练习计划服务层：计划 / 每日题单 / 逐题判分 / 错题纠正 / 进度统计。

独立于 langgraph（Web API 直接调用，不做工作流）；判分复用 mock_interview/score.v2
模板与 llm_strong（与面试判分同档），错题纠正走 question_bank/reclassify.v1 与
llm_classify（cheap 档）。三张表（drill_plans/drill_sets/drill_attempts）随
SessionStore 启动在会话 sqlite 库自动建表。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import DrillAttempt, DrillPlan, DrillSet, Question
from app.graphs.json_utils import parse_json_text
from app.prompts.registry import PromptRegistry
from app.question_bank.store import QuestionStore, normalize_position

ANSWER_MIN = 20
DIFFICULTIES = ("entry", "medium", "hard", "mixed")
DAILY_COUNT_MIN, DAILY_COUNT_MAX = 1, 20
DEFAULT_DAILY_COUNT = 5
ALL_POSITION_TOKENS = ("", "全部", "all")
PROMPT_SCORE = "mock_interview/score"
PROMPT_RECLASSIFY = "question_bank/reclassify"

# 纠正分类提示词用的岗位展示名（与前端 DRILL_POSITION_DISPLAYS 一致）
POSITION_DISPLAYS = {
    "java_backend": "Java 后端",
    "python_backend": "Python",
    "c_backend": "C",
    "cpp_backend": "C++",
    "csharp_backend": "C#",
    "ai_agent": "AI Agent",
    "project_manager": "项目经理",
}

SCORE_FLOOR, SCORE_CEIL, SCORE_DEFAULT = 1, 5, 3

MISSING_PLAN_TEXT = "还没有设置练习计划，请先到「练习」页设置岗位、难度与每日题量"
EMPTY_BANK_TEXT = "题库中没有符合计划条件的题目，请调整岗位或难度后重试"


class DrillNotFoundError(ValueError):
    """题目不在当前题单（含跨用户取他人题）：API 层统一转 404 不泄露。"""


def _clamp(value: Any) -> int:
    """分数钳制到 1-5；非数值回退默认 3 分（与 mock_interview 判分一致）。"""
    try:
        return max(SCORE_FLOOR, min(SCORE_CEIL, int(value)))
    except (TypeError, ValueError):
        return SCORE_DEFAULT


def _numbered(items: Any) -> str:
    lines = [f"{index}. {item}" for index, item in enumerate(list(items or []), start=1)]
    return "\n".join(lines) or "（无）"


def _qa_block(question: Question, answer: str) -> str:
    """单题单块（score.v2 的 $qa_blocks 变量，形状与 mock_interview score 节点一致）。"""
    return (
        f"题目 1（{question.question_id}）：{question.question}\n"
        f"评分要点：\n{_numbered(question.reference_points)}\n回答：{answer}"
    )


def _streak(completed_dates: set[str], today: str) -> int:
    """连续完成天数：从今天（今天已完成）或昨天起向前连续计数。"""
    day = datetime.fromisoformat(today)
    if today not in completed_dates:
        day -= timedelta(days=1)
    count = 0
    while day.date().isoformat() in completed_dates:
        count += 1
        day -= timedelta(days=1)
    return count


def _drill_trace_config(user_id: str, question_id: str, *, tag: str = "drill") -> dict:
    """练习 LLM 调用的追踪配置（与 executor 同套 langfuse_* metadata 约定）。"""
    from app.core.tracing import build_trace_handler

    handler = build_trace_handler()
    if handler is None:
        return {}
    return {
        "callbacks": [handler],
        "metadata": {
            "langfuse_session_id": f"drill:{question_id}",
            "langfuse_user_id": user_id,
            "langfuse_tags": [tag],
        },
    }


class DrillService:
    """练习计划业务逻辑。now 可注入（单测假时钟）；判分走 llm、纠正分类走 llm_classify。"""

    def __init__(
        self,
        store: QuestionStore,
        engine: Engine,
        registry: PromptRegistry,
        llm: Any,
        llm_classify: Any,
        *,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._store = store
        self._engine = engine
        self._registry = registry
        self._llm = llm
        self._llm_classify = llm_classify
        self._now = now

    # ---- 计划 ----

    def get_plan(self, user_id: str) -> dict | None:
        with Session(self._engine) as session:
            plan = session.scalar(select(DrillPlan).where(DrillPlan.user_id == user_id))
            return self._plan_view(plan) if plan else None

    @staticmethod
    def _plan_view(plan: DrillPlan) -> dict:
        return {
            "position": plan.position,
            "difficulty": plan.difficulty,
            "daily_count": plan.daily_count,
        }

    def save_plan(
        self, user_id: str, *, position: str | None, difficulty: str, daily_count: int
    ) -> dict:
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"难度只支持 entry/medium/hard/mixed，收到 {difficulty!r}")
        if not DAILY_COUNT_MIN <= daily_count <= DAILY_COUNT_MAX:
            raise ValueError(f"每日题量需在 {DAILY_COUNT_MIN}-{DAILY_COUNT_MAX} 之间")
        normalized = None
        if position and position.strip().lower() not in ALL_POSITION_TOKENS:
            normalized = normalize_position(position)
        with Session(self._engine) as session:
            plan = session.scalar(select(DrillPlan).where(DrillPlan.user_id == user_id))
            if plan is None:
                plan = DrillPlan(
                    user_id=user_id,
                    position=normalized,
                    difficulty=difficulty,
                    daily_count=daily_count,
                )
                session.add(plan)
            else:
                plan.position = normalized
                plan.difficulty = difficulty
                plan.daily_count = daily_count
            session.commit()
            return self._plan_view(plan)

    # ---- 每日题单 ----

    def _pick_questions(self, plan: DrillPlan) -> tuple[list[str], bool]:
        """按计划随机抽 daily_count 题不重复；返回 (question_ids, shortage)。"""
        question_ids: list[str] = []
        for _ in range(plan.daily_count):
            question = self._store.random_pick(
                position=plan.position,  # None = 全岗位
                difficulty=None if plan.difficulty == "mixed" else plan.difficulty,
                exclude_ids=question_ids,
            )
            if question is None:
                break
            question_ids.append(question.question_id)
        if not question_ids:
            raise ValueError(EMPTY_BANK_TEXT)
        return question_ids, len(question_ids) < plan.daily_count

    def _get_or_create_today_set(self, session: Session, user_id: str) -> DrillSet:
        today = self._now().date().isoformat()
        existing = session.scalar(
            select(DrillSet).where(DrillSet.user_id == user_id, DrillSet.plan_date == today)
        )
        if existing:
            return existing
        plan = session.scalar(select(DrillPlan).where(DrillPlan.user_id == user_id))
        if plan is None:
            raise ValueError(MISSING_PLAN_TEXT)
        # 生成新单前作废所有未完成旧单
        for old in session.scalars(
            select(DrillSet).where(
                DrillSet.user_id == user_id,
                DrillSet.status == "active",
                DrillSet.plan_date != today,
            )
        ):
            old.status = "abandoned"
        question_ids, shortage = self._pick_questions(plan)
        drill_set = DrillSet(
            id=str(uuid.uuid4()),
            user_id=user_id,
            plan_date=today,
            question_ids=question_ids,
            shortage=shortage,
            status="active",
        )
        session.add(drill_set)
        session.commit()
        return drill_set

    def _attempt_map(self, session: Session, drill_set: DrillSet) -> dict[str, DrillAttempt]:
        attempts = session.scalars(select(DrillAttempt).where(DrillAttempt.set_id == drill_set.id))
        return {attempt.question_id: attempt for attempt in attempts}

    def today_view(self, user_id: str) -> dict:
        with Session(self._engine) as session:
            drill_set = self._get_or_create_today_set(session, user_id)
            attempts = self._attempt_map(session, drill_set)
            remaining: list[dict] = []
            for question_id in drill_set.question_ids:
                if question_id in attempts:
                    continue
                question = self._store.get(question_id)
                if question is None:  # 题库被清理的兜底：跳过不展示
                    continue
                remaining.append(
                    {
                        "question_id": question.question_id,
                        "position": question.position,
                        "category": question.category,
                        "difficulty": question.difficulty,
                        "question": question.question,
                    }
                )
            return {
                "plan_date": drill_set.plan_date,
                "total": len(drill_set.question_ids),
                "done": sum(1 for qid in drill_set.question_ids if qid in attempts),
                "completed": drill_set.status == "completed",
                "shortage": drill_set.shortage,
                "remaining": remaining,
            }

    # ---- 逐题判分 ----

    def answer(self, user_id: str, question_id: str, answer: str) -> dict:
        text = (answer or "").strip()
        if len(text) < ANSWER_MIN:
            raise ValueError(f"回答太短，至少 {ANSWER_MIN} 字")
        with Session(self._engine) as session:
            drill_set = session.scalar(
                select(DrillSet).where(DrillSet.user_id == user_id, DrillSet.status == "active")
            )
            if drill_set is None or question_id not in drill_set.question_ids:
                raise DrillNotFoundError("题目不在当前题单中")
            question = self._store.get(question_id)
            if question is None:
                raise DrillNotFoundError("题目不存在")

        # 判分在事务外调用 LLM（session 不跨长调用）；Langfuse 配置齐全时挂 trace
        system = self._registry.render(PROMPT_SCORE, qa_blocks=_qa_block(question, text))
        raw = self._llm.invoke(
            [system],
            config=_drill_trace_config(user_id, question_id),
        ).content
        data = parse_json_text(raw)
        results = data.get("results") if data and isinstance(data.get("results"), list) else []
        item = results[0] if results and isinstance(results[0], dict) else {}
        if not any(key in item for key in ("correctness", "depth", "expression")):
            # 解析不出任何分数视为判分失败：不写盘，交 API 层转 502
            raise RuntimeError("判分结果解析失败")

        score = {
            "correctness": _clamp(item.get("correctness")),
            "depth": _clamp(item.get("depth")),
            "expression": _clamp(item.get("expression")),
        }
        feedback = item.get("comment", "")
        strengths = item.get("strengths") or []
        weaknesses = item.get("weaknesses") or []
        with Session(self._engine) as session:
            drill_set = session.get(DrillSet, drill_set.id)
            if drill_set is None:
                raise RuntimeError("题单状态异常")
            attempt = session.scalar(
                select(DrillAttempt).where(
                    DrillAttempt.set_id == drill_set.id,
                    DrillAttempt.question_id == question_id,
                )
            )
            if attempt is None:
                attempt = DrillAttempt(
                    set_id=drill_set.id,
                    question_id=question_id,
                    answer=text,
                    correctness=score["correctness"],
                    depth=score["depth"],
                    expression=score["expression"],
                    feedback=feedback,
                    strengths=strengths,
                    weaknesses=weaknesses,
                )
                session.add(attempt)
            else:
                attempt.answer = text
                attempt.correctness = score["correctness"]
                attempt.depth = score["depth"]
                attempt.expression = score["expression"]
                attempt.feedback = feedback
                attempt.strengths = strengths
                attempt.weaknesses = weaknesses
                attempt.answered_at = self._now()
            session.commit()
            done = session.scalar(
                select(func.count())
                .select_from(DrillAttempt)
                .where(DrillAttempt.set_id == drill_set.id)
            )
            total = len(drill_set.question_ids)
            completed = done == total
            if completed and drill_set.status != "completed":
                drill_set.status = "completed"
                drill_set.completed_at = self._now()
                session.commit()
            return {
                "score": score,
                "feedback": feedback,
                "strengths": strengths,
                "weaknesses": weaknesses,
                "reference_points": list(question.reference_points or []),
                "completed": completed,
                "progress": {"done": done, "total": total},
            }

    # ---- 错题纠正 ----

    def reclassify(self, user_id: str, question_id: str) -> dict:
        """重新识别题目的岗位与类别（llm_classify），校验通过后纠正题库记录。

        question_id 不变（练习答题按它引用）；识别结果越界/解析失败一律不写库，
        向上抛 RuntimeError 交 API 层转 502（与判分同策略）。
        """
        with Session(self._engine) as session:
            drill_set = session.scalar(
                select(DrillSet).where(DrillSet.user_id == user_id, DrillSet.status == "active")
            )
            if drill_set is None or question_id not in drill_set.question_ids:
                raise DrillNotFoundError("题目不在当前题单中")
        question = self._store.get(question_id)
        if question is None:
            raise DrillNotFoundError("题目不存在")

        categories = self._store.position_categories()
        positions_line = "、".join(
            f"{position}（{POSITION_DISPLAYS.get(position, position)}）" for position in categories
        )
        category_map = "\n".join(
            f"{position}: {'/'.join(items)}" for position, items in categories.items()
        )
        system = self._registry.render(
            PROMPT_RECLASSIFY,
            positions=positions_line,
            category_map=category_map,
            question=question.question,
        )
        raw = self._llm_classify.invoke(
            [system],
            config=_drill_trace_config(user_id, question_id, tag="drill-reclassify"),
        ).content
        data = parse_json_text(raw)
        position = (data or {}).get("position")
        category = (data or {}).get("category")
        if not isinstance(position, str) or not isinstance(category, str):
            raise RuntimeError("分类识别结果解析失败")
        if categories.get(position) is None or category not in categories[position]:
            raise RuntimeError("分类识别结果越界")

        previous = {"position": question.position, "category": question.category}
        changed = position != question.position or category != question.category
        if changed:
            updated = self._store.reclassify(question_id, position, category)
            if updated is None:
                raise RuntimeError("题目记录不存在")
        return {
            "question_id": question_id,
            "position": position,
            "category": category,
            "changed": changed,
            "previous": previous,
        }

    # ---- 进度 ----

    def progress(self, user_id: str) -> dict:
        today = self._now().date().isoformat()
        with Session(self._engine) as session:
            drill_set = session.scalar(
                select(DrillSet).where(DrillSet.user_id == user_id, DrillSet.plan_date == today)
            )
            if drill_set is None:
                today_state = {"total": 0, "done": 0, "completed": False, "shortage": False}
            else:
                done = session.scalar(
                    select(func.count())
                    .select_from(DrillAttempt)
                    .where(DrillAttempt.set_id == drill_set.id)
                )
                today_state = {
                    "total": len(drill_set.question_ids),
                    "done": done or 0,
                    "completed": drill_set.status == "completed",
                    "shortage": drill_set.shortage,
                }
            total_attempts = session.scalar(
                select(func.count())
                .select_from(DrillAttempt)
                .join(DrillSet, DrillAttempt.set_id == DrillSet.id)
                .where(DrillSet.user_id == user_id)
            )
            completed_dates = {
                row.completed_at.date().isoformat()
                for row in session.scalars(
                    select(DrillSet).where(
                        DrillSet.user_id == user_id, DrillSet.status == "completed"
                    )
                )
                if row.completed_at is not None
            }
            plan = session.scalar(select(DrillPlan).where(DrillPlan.user_id == user_id))
            return {
                "plan": self._plan_view(plan) if plan else None,
                "today": today_state,
                "total_attempts": total_attempts,
                "total_completed_sets": len(completed_dates),
                "streak": _streak(completed_dates, today),
            }
