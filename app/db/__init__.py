"""数据库层：SQLAlchemy 模型与引擎工厂。

连接串取环境变量 DATABASE_URL（测试用 sqlite://）。部署时指向服务器
MySQL offercoach 库；与 fin 项目隔离（独立 database）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, create_engine, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Question(Base):
    """题库表：A1 抽题与 B1 题库检索共用。"""

    __tablename__ = "question_bank"

    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    difficulty: Mapped[str] = mapped_column(String(16), index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    question: Mapped[str] = mapped_column(String(2000))
    reference_points: Mapped[list[str]] = mapped_column(JSON)
    followups: Mapped[list[str]] = mapped_column(JSON)
    keywords: Mapped[list[str]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ChatSession(Base):
    """会话表：Web API 按业务类型建会话的元数据（消息本体在 checkpointer）。"""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    business: Mapped[str] = mapped_column(String(16), index=True)
    target_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 仅 interview
    position: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 仅 interview
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # 最近一次消息时间：三层记忆 T1 兜底总结判定（老库迁移补列，nullable 兼容）
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuthUser(Base):
    """登录用户表：用户名唯一；密码只存 PBKDF2 散列（不存明文）。"""

    __tablename__ = "auth_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuthToken(Base):
    """登录令牌表：只存 token 的 sha256——DB 泄露不暴露原始 token。

    expires_at 7 天滑动过期（每次成功认证续期）；nullable 兼容老库迁移。
    """

    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LoginFailure(Base):
    """登录失败事件：24 小时滑动窗口内满 MAX_FAILURES 条即锁定（见 auth_service）。

    不存在用户名同样记事件（防探测：验证码/锁定与否不泄露用户是否存在）。
    """

    __tablename__ = "auth_login_failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), index=True)
    failed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DrillPlan(Base):
    """练习计划：一人一份（user_id 唯一）。position 可空 = 全岗位混合抽题。"""

    __tablename__ = "drill_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    position: Mapped[str | None] = mapped_column(String(32), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(16))  # entry/medium/hard/mixed
    daily_count: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class DrillSet(Base):
    """每日题单：按自然日一份；生成新单时未完成旧单置 abandoned。"""

    __tablename__ = "drill_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_date: Mapped[str] = mapped_column(String(10), index=True)
    question_ids: Mapped[list[str]] = mapped_column(JSON)
    shortage: Mapped[bool] = mapped_column(default=False)  # 题库不足当日题量
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/abandoned/completed
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DrillAttempt(Base):
    """题单作答记录：UNIQUE(set_id, question_id)，重复作答覆盖更新（幂等）。"""

    __tablename__ = "drill_attempts"
    __table_args__ = (UniqueConstraint("set_id", "question_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[str] = mapped_column(String(36), index=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    answer: Mapped[str] = mapped_column(String(4000))
    correctness: Mapped[int] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(Integer)
    expression: Mapped[int] = mapped_column(Integer)
    feedback: Mapped[str] = mapped_column(String(2000))
    strengths: Mapped[list[str]] = mapped_column(JSON)
    weaknesses: Mapped[list[str]] = mapped_column(JSON)
    answered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def build_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)
