"""L4: MemoryStore — FakeMemoryStore 语义与协议对齐（AC1）。

依据 specs/02-in-progress/architecture/three-tier-memory.md 的 AC1。
"""

from __future__ import annotations

import pytest

from app.memory.store import (
    COLLECTION_CANDIDATES,
    COLLECTION_LONG_TERM,
    FakeMemoryStore,
    _cosine,
    _wire_id,
)


def _upsert(store: FakeMemoryStore, collection: str, point_id: str, user_id: str) -> None:
    store.upsert(collection, point_id, [0.1] * 32, {"user_id": user_id, "topic": point_id})


def test_upsert_get_roundtrip():
    store = FakeMemoryStore()
    store.ensure_collections(32)
    store.upsert(
        COLLECTION_LONG_TERM, "u1:s1", [0.1] * 32, {"user_id": "u1", "topic": "并发"}
    )
    point = store.get(COLLECTION_LONG_TERM, "u1:s1")
    assert point is not None
    assert point["payload"]["topic"] == "并发"
    assert point["vector"] == [0.1] * 32
    assert store.get(COLLECTION_LONG_TERM, "missing") is None


def test_upsert_same_id_overwrites():
    store = FakeMemoryStore()
    _upsert(store, COLLECTION_LONG_TERM, "u1:s1", "u1")
    store.upsert(
        COLLECTION_LONG_TERM, "u1:s1", [0.5] * 32, {"user_id": "u1", "topic": "更新"}
    )
    point = store.get(COLLECTION_LONG_TERM, "u1:s1")
    assert point["payload"]["topic"] == "更新"
    assert point["vector"] == [0.5] * 32


def test_list_by_user_filters_user_id():
    store = FakeMemoryStore()
    _upsert(store, COLLECTION_LONG_TERM, "u1:s1", "u1")
    _upsert(store, COLLECTION_LONG_TERM, "u1:s2", "u1")
    _upsert(store, COLLECTION_LONG_TERM, "u2:s1", "u2")
    mine = store.list_by_user(COLLECTION_LONG_TERM, "u1")
    assert {point["id"] for point in mine} == {"u1:s1", "u1:s2"}
    assert store.list_by_user(COLLECTION_LONG_TERM, "u3") == []


def test_list_all_returns_everything():
    store = FakeMemoryStore()
    _upsert(store, COLLECTION_LONG_TERM, "u1:s1", "u1")
    _upsert(store, COLLECTION_CANDIDATES, "u2:c1", "u2")
    assert len(store.list_all(COLLECTION_LONG_TERM)) == 1
    assert len(store.list_all(COLLECTION_CANDIDATES)) == 1


def test_search_user_filtered_and_score_sorted():
    store = FakeMemoryStore()
    store.ensure_collections(32)
    store.upsert(
        COLLECTION_LONG_TERM, "u1:a", [1.0] + [0.0] * 31, {"user_id": "u1", "topic": "A"}
    )
    store.upsert(
        COLLECTION_LONG_TERM, "u1:b", [0.0] * 31 + [1.0], {"user_id": "u1", "topic": "B"}
    )
    store.upsert(
        COLLECTION_LONG_TERM, "u2:b", [0.0] * 31 + [1.0], {"user_id": "u2", "topic": "B2"}
    )
    hits = store.search(COLLECTION_LONG_TERM, "u1", [1.0] + [0.0] * 31, limit=5)
    assert [hit["id"] for hit in hits] == ["u1:a", "u1:b"]  # 只搜本用户，按分数降序
    assert hits[0]["score"] == pytest.approx(1.0)
    assert hits[1]["score"] == pytest.approx(0.0)


def test_touch_updates_payload_keeps_vector():
    store = FakeMemoryStore()
    store.upsert(
        COLLECTION_LONG_TERM, "u1:s1", [0.3] * 32, {"user_id": "u1", "status": "active"}
    )
    store.touch(COLLECTION_LONG_TERM, "u1:s1", {"status": "decayed", "recall_count": 1})
    point = store.get(COLLECTION_LONG_TERM, "u1:s1")
    assert point["payload"]["status"] == "decayed"
    assert point["payload"]["recall_count"] == 1
    assert point["payload"]["user_id"] == "u1"  # 原 payload 键保留
    assert point["vector"] == [0.3] * 32  # 向量不被零向量覆盖


def test_delete_removes_point():
    store = FakeMemoryStore()
    _upsert(store, COLLECTION_LONG_TERM, "u1:s1", "u1")
    store.delete(COLLECTION_LONG_TERM, "u1:s1")
    assert store.get(COLLECTION_LONG_TERM, "u1:s1") is None
    store.delete(COLLECTION_LONG_TERM, "never-existed")  # 幂等不抛


def test_ensure_collections_records_size():
    store = FakeMemoryStore()
    store.ensure_collections(1024)
    assert store._vector_size == 1024


def test_cosine_helper():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_wire_id_deterministic_and_valid_uuid():
    """逻辑键 → 合法 UUID 且确定性（Qdrant 点 id 只收 uint64/UUID，冒烟实测 400 教训）。"""
    import uuid

    first = _wire_id("smoke_1:mock_interview:aaf20858-51a7-4d53-81f8-46a000f5f333")
    assert first == _wire_id("smoke_1:mock_interview:aaf20858-51a7-4d53-81f8-46a000f5f333")
    assert first != _wire_id("smoke_1:mock_interview:other")
    uuid.UUID(first)  # 非法 UUID 会抛 ValueError


def test_fake_upsert_injects_logical_id():
    """Fake 与 Qdrant 同构：payload 注入 logical_id（读回重建逻辑 id 的依据）。"""
    store = FakeMemoryStore()
    store.upsert(COLLECTION_LONG_TERM, "u1:s1", [0.1] * 32, {"user_id": "u1"})
    point = store.get(COLLECTION_LONG_TERM, "u1:s1")
    assert point["payload"]["logical_id"] == "u1:s1"
