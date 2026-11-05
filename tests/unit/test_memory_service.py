"""L4: MemoryService 门面 — fail-open 兜底（请求路径不可被记忆故障阻断）。

依据 specs/02-in-progress/architecture/three-tier-memory.md 的 fail-open 要求。
"""

from __future__ import annotations

from app.core.embedding import DeterministicEmbedder
from app.memory.service import MemoryService
from app.memory.store import FakeMemoryStore
from tests.conftest import StubLLM


class BrokenStore(FakeMemoryStore):
    """存储全故障桩：任何方法抛错（模拟 Qdrant 不可达）。"""

    def ensure_collections(self, vector_size: int) -> None:
        raise RuntimeError("qdrant down")

    def list_by_user(self, collection: str, user_id: str):
        raise RuntimeError("qdrant down")


def _service(registry, store):
    return MemoryService(
        store=store,
        embedder=DeterministicEmbedder(),
        llm=StubLLM([]),
        registry=registry,
        checkpointer=None,
        sessions=None,
    )


def test_assemble_swallows_store_failure(registry):
    """请求路径上的装配：存储异常 → 空串 + 记录，不抛出。"""
    service = _service(registry, BrokenStore())
    assert service.assemble("u1", "随便问") == ""
    assert service.errors and "assemble" in service.errors[-1]


def test_ensure_failure_records_and_returns_false(registry):
    service = _service(registry, BrokenStore())
    assert service.ensure() is False
    assert service.errors and "ensure" in service.errors[-1]


def test_ensure_success_sets_vector_size(registry):
    service = _service(registry, FakeMemoryStore())
    assert service.ensure() is True
    assert service._embedder.vector_size == 32
