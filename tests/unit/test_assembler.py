"""L4: 记忆装配器（AC4）。

依据 specs/02-in-progress/architecture/three-tier-memory.md 的 AC4。
"""

from __future__ import annotations

from app.core.embedding import DeterministicEmbedder
from app.memory.assembler import MemoryAssembler
from app.memory.store import (
    COLLECTION_LONG_TERM,
    COLLECTION_PROFILE,
    FakeMemoryStore,
)


class FailingEmbedder:
    """embedding 服务宕机桩：装配必须降级时间序而非抛错。"""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding down")


def _profile(store, point_id, *, statement, importance, status="active") -> None:
    store.upsert(
        COLLECTION_PROFILE,
        point_id,
        [0.1] * 32,
        {
            "memory_type": "profile",
            "user_id": "u1",
            "category": "preference",
            "statement": statement,
            "importance": importance,
            "status": status,
            "created_at": 100.0,
            "last_recalled_at": 100.0,
            "recall_count": 0,
        },
    )


def _long_term(store, point_id, topic, created) -> None:
    embedder = DeterministicEmbedder()
    store.upsert(
        COLLECTION_LONG_TERM,
        point_id,
        embedder.embed_texts([topic])[0],
        {
            "memory_type": "long_term",
            "user_id": "u1",
            "session_type": "chat",
            "topic": topic,
            "importance": 0.5,
            "summary": {"key_content": [{"point": topic}]},
            "status": "active",
            "created_at": created,
            "last_recalled_at": created,
            "recall_count": 0,
        },
    )


def _assembler(store, embedder=None):
    return MemoryAssembler(store, embedder or DeterministicEmbedder())


def test_profile_active_injected_sorted_and_capped():
    store = FakeMemoryStore()
    store.ensure_collections(32)
    for index in range(6):
        _profile(store, f"u1:p{index}", statement=f"画像{index}", importance=0.1 * index)
    _profile(store, "u1:inactive", statement="已遗忘画像", importance=0.9, status="decayed")
    text = _assembler(store).assemble("u1", "随便问", now=1000.0)
    assert "用户画像" in text
    for index in range(1, 6):  # importance 最高的 5 条（0.1-0.5），最低的画像0 被截断
        assert f"画像{index}" in text
    assert "画像0" not in text
    assert "已遗忘画像" not in text  # 非 active 不注入


def test_recent_two_time_order_plus_semantic_recall_merged():
    store = FakeMemoryStore()
    store.ensure_collections(32)
    _long_term(store, "u1:s1", "线程池调优", created=1000.0)
    _long_term(store, "u1:s2", "JVM 内存模型", created=2000.0)
    _long_term(store, "u1:s3", "HashMap 源码", created=3000.0)
    # 语义 query 命中 s1（确定性向量：query 与 s1 topic 同文本 → cosine 1.0）
    text = _assembler(store).assemble("u1", "线程池调优", now=4000.0)
    assert "近期训练回顾" in text
    assert "- HashMap 源码" in text  # 最近 2 场：s3/s2
    assert "- 线程池调优" in text  # 语义召回 s1
    assert text.count("- 线程池调优") == 1  # 去重合并（同一点只出现一次）


def test_embedding_failure_degrades_to_time_order():
    store = FakeMemoryStore()
    store.ensure_collections(32)
    _long_term(store, "u1:s1", "线程池调优", created=1000.0)
    _long_term(store, "u1:s2", "JVM 内存模型", created=2000.0)
    _long_term(store, "u1:s3", "HashMap 源码", created=3000.0)
    text = _assembler(store, FailingEmbedder()).assemble("u1", "线程池调优", now=4000.0)
    assert "- HashMap 源码" in text and "- JVM 内存模型" in text  # 最近 2 场
    assert "- 线程池调优" not in text  # 语义召回不可用（s1 不进最近 2 场）
    assert text.count("- ") == 2  # 只有时间序 2 场


def test_recall_refreshes_last_recalled_and_count():
    store = FakeMemoryStore()
    store.ensure_collections(32)
    _long_term(store, "u1:s1", "线程池调优", created=1000.0)
    _assembler(store).assemble("u1", "线程池调优", now=5000.0)
    point = store.get(COLLECTION_LONG_TERM, "u1:s1")
    assert point["payload"]["last_recalled_at"] == 5000.0
    assert point["payload"]["recall_count"] == 1
    assert point["payload"]["status"] == "active"
    assert point["vector"] != [0.0] * 32  # touch 不覆盖向量


def test_empty_memory_returns_empty_string():
    store = FakeMemoryStore()
    store.ensure_collections(32)
    assert _assembler(store).assemble("u1", "随便问", now=1000.0) == ""
