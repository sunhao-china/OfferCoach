"""题库查询层：A1 抽题与 B1 检索工具共用。

生产 MySQL / 测试 sqlite 同构（engine 依赖注入）。数据契约见
specs/03-implemented/question_bank/schema.md。
"""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import Question

DEFAULT_POSITION = "java_backend"
DEFAULT_LIMIT = 20

# 口语岗位输入 → 库内 position 别名表（三期多岗位；键值均为小写、无多余空白）
POSITION_ALIASES: dict[str, str] = {
    "java": "java_backend",
    "java后端": "java_backend",
    "java 后端": "java_backend",
    "java开发": "java_backend",
    "java_backend": "java_backend",
    "python": "python_backend",
    "py": "python_backend",
    "python后端": "python_backend",
    "python 后端": "python_backend",
    "python开发": "python_backend",
    "python_backend": "python_backend",
    "c": "c_backend",
    "c语言": "c_backend",
    "c 语言": "c_backend",
    "c后端": "c_backend",
    "c_backend": "c_backend",
    "c++": "cpp_backend",
    "cpp": "cpp_backend",
    "c++后端": "cpp_backend",
    "c++ 后端": "cpp_backend",
    "cpp后端": "cpp_backend",
    "cpp_backend": "cpp_backend",
    "c#": "csharp_backend",
    "csharp": "csharp_backend",
    "c sharp": "csharp_backend",
    ".net": "csharp_backend",
    "c#后端": "csharp_backend",
    "csharp_backend": "csharp_backend",
    "ai agent": "ai_agent",
    "aiagent": "ai_agent",
    "ai": "ai_agent",
    "agent": "ai_agent",
    "ai智能体": "ai_agent",
    "智能体": "ai_agent",
    "ai_agent": "ai_agent",
    "项目经理": "project_manager",
    "pm": "project_manager",
    "产品经理": "project_manager",
    "project_manager": "project_manager",
}


def normalize_position(raw: str) -> str:
    """口语岗位输入归一化到库内 position；未命中返回原文（去首尾空白、内部空白压缩）。

    大小写不敏感："Python"/"C++"/"AI Agent" 均先小写再查别名表。
    """
    text = " ".join(str(raw).strip().lower().split())
    return POSITION_ALIASES.get(text, text)


class QuestionStore:
    """按过滤条件查询 question_bank 表；排序稳定可测。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _apply_filters(
        stmt: Select,
        *,
        position: str | None,
        category: str | None,
        difficulty: str | None,
        qtype: str | None,
        keywords: str | list[str] | None,
        exclude_ids: tuple[str, ...] | list[str],
    ) -> Select:
        if position is not None:
            stmt = stmt.where(Question.position == position)
        if category is not None:
            stmt = stmt.where(Question.category == category)
        if difficulty is not None:
            stmt = stmt.where(Question.difficulty == difficulty)
        if qtype is not None:
            stmt = stmt.where(Question.type == qtype)
        if keywords:
            words = [keywords] if isinstance(keywords, str) else list(keywords)
            stmt = stmt.where(or_(*[Question.question.like(f"%{w}%") for w in words]))
        if exclude_ids:
            stmt = stmt.where(Question.question_id.not_in(list(exclude_ids)))
        return stmt

    def search(
        self,
        *,
        position: str | None = DEFAULT_POSITION,
        category: str | None = None,
        difficulty: str | None = None,
        qtype: str | None = None,
        keywords: str | list[str] | None = None,
        exclude_ids: tuple[str, ...] | list[str] = (),
        limit: int = DEFAULT_LIMIT,
    ) -> list[Question]:
        """按 AND 组合过滤；keywords 对题面做 OR 模糊匹配；结果按 category + id 稳定排序。

        position=None 表示全岗位检索（B1 跨岗位）。
        """
        stmt = (
            self._apply_filters(
                select(Question),
                position=position,
                category=category,
                difficulty=difficulty,
                qtype=qtype,
                keywords=keywords,
                exclude_ids=exclude_ids,
            )
            .order_by(Question.category, Question.question_id)
            .limit(limit)
        )
        with Session(self._engine) as session:
            return list(session.scalars(stmt))

    def get(self, question_id: str) -> Question | None:
        with Session(self._engine) as session:
            return session.get(Question, question_id)

    def position_categories(self) -> dict[str, list[str]]:
        """全库岗位 → 类别清单（去重升序；供纠正分类提示词渲染与结果校验共用）。"""
        with Session(self._engine) as session:
            rows = session.execute(
                select(Question.position, Question.category)
                .distinct()
                .order_by(Question.position, Question.category)
            )
        result: dict[str, list[str]] = {}
        for position, category in rows:
            result.setdefault(position, []).append(category)
        return result

    def reclassify(self, question_id: str, position: str, category: str) -> Question | None:
        """纠正分类：改写 position/category 两列；question_id 保持不变（练习答题按它引用）。"""
        with Session(self._engine) as session:
            question = session.get(Question, question_id)
            if question is None:
                return None
            question.position = position
            question.category = category
            session.commit()
            return question

    def random_pick(
        self,
        *,
        position: str | None = DEFAULT_POSITION,
        category: str | None = None,
        difficulty: str | None = None,
        qtype: str | None = None,
        exclude_ids: tuple[str, ...] | list[str] = (),
    ) -> Question | None:
        """满足过滤条件的随机抽一题；无匹配返回 None。"""
        stmt = (
            self._apply_filters(
                select(Question),
                position=position,
                category=category,
                difficulty=difficulty,
                qtype=qtype,
                keywords=None,
                exclude_ids=exclude_ids,
            )
            .order_by(func.random())
            .limit(1)
        )
        with Session(self._engine) as session:
            return session.scalars(stmt).first()
