"""记忆装配器：画像 + 长期记忆 → 提示词注入文本块（ContextPackage）。

画像：active 条目按 importance 降序截断 5 条。
长期：最近 2 场时间序 + 当前 query 语义 top-3 召回（去重合并）；
embedding 抛错 → 降级为纯时间序（fail-open，不阻断业务）。
命中点刷新 last_recalled_at / recall_count / status=active（touch 只动 payload）。
"""

from __future__ import annotations

import time
from typing import Any

from app.memory.store import COLLECTION_LONG_TERM, COLLECTION_PROFILE, MemoryStore

PROFILE_LIMIT = 5
RECENT_LIMIT = 2
RECALL_LIMIT = 3


def _importance(point: dict) -> float:
    try:
        return float(point["payload"].get("importance") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _created_at(point: dict) -> float:
    try:
        return float(point["payload"].get("created_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _brief(summary: dict) -> str:
    """长期记忆点的一句话摘要（注入用，控制长度）。"""
    parts = [
        str(item.get("point") or "")
        for item in summary.get("key_content") or []
        if item.get("point")
    ]
    if not parts:
        strengths = summary.get("strengths") or []
        return str(strengths[0]) if strengths else ""
    return "；".join(part for part in parts[:3] if part)


class MemoryAssembler:
    """无状态装配：输入 user_id + 当前 query，输出注入文本块（空串 = 无记忆）。"""

    def __init__(self, store: MemoryStore, embedder: Any, *, clock: Any = time.time) -> None:
        self._store = store
        self._embedder = embedder
        self._clock = clock

    def assemble(self, user_id: str, query: str, *, now: float | None = None) -> str:
        now = float(now if now is not None else self._clock())
        blocks: list[str] = []
        profile_lines = self._profile_lines(user_id)
        if profile_lines:
            blocks.append("用户画像（跨会话观察，供参考）：\n" + "\n".join(profile_lines))
        recent, recalled = self._long_term_points(user_id, query)
        merged: dict[str, dict] = {}
        for point in (*recalled, *recent):
            merged[str(point["id"])] = point
        if merged:
            for point in merged.values():
                self._refresh(point, now)
            lines = [
                f"- {point['payload'].get('topic') or '训练回顾'}："
                f"{_brief(point['payload'].get('summary') or {})}"
                for point in sorted(merged.values(), key=_created_at, reverse=True)
            ]
            blocks.append("近期训练回顾：\n" + "\n".join(lines))
        return "\n\n".join(blocks)

    def _profile_lines(self, user_id: str) -> list[str]:
        profiles = [
            point
            for point in self._store.list_by_user(COLLECTION_PROFILE, user_id)
            if point["payload"].get("status") == "active"
        ]
        profiles.sort(key=_importance, reverse=True)
        return [
            f"- {point['payload'].get('statement') or ''}".strip()
            for point in profiles[:PROFILE_LIMIT]
        ]

    def _long_term_points(
        self, user_id: str, query: str
    ) -> tuple[list[dict], list[dict]]:
        """(最近 2 场时间序, 语义 top-k)；embedding 失败 → 仅时间序。"""
        longs = self._store.list_by_user(COLLECTION_LONG_TERM, user_id)
        longs.sort(key=_created_at, reverse=True)
        recent = longs[:RECENT_LIMIT]
        try:
            vector = list(self._embedder.embed_texts([query])[0])
            recalled = self._store.search(COLLECTION_LONG_TERM, user_id, vector, RECALL_LIMIT)
        except Exception:
            recalled = []  # 降级：语义召回跳过，时间序仍可用
        return recent, recalled

    def _refresh(self, point: dict, now: float) -> None:
        """召回/装配命中刷新：touch 只更新 payload，不覆盖向量。"""
        payload = dict(point["payload"])
        payload["status"] = "active"
        payload["last_recalled_at"] = now
        payload["recall_count"] = int(payload.get("recall_count") or 0) + 1
        self._store.touch(COLLECTION_LONG_TERM, str(point["id"]), payload)
