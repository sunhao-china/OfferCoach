"""记忆存储层：Qdrant `oc_` 四集合 + 测试用内存实现。

点 id：应用层统一逻辑字符串键（如 `{user_id}:{session_type}:{session_id}`），
payload 一律带 user_id（查询强制过滤）与 logical_id（回写键）。
Qdrant 点 id 只接受 uint64/UUID：QdrantMemoryStore 在客户端边界把逻辑键
确定性映射为 uuid5（不可逆），读回时用 payload.logical_id 重建逻辑 id，
因此协议两侧看到的都是逻辑键，wire 形式不外泄。
时间字段用 epoch 秒（float），便于状态机比较。QdrantMemoryStore 懒连接
（首个操作才建客户端），建集合按 embedder 实际维度。所有方法对缺失点
返回 None / 空列表，不抛错（记忆层 fail-open 的前提）。
"""

from __future__ import annotations

import math
import uuid
from typing import Protocol

# 逻辑键 → UUID 的命名空间（任意固定 UUID；uuid5 确定性映射，Qdrant 合法点 id）
LOGICAL_NAMESPACE = uuid.UUID("6f666665-7263-6f61-6368-6d65-6d6f7279")


def _wire_id(point_id: str) -> str:
    """逻辑键 → Qdrant 合法 UUID（确定性、不可逆；回读靠 payload.logical_id）。"""
    return str(uuid.uuid5(LOGICAL_NAMESPACE, point_id))

COLLECTION_LONG_TERM = "oc_long_term_memory"
COLLECTION_PROFILE = "oc_profile_memory"
COLLECTION_CANDIDATES = "oc_profile_candidates"
COLLECTION_ARCHIVE = "oc_profile_archive"
COLLECTIONS = (
    COLLECTION_LONG_TERM,
    COLLECTION_PROFILE,
    COLLECTION_CANDIDATES,
    COLLECTION_ARCHIVE,
)

Point = dict  # {"id": str, "payload": dict, "vector": list[float] | None}


class MemoryStore(Protocol):
    def ensure_collections(self, vector_size: int) -> None: ...

    def upsert(
        self, collection: str, point_id: str, vector: list[float], payload: dict
    ) -> None: ...

    def get(self, collection: str, point_id: str) -> Point | None: ...

    def list_by_user(self, collection: str, user_id: str) -> list[Point]: ...

    def list_all(self, collection: str) -> list[Point]: ...

    def search(
        self, collection: str, user_id: str, vector: list[float], limit: int
    ) -> list[Point]: ...

    def touch(self, collection: str, point_id: str, payload: dict) -> None: ...

    def delete(self, collection: str, point_id: str) -> None: ...


class FakeMemoryStore:
    """内存实现：语义与 Qdrant 客户端对齐（user 过滤 / 相似度搜索 / 幂等 upsert）。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Point]] = {name: {} for name in COLLECTIONS}

    def ensure_collections(self, vector_size: int) -> None:
        self._vector_size = vector_size

    def upsert(self, collection: str, point_id: str, vector: list[float], payload: dict) -> None:
        stored = dict(payload)
        stored["logical_id"] = point_id  # 与 Qdrant 实现同构：payload 携带回写键
        self._data[collection][point_id] = {
            "id": point_id,
            "payload": stored,
            "vector": vector,
        }

    def get(self, collection: str, point_id: str) -> Point | None:
        return self._data[collection].get(point_id)

    def list_by_user(self, collection: str, user_id: str) -> list[Point]:
        return [
            point
            for point in self._data[collection].values()
            if point["payload"].get("user_id") == user_id
        ]

    def list_all(self, collection: str) -> list[Point]:
        return list(self._data[collection].values())

    def search(self, collection: str, user_id: str, vector: list[float], limit: int) -> list[Point]:
        scored = []
        for point in self._data[collection].values():
            if point["payload"].get("user_id") != user_id or point["vector"] is None:
                continue
            score = _cosine(vector, point["vector"])
            scored.append((score, point, score))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "id": point["id"],
                "payload": point["payload"],
                "vector": point["vector"],
                "score": score,
            }
            for _, point, score in scored[:limit]
        ]

    def touch(self, collection: str, point_id: str, payload: dict) -> None:
        point = self._data[collection].get(point_id)
        if point is not None:
            point["payload"].update(payload)

    def delete(self, collection: str, point_id: str) -> None:
        self._data[collection].pop(point_id, None)


class QdrantMemoryStore:
    """真实 Qdrant 客户端封装（URL 模式连 127.0.0.1:6333，不碰 fin 集合）。"""

    def __init__(self, url: str = "http://127.0.0.1:6333") -> None:
        self._url = url
        self._client = None

    def _qdrant(self):
        if self._client is None:
            from qdrant_client import QdrantClient  # 惰性 import

            self._client = QdrantClient(url=self._url)
        return self._client

    def ensure_collections(self, vector_size: int) -> None:
        from qdrant_client.http.models import Distance, VectorParams

        client = self._qdrant()
        for name in COLLECTIONS:
            if not client.collection_exists(name):
                client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                )

    def upsert(self, collection: str, point_id: str, vector: list[float], payload: dict) -> None:
        from qdrant_client.http.models import PointStruct

        stored = dict(payload)
        stored["logical_id"] = point_id
        self._qdrant().upsert(
            collection_name=collection,
            points=[PointStruct(id=_wire_id(point_id), vector=vector, payload=stored)],
        )

    def get(self, collection: str, point_id: str) -> Point | None:
        # with_vectors=True：晋升/合并需要候选点的原始向量
        records = self._qdrant().retrieve(
            collection_name=collection, ids=[_wire_id(point_id)], with_vectors=True
        )
        if not records:
            return None
        record = records[0]
        payload = dict(record.payload or {})
        return {
            "id": payload.get("logical_id", record.id),
            "payload": payload,
            "vector": record.vector,
        }

    def list_by_user(self, collection: str, user_id: str) -> list[Point]:
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        points, _ = self._qdrant().scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=256,
            with_vectors=False,
        )
        return [
            {
                "id": (point.payload or {}).get("logical_id", point.id),
                "payload": dict(point.payload or {}),
                "vector": None,
            }
            for point in points
        ]

    def list_all(self, collection: str) -> list[Point]:
        points, _ = self._qdrant().scroll(collection_name=collection, limit=256, with_vectors=False)
        return [
            {
                "id": (point.payload or {}).get("logical_id", point.id),
                "payload": dict(point.payload or {}),
                "vector": None,
            }
            for point in points
        ]

    def search(self, collection: str, user_id: str, vector: list[float], limit: int) -> list[Point]:
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        hits = self._qdrant().search(
            collection_name=collection,
            query_vector=vector,
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=limit,
        )
        return [
            {
                "id": (hit.payload or {}).get("logical_id", hit.id),
                "payload": dict(hit.payload or {}),
                "vector": None,
                "score": float(hit.score),
            }
            for hit in hits
        ]

    def touch(self, collection: str, point_id: str, payload: dict) -> None:
        # set_payload 只更新 payload 不动向量（避免 list_all/scroll 取不到向量时被零向量覆盖）
        self._qdrant().set_payload(
            collection_name=collection, payload=dict(payload), points=[_wire_id(point_id)]
        )

    def delete(self, collection: str, point_id: str) -> None:
        self._qdrant().delete(collection_name=collection, points_selector=[_wire_id(point_id)])


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)
