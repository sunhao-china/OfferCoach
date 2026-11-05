"""会话总结器：LLM 提炼 → 长期记忆点 + 画像候选（语义合并 + 晋升检查）。

幂等：长期记忆点 id = `{user_id}:{session_id}`，已存在直接跳过。
解析失败降级：importance=0.2 的极简点（流程继续，fail-open）。
候选合并：同用户同 category 语义相似度 ≥ 0.85 → 追加证据（去重、上限 5），
否则新建候选点；证据 ≥2 立即触发晋升检查。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.graphs.json_utils import parse_json_text
from app.memory.profile import promote_if_ready
from app.memory.store import (
    COLLECTION_CANDIDATES,
    COLLECTION_LONG_TERM,
    MemoryStore,
    _cosine,
)

PROMPT_SUMMARIZE = "memory/summarize"
CANDIDATE_SIMILARITY = 0.85  # 语义合并阈值（cosine）
EVIDENCE_LIMIT = 5  # 单候选证据上限（晋升只需 ≥2 条）
CONFIDENCE_START = 0.7
CONFIDENCE_STEP = 0.05
CONFIDENCE_CAP = 0.9
FALLBACK_IMPORTANCE = 0.2  # LLM 解析失败时的重要性兜底

CATEGORIES = ("identity", "skill_strength", "skill_weakness", "preference", "goal")

# 消息摘录上限：条数与单条长度（防 token 膨胀）
EXCERPT_MAX_MESSAGES = 40
EXCERPT_MAX_CHARS = 300


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else str(content or "")


def build_excerpt(messages: list[Any]) -> str:
    """会话记录摘录：`角色: 文本` 行；角色取类型名（Human/AI/Tool/System）。"""
    lines: list[str] = []
    for message in messages[-EXCERPT_MAX_MESSAGES:]:
        role = type(message).__name__.replace("Message", "")
        lines.append(f"{role}: {_message_text(message)[:EXCERPT_MAX_CHARS]}")
    return "\n".join(lines)


def summarize_text(payload: dict) -> str:
    """摘要文本（embedding 用）：主题 + 要点 + 短板/亮点拼接。"""
    summary = payload.get("summary") or {}
    parts = [str(payload.get("topic") or "")]
    for point in summary.get("key_content") or []:
        if isinstance(point, dict) and point.get("point"):
            parts.append(str(point["point"]))
    parts.extend(str(item) for item in (summary.get("weak_points") or []))
    parts.extend(str(item) for item in (summary.get("strengths") or []))
    return " ".join(part for part in parts if part)


class SessionSummarizer:
    """强档 LLM 总结一次会话 → 长期点 + 候选点；全部异常由调用方（service）兜底。"""

    def __init__(
        self,
        store: MemoryStore,
        embedder: Any,
        llm: Any,
        registry: Any,
        *,
        clock: Any = time.time,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._llm = llm
        self._registry = registry
        self._clock = clock

    def summarize(
        self,
        user_id: str,
        session_id: str,
        session_type: str,
        messages: list[Any],
        *,
        extra_context: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """总结一场会话；已存在长期点（幂等）或消息为空时返回 {"skipped": True}。"""
        now = float(now if now is not None else self._clock())
        # 类型入键：interview 与其复盘（同 target）各自成点，避免互相覆盖
        point_id = f"{user_id}:{session_type}:{session_id}"
        if self._store.get(COLLECTION_LONG_TERM, point_id) is not None:
            return {"skipped": True, "reason": "exists"}
        if not messages:
            return {"skipped": True, "reason": "no_messages"}
        excerpt = build_excerpt(messages)
        system = self._registry.render(
            PROMPT_SUMMARIZE,
            session_type=session_type,
            session_id=session_id,
            extra_context=extra_context or "（无）",
            messages=excerpt,
        )
        raw = self._llm.invoke([system]).content
        data = parse_json_text(str(raw))
        if data is None:
            data = {
                "topic": f"{session_type} 会话（自动总结解析失败，降级保存）",
                "importance": FALLBACK_IMPORTANCE,
                "summary": {
                    "key_content": [],
                    "weak_points": [],
                    "strengths": [],
                    "next_practice_suggestions": [],
                },
                "profile_candidates": [],
            }
        payload = {
            "memory_type": "long_term",
            "user_id": user_id,
            "entry_id": session_id,
            "session_id": session_id,
            "session_type": session_type,
            "topic": str(data.get("topic") or ""),
            "importance": float(data.get("importance") or 0.0),
            "summary": data.get("summary") or {},
            "status": "active",
            "created_at": now,
            "last_recalled_at": now,
            "recall_count": 0,
        }
        vector = self._embed_statement(summarize_text(payload))
        self._store.upsert(COLLECTION_LONG_TERM, point_id, vector, payload)
        promoted = 0
        skipped_candidates = 0
        for candidate in data.get("profile_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            if self._upsert_candidate(user_id, session_id, candidate, now):
                promoted += 1
            else:
                skipped_candidates += 1
        return {
            "created": True,
            "point_id": point_id,
            "candidates": len(data.get("profile_candidates") or []),
            "promoted": promoted,
            "skipped_candidates": skipped_candidates,
        }

    def _embed_statement(self, text: str) -> list[float]:
        """embedding 失败 → 零向量占位（fail-open：点照常保存，语义召回自动降级）。

        零向量点仅在 embedding 恢复后仍以低分参与搜索，不影响时间序兜底。
        """
        try:
            return list(self._embedder.embed_texts([text])[0])
        except Exception:
            size = getattr(self._embedder, "vector_size", 0)
            return [0.0] * (size if isinstance(size, int) and size > 0 else 1024)

    def _upsert_candidate(
        self, user_id: str, session_id: str, candidate: dict, now: float
    ) -> bool:
        """单条画像候选：语义合并或新建；随后晋升检查。返回是否晋升。"""
        category = str(candidate.get("category") or "")
        statement = str(candidate.get("statement") or "").strip()
        if category not in CATEGORIES or not statement:
            return False
        raw_evidence = candidate.get("evidence") or []
        evidence = [
            {
                "session_id": str(item.get("session_id") or session_id),
                "round": item.get("round"),
                "quote": str(item.get("quote") or ""),
            }
            for item in raw_evidence
            if isinstance(item, dict) and str(item.get("quote") or "")
        ]
        if not evidence:
            return False
        try:
            vector = list(self._embedder.embed_texts([statement])[0])
        except Exception:
            # embedding 不可用 → 无法语义合并，跳过候选（避免错误合并污染画像）
            return False
        hits = self._store.search(COLLECTION_CANDIDATES, user_id, vector, limit=3)
        merged = next(
            (
                hit
                for hit in hits
                if hit["payload"].get("category") == category
                and (hit.get("score") is not None or hit.get("vector") is not None)
            ),
            None,
        )
        if merged is not None:
            hit_score = float(merged.get("score") or 0.0)
            if hit_score < CANDIDATE_SIMILARITY and merged.get("vector") is not None:
                hit_score = _cosine(vector, merged["vector"])
            if hit_score < CANDIDATE_SIMILARITY:
                merged = None
        if merged is not None:
            payload = dict(merged["payload"])
            merged_evidence = self._merge_evidence(payload.get("evidence") or [], evidence)
            payload["evidence"] = merged_evidence
            payload["confidence"] = min(
                CONFIDENCE_CAP,
                CONFIDENCE_START + CONFIDENCE_STEP * (len(merged_evidence) - 1),
            )
            payload["updated_at"] = now
            self._store.touch(COLLECTION_CANDIDATES, merged["id"], payload)
            entry_id = str(merged["id"]).split(":", 1)[1]
        else:
            entry_id = uuid.uuid4().hex[:8]
            self._store.upsert(
                COLLECTION_CANDIDATES,
                f"{user_id}:{entry_id}",
                vector,
                {
                    "memory_type": "candidate",
                    "user_id": user_id,
                    "entry_id": entry_id,
                    "category": category,
                    "statement": statement,
                    "confidence": CONFIDENCE_START,
                    "source": "extracted",
                    "evidence": evidence,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                },
            )
        return promote_if_ready(self._store, user_id, entry_id, now)

    @staticmethod
    def _merge_evidence(existing: list[dict], incoming: list[dict]) -> list[dict]:
        """证据合并去重（session_id+quote 为键），保留顺序、上限 EVIDENCE_LIMIT。"""
        merged: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for item in (*existing, *incoming):
            key = (str(item.get("session_id") or ""), str(item.get("quote") or ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged[:EVIDENCE_LIMIT]


def evidence_count(payload: dict) -> int:
    """候选点证据条数（晋升判定与测试断言共用）。"""
    return len(payload.get("evidence") or [])
