"""画像晋升与遗忘状态机（纯函数层，FakeMemoryStore 可测）。

晋升：候选点证据 ≥2 条独立证据 → profile_memory 新点 + 删候选点。
遗忘：active 超间隔 → decayed；decayed 再超间隔 → forgotten
（长期记忆直接删点；画像移入 archive 审计）。间隔按类别，importance ≥ 0.8 × 1.5。
"""

from __future__ import annotations

from app.memory.store import (
    COLLECTION_ARCHIVE,
    COLLECTION_CANDIDATES,
    COLLECTION_LONG_TERM,
    COLLECTION_PROFILE,
    MemoryStore,
)

PROMOTE_EVIDENCE_MIN = 2  # extracted 晋升门槛：≥2 条独立证据
CANDIDATE_STALE_SECONDS = 60 * 24 * 3600  # 候选 60 天无新证据 → 淘汰

LONG_TERM_INTERVAL = 30 * 24 * 3600.0
PROFILE_INTERVALS = {
    "identity": 30 * 24 * 3600.0,
    "goal": 30 * 24 * 3600.0,
    "skill_strength": 14 * 24 * 3600.0,
    "skill_weakness": 14 * 24 * 3600.0,
    "preference": 14 * 24 * 3600.0,
}
IMPORTANCE_MULTIPLIER = 1.5  # importance ≥ 0.8 的间隔放大系数


def promote_if_ready(store: MemoryStore, user_id: str, entry_id: str, now: float) -> bool:
    """候选证据达标即晋升；返回是否晋升。"""
    candidate = store.get(COLLECTION_CANDIDATES, f"{user_id}:{entry_id}")
    if candidate is None:
        return False
    evidence = candidate["payload"].get("evidence") or []
    if len(evidence) < PROMOTE_EVIDENCE_MIN:
        return False
    payload = dict(candidate["payload"])
    payload.update(
        {
            "memory_type": "profile",
            "evidence_chain": evidence,
            "status": "active",
            "last_recalled_at": payload.get("last_recalled_at", now),
            "recall_count": payload.get("recall_count", 0),
            "promoted_at": now,
        }
    )
    vector = candidate["vector"] or [0.0]
    store.upsert(COLLECTION_PROFILE, f"{user_id}:{entry_id}", vector, payload)
    store.delete(COLLECTION_CANDIDATES, f"{user_id}:{entry_id}")
    return True


def _interval_seconds(payload: dict) -> float:
    importance = float(payload.get("importance") or 0.0)
    base = PROFILE_INTERVALS.get(str(payload.get("category")), LONG_TERM_INTERVAL)
    if importance >= 0.8:
        base *= IMPORTANCE_MULTIPLIER
    return base


def forget_long_term(store: MemoryStore, point: dict, now: float) -> str | None:
    """长期记忆遗忘：active → decayed →（再超间隔）直接删点。返回发生的变化。"""
    payload = point["payload"]
    interval = _interval_seconds(payload)
    last = float(payload.get("last_recalled_at") or payload.get("created_at") or 0.0)
    if payload.get("status") == "active" and now - last > interval:
        payload["status"] = "decayed"
        store.touch(COLLECTION_LONG_TERM, point["id"], payload)
        return "decayed"
    if payload.get("status") == "decayed" and now - last > 2 * interval:
        store.delete(COLLECTION_LONG_TERM, point["id"])
        return "forgotten"
    return None


def forget_profile(store: MemoryStore, point: dict, now: float) -> str | None:
    """画像遗忘：active → decayed → forgotten 移入 archive（保留证据链审计）。"""
    payload = point["payload"]
    interval = _interval_seconds(payload)
    last = float(payload.get("last_recalled_at") or payload.get("created_at") or 0.0)
    if payload.get("status") == "active" and now - last > interval:
        payload["status"] = "decayed"
        store.touch(COLLECTION_PROFILE, point["id"], payload)
        return "decayed"
    if payload.get("status") == "decayed" and now - last > 2 * interval:
        payload["status"] = "forgotten"
        payload["forgotten_at"] = now
        # archive 只做审计不参与搜索，向量有无不影响；list_all 无向量时零向量占位
        store.upsert(COLLECTION_ARCHIVE, point["id"], point["vector"] or [0.0], payload)
        store.delete(COLLECTION_PROFILE, point["id"])
        return "forgotten"
    return None


def forget_candidates(store: MemoryStore, point: dict, now: float) -> bool:
    """候选淘汰：60 天无新证据 → 删除；返回是否删除。"""
    updated = float(point["payload"].get("updated_at") or point["payload"].get("created_at") or 0.0)
    if now - updated > CANDIDATE_STALE_SECONDS:
        store.delete(COLLECTION_CANDIDATES, point["id"])
        return True
    return False


def sweep_forgetting(store: MemoryStore, now: float) -> dict[str, int]:
    """T2 遗忘扫描：三集合全量状态机推进。返回各动作计数（冒烟/日志用）。"""
    counts = {"decayed": 0, "forgotten": 0, "promoted": 0}
    for point in store.list_all(COLLECTION_LONG_TERM):
        if forget_long_term(store, point, now) == "decayed":
            counts["decayed"] += 1
        # 长期记忆 forgotten 即删除，不单列计数（decayed 记录状态推进即可）
    for point in store.list_all(COLLECTION_PROFILE):
        change = forget_profile(store, point, now)
        if change == "decayed":
            counts["decayed"] += 1
        elif change == "forgotten":
            counts["forgotten"] += 1
    for point in store.list_all(COLLECTION_CANDIDATES):
        forget_candidates(store, point, now)
        user_id = str(point["payload"].get("user_id") or "")
        raw_id = str(point["id"])
        entry_id = raw_id.split(":", 1)[1] if ":" in raw_id else raw_id
        if promote_if_ready(store, user_id, entry_id, now):
            counts["promoted"] += 1
    return counts
