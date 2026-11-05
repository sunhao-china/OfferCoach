"""会话元数据存取：chat_sessions 表（消息本体在 checkpointer）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import Base, ChatSession

# 老库补列（列名/类型为常量，无注入面）：difficulty/position 为 interview 会话属性，
# last_message_at 为三层记忆 T1 兜底总结判定
_MIGRATION_COLUMNS = (
    ("difficulty", "VARCHAR(16)"),
    ("position", "VARCHAR(64)"),
    ("last_message_at", "DATETIME"),
)


class SessionStore:
    """engine 注入（测试 sqlite tmp / 生产 MySQL 同构）；开发期自动建表 + 老库补列。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        Base.metadata.create_all(engine)
        self._migrate(engine)

    def _migrate(self, engine: Engine) -> None:
        """create_all 不增量：对已存在的 chat_sessions 补齐新列（幂等）。"""
        existing = {column["name"] for column in inspect(engine).get_columns("chat_sessions")}
        for name, ddl in _MIGRATION_COLUMNS:
            if name not in existing:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE chat_sessions ADD COLUMN {name} {ddl}"
                    )

    def create(
        self,
        session_id: str,
        user_id: str,
        business: str,
        target_session_id: str | None = None,
        *,
        difficulty: str | None = None,
        position: str | None = None,
    ) -> ChatSession:
        # created_at 由 Python 写入（微秒精度）：同秒连建的会话排序仍稳定
        now = datetime.now()
        row = ChatSession(
            id=session_id,
            user_id=user_id,
            business=business,
            target_session_id=target_session_id,
            difficulty=difficulty,
            position=position,
            created_at=now,
            last_message_at=now,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def get(self, session_id: str) -> ChatSession | None:
        with Session(self._engine) as session:
            return session.scalar(select(ChatSession).where(ChatSession.id == session_id))

    def touch(self, session_id: str) -> bool:
        """消息路径更新 last_message_at；会话不存在返回 False。"""
        with Session(self._engine) as session:
            row = session.get(ChatSession, session_id)
            if row is None:
                return False
            row.last_message_at = datetime.now()
            session.commit()
            return True

    def list_stale(self, cutoff: datetime) -> list[ChatSession]:
        """last_message_at 早于 cutoff 的会话（T1 兜底总结扫描用）。

        NULL 行（迁移前的老会话，从未有消息经过 touch/create）不纳入——
        它们早于记忆功能上线，批量补总结无意义。
        """
        stmt = select(ChatSession).where(
            ChatSession.last_message_at.is_not(None),
            ChatSession.last_message_at < cutoff,
        )
        with Session(self._engine) as session:
            return list(session.scalars(stmt))

    def list(self, user_id: str | None = None) -> list[ChatSession]:
        """按创建时间倒序列出会话；user_id 为空时返回全部（MVP）。"""
        stmt = select(ChatSession).order_by(ChatSession.created_at.desc())
        if user_id:
            stmt = stmt.where(ChatSession.user_id == user_id)
        with Session(self._engine) as session:
            return list(session.scalars(stmt))

    def referenced_by(self, session_id: str) -> int:
        """被多少 review 会话引用为复盘对象（删除前引用保护）。"""
        with Session(self._engine) as session:
            count = session.scalar(
                select(func.count())
                .select_from(ChatSession)
                .where(ChatSession.target_session_id == session_id)
            )
            return count or 0

    def delete(self, session_id: str) -> bool:
        """删除会话行；不存在返回 False（检查点由 executor 另行清理）。"""
        with Session(self._engine) as session:
            row = session.get(ChatSession, session_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
