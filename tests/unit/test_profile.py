"""L4: 画像晋升与遗忘状态机（AC3）。

依据 specs/02-in-progress/architecture/three-tier-memory.md 的 AC3。
"""

from __future__ import annotations

from app.memory.profile import (
    LONG_TERM_INTERVAL,
    PROMOTE_EVIDENCE_MIN,
    forget_candidates,
    forget_long_term,
    forget_profile,
    promote_if_ready,
    sweep_forgetting,
)
from app.memory.store import (
    COLLECTION_ARCHIVE,
    COLLECTION_CANDIDATES,
    COLLECTION_LONG_TERM,
    COLLECTION_PROFILE,
    FakeMemoryStore,
)

NOW = 1_800_000_000.0
DAY = 24 * 3600.0
VECTOR = [0.1] * 32


def _candidate(
    store: FakeMemoryStore,
    entry_id: str = "c1",
    *,
    evidence: int = 1,
    category: str = "skill_weakness",
    user_id: str = "u1",
) -> None:
    store.upsert(
        COLLECTION_CANDIDATES,
        f"{user_id}:{entry_id}",
        VECTOR,
        {
            "memory_type": "candidate",
            "user_id": user_id,
            "entry_id": entry_id,
            "category": category,
            "statement": "CAS 原理薄弱",
            "confidence": 0.7,
            "source": "extracted",
            "evidence": [
                {"session_id": f"s{i}", "round": i, "quote": f"证据{i}"} for i in range(evidence)
            ],
            "status": "pending",
            "created_at": NOW - DAY,
            "updated_at": NOW - DAY,
        },
    )


def _long_term(
    store: FakeMemoryStore,
    point_id: str,
    *,
    created: float,
    status: str = "active",
    importance: float = 0.5,
    last_recalled: float | None = None,
) -> None:
    store.upsert(
        COLLECTION_LONG_TERM,
        point_id,
        VECTOR,
        {
            "memory_type": "long_term",
            "user_id": "u1",
            "category": "chat",
            "topic": point_id,
            "importance": importance,
            "status": status,
            "created_at": created,
            "last_recalled_at": last_recalled if last_recalled is not None else created,
        },
    )


def _profile(
    store: FakeMemoryStore,
    point_id: str,
    *,
    created: float,
    category: str = "skill_strength",
    status: str = "active",
    importance: float = 0.5,
) -> None:
    store.upsert(
        COLLECTION_PROFILE,
        point_id,
        VECTOR,
        {
            "memory_type": "profile",
            "user_id": "u1",
            "category": category,
            "statement": point_id,
            "importance": importance,
            "status": status,
            "created_at": created,
            "last_recalled_at": created,
            "recall_count": 0,
        },
    )


def test_single_evidence_not_promoted():
    store = FakeMemoryStore()
    _candidate(store, evidence=1)
    assert promote_if_ready(store, "u1", "c1", NOW) is False
    assert store.get(COLLECTION_CANDIDATES, "u1:c1") is not None
    assert store.list_by_user(COLLECTION_PROFILE, "u1") == []


def test_two_evidence_promoted_and_candidate_deleted():
    store = FakeMemoryStore()
    _candidate(store, evidence=PROMOTE_EVIDENCE_MIN)
    assert promote_if_ready(store, "u1", "c1", NOW) is True
    assert store.get(COLLECTION_CANDIDATES, "u1:c1") is None
    profile = store.get(COLLECTION_PROFILE, "u1:c1")
    assert profile is not None
    assert profile["payload"]["status"] == "active"
    assert profile["payload"]["memory_type"] == "profile"
    assert len(profile["payload"]["evidence_chain"]) == PROMOTE_EVIDENCE_MIN
    assert profile["payload"]["promoted_at"] == NOW


def test_missing_candidate_promote_noop():
    store = FakeMemoryStore()
    assert promote_if_ready(store, "u1", "ghost", NOW) is False


def test_long_term_active_decays_after_interval():
    store = FakeMemoryStore()
    _long_term(store, "u1:s1", created=NOW - LONG_TERM_INTERVAL - 1)
    assert forget_long_term(store, store.get(COLLECTION_LONG_TERM, "u1:s1"), NOW) == "decayed"
    assert store.get(COLLECTION_LONG_TERM, "u1:s1")["payload"]["status"] == "decayed"


def test_long_term_recent_recall_not_decayed():
    store = FakeMemoryStore()
    _long_term(store, "u1:s1", created=NOW - 10 * DAY, last_recalled=NOW - 1)
    assert forget_long_term(store, store.get(COLLECTION_LONG_TERM, "u1:s1"), NOW) is None


def test_long_term_decayed_deleted_after_double_interval():
    store = FakeMemoryStore()
    _long_term(store, "u1:s1", created=NOW - 2 * LONG_TERM_INTERVAL - 1, status="decayed")
    assert forget_long_term(store, store.get(COLLECTION_LONG_TERM, "u1:s1"), NOW) == "forgotten"
    assert store.get(COLLECTION_LONG_TERM, "u1:s1") is None


def test_high_importance_extends_interval():
    store = FakeMemoryStore()
    created = NOW - LONG_TERM_INTERVAL - 1  # 常规间隔已超
    _long_term(store, "u1:s1", created=created, importance=0.9)  # ×1.5 后未超
    assert forget_long_term(store, store.get(COLLECTION_LONG_TERM, "u1:s1"), NOW) is None


def test_profile_decays_then_archives():
    store = FakeMemoryStore()
    interval = 14 * DAY  # skill_strength
    _profile(store, "u1:p1", created=NOW - interval - 1)
    point = store.get(COLLECTION_PROFILE, "u1:p1")
    assert forget_profile(store, point, NOW) == "decayed"
    assert store.get(COLLECTION_PROFILE, "u1:p1")["payload"]["status"] == "decayed"
    later = NOW + 2 * interval + 1
    point = store.get(COLLECTION_PROFILE, "u1:p1")
    assert forget_profile(store, point, later) == "forgotten"
    assert store.get(COLLECTION_PROFILE, "u1:p1") is None
    archived = store.get(COLLECTION_ARCHIVE, "u1:p1")
    assert archived is not None
    assert archived["payload"]["status"] == "forgotten"
    assert archived["payload"]["forgotten_at"] == later
    assert archived["payload"]["statement"] == "u1:p1"  # payload 完整迁移


def test_candidates_stale_deleted():
    store = FakeMemoryStore()
    _candidate(store, entry_id="stale", evidence=1)
    stale = store.get(COLLECTION_CANDIDATES, "u1:stale")
    assert forget_candidates(store, stale, NOW + 61 * DAY) is True
    assert store.get(COLLECTION_CANDIDATES, "u1:stale") is None
    _candidate(store, entry_id="fresh", evidence=1)
    fresh = store.get(COLLECTION_CANDIDATES, "u1:fresh")
    assert forget_candidates(store, fresh, NOW + 1 * DAY) is False


def test_sweep_counts_decayed_forgotten_promoted():
    store = FakeMemoryStore()
    _long_term(store, "u1:s1", created=NOW - LONG_TERM_INTERVAL - 1)  # → decayed
    _profile(store, "u1:p1", created=NOW - 30 * DAY - 1, category="identity")  # → decayed
    _candidate(store, entry_id="c1", evidence=2)  # → promoted
    counts = sweep_forgetting(store, NOW)
    assert counts["decayed"] == 2
    assert counts["promoted"] == 1
