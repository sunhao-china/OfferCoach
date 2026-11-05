"""L3: SessionStore 老库补列迁移（create_all 不增量）。

依据 specs/02-in-progress/api/interview-options.md 的 AC3。
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect

from app.api.session_store import SessionStore

LEGACY_DDL = (
    "CREATE TABLE chat_sessions ("
    "id VARCHAR(36) PRIMARY KEY, "
    "user_id VARCHAR(64), "
    "business VARCHAR(16), "
    "target_session_id VARCHAR(36), "
    "created_at DATETIME)"
)


def test_session_store_migrates_legacy_columns(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as conn:
        conn.exec_driver_sql(LEGACY_DDL)
    store = SessionStore(engine)  # 初始化触发补列
    columns = {column["name"] for column in inspect(engine).get_columns("chat_sessions")}
    assert {"difficulty", "position"} <= columns
    # 老表读写不受影响，新列可用
    store.create("s1", "u1", "interview", None, difficulty="entry", position="Java")
    fetched = store.get("s1")
    assert fetched is not None
    assert fetched.difficulty == "entry"
    assert fetched.position == "Java"


def test_session_store_migration_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    store = SessionStore(engine)
    SessionStore(engine)  # 二次初始化不报错（列已存在）
    assert store.get("nope") is None


def test_session_store_delete_removes_row_and_reports_missing(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    store = SessionStore(engine)
    store.create("s1", "u1", "interview")
    assert store.delete("s1") is True
    assert store.get("s1") is None
    assert store.delete("s1") is False  # 不存在 → False


def test_session_store_referenced_by_counts_reviews(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    store = SessionStore(engine)
    store.create("interview_1", "u1", "interview")
    store.create("review_1", "u1", "review", target_session_id="interview_1")
    store.create("review_2", "u1", "review", target_session_id="interview_1")
    assert store.referenced_by("interview_1") == 2
    assert store.referenced_by("review_1") == 0
