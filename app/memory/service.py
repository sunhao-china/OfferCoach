"""MemoryService 门面：事件订阅即时总结 + T1 兜底 + T2 遗忘 + 装配入口。

全部 fail-open：任何异常记录到 errors（上限 20 条）并继续，不阻断业务。
幂等键：点 id = `{user_id}:{session_type}:{canonical_id}`——canonical_id 即
executor 写入子图 state 的 session_id（chat/interview/resume 为会话行 id，
review 为复盘目标 id）。类型入键的原因：interview 与复盘同一 target 的会话
各自成点，若只按 `{user}:{session_id}` 会互相覆盖（复盘内容永不落库）。
事件总结在 daemon 线程执行（不阻塞 graph 的 fire 主流程），锁串行化。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from app.graphs.checkpoints import read_checkpoint
from app.hooks.bus import (
    EVENT_ERROR,
    EVENT_INTERVIEW_REVIEW_COMPLETE,
    EVENT_MOCK_INTERVIEW_END,
    EVENT_RESUME_ANALYSIS_COMPLETE,
)
from app.memory.assembler import MemoryAssembler
from app.memory.profile import sweep_forgetting
from app.memory.store import COLLECTION_LONG_TERM, MemoryStore
from app.memory.summarizer import SessionSummarizer

# 事件名 → 会话类型（与 composition 子图名/executor business 对应）
SESSION_TYPE_BY_EVENT = {
    EVENT_MOCK_INTERVIEW_END: "mock_interview",
    EVENT_RESUME_ANALYSIS_COMPLETE: "resume_analysis",
    EVENT_INTERVIEW_REVIEW_COMPLETE: "interview_review",
}
BUSINESS_TO_SESSION_TYPE = {
    "chat": "chat",
    "interview": "mock_interview",
    "resume": "resume_analysis",
    "review": "interview_review",
}
# business → 检查点线程后缀（与 app/api/executor.py BUSINESS_SUBGRAPH 一致）
SUFFIX_BY_SESSION_TYPE = {
    "mock_interview": "mock_interview",
    "resume_analysis": "resume_analysis",
    "interview_review": "interview_review",
}

T1_IDLE_SECONDS = 30 * 60  # 消息静默 30 分钟 → 兜底总结
ERRORS_LIMIT = 20
EVENT_PRIORITY = 200  # 低优先级：其他处理器先跑，总结不挡路


class MemoryService:
    def __init__(
        self,
        *,
        store: MemoryStore,
        embedder: Any,
        llm: Any,
        registry: Any,
        checkpointer: Any,
        sessions: Any,
        bus: Any = None,
        clock: Any = time.time,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._bus = bus
        self._clock = clock
        self._lock = threading.Lock()
        self._errors: deque[str] = deque(maxlen=ERRORS_LIMIT)
        self._summarizer = SessionSummarizer(store, embedder, llm, registry, clock=clock)
        self._assembler = MemoryAssembler(store, embedder, clock=clock)
        self._checkpointer = checkpointer
        self._sessions = sessions

    # ---- 启动 ----

    def ensure(self) -> bool:
        """探测 embedding 维度并建 `oc_` 四集合；失败记录并返回 False（不阻断启动）。"""
        try:
            probe = list(self._embedder.embed_texts(["probe"])[0])
            self._embedder.vector_size = len(probe)
            self._store.ensure_collections(len(probe))
            return True
        except Exception as exc:
            self.record_error(f"ensure: {exc}")
            return False

    def register(self) -> None:
        """订阅会话结束事件（bus 为 None 时跳过，测试零行为）。"""
        if self._bus is None:
            return
        for event in SESSION_TYPE_BY_EVENT:
            self._bus.register(event, self._on_end_event, priority=EVENT_PRIORITY)

    # ---- 事件驱动总结 ----

    def _on_end_event(self, hook_event: Any) -> None:
        payload = hook_event.payload or {}
        user_id = str(payload.get("user_id") or "")
        canonical_id = str(payload.get("session_id") or "")
        session_type = SESSION_TYPE_BY_EVENT.get(hook_event.name, "chat")
        if not user_id or not canonical_id:
            return
        # daemon 线程：fire 主流程（graph 执行线程）不被总结的 LLM 调用拖慢
        threading.Thread(
            target=self._safe_summarize,
            args=(user_id, canonical_id, session_type),
            daemon=True,
        ).start()

    def _safe_summarize(self, user_id: str, canonical_id: str, session_type: str) -> None:
        try:
            with self._lock:
                self.summarize_session(user_id, canonical_id, session_type)
        except Exception as exc:
            self.record_error(f"summarize {user_id}:{canonical_id}: {exc}")
            if self._bus is not None:
                self._bus.fire(
                    EVENT_ERROR,
                    payload={"node": "memory", "session_id": canonical_id, "raw": str(exc)[:200]},
                )

    # ---- 总结 / T1 / T2 / 装配 ----

    def summarize_session(
        self,
        user_id: str,
        canonical_id: str,
        session_type: str,
        *,
        extra_context: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """读检查点消息 → 总结。缺消息或已有点返回 {"skipped": True}（幂等）。"""
        messages = self._read_messages(canonical_id, session_type)
        return self._summarizer.summarize(
            user_id,
            canonical_id,
            session_type,
            messages,
            extra_context=extra_context,
            now=now,
        )

    def _read_messages(self, canonical_id: str, session_type: str) -> list[Any]:
        namespaces: tuple[str, ...]
        if session_type == "chat":
            thread_id, namespaces = canonical_id, ("",)
        else:
            name = SUFFIX_BY_SESSION_TYPE[session_type]
            thread_id = f"{canonical_id}:{name}"
            # 首候选 ns=子图名（根图嵌套路径），兜底 ns=""（API 直连 invoke 路径）
            namespaces = (name, "")
        snapshot = read_checkpoint(self._checkpointer, thread_id, namespaces)
        if snapshot is None:
            return []
        # CheckpointTuple 无 values 属性：消息在 checkpoint.channel_values（与 read_history 同）
        values = snapshot.checkpoint.get("channel_values", {}) or {}
        return list(values.get("messages") or [])

    def sweep_t1(self, cutoff: datetime | None = None) -> dict[str, int]:
        """T1：静默超时且无长期点的会话兜底总结。返回 {"summarized": n}。"""
        try:
            cutoff = cutoff or (datetime.now() - timedelta(seconds=T1_IDLE_SECONDS))
            summarized = 0
            for row in self._sessions.list_stale(cutoff):
                session_type = BUSINESS_TO_SESSION_TYPE.get(str(row.business), "chat")
                # review 的检查点线程与幂等键都以 target 为准（与事件路径一致）
                canonical_id = str(row.target_session_id or row.id)
                point_id = self._point_id(str(row.user_id), session_type, canonical_id)
                if self._store.get(COLLECTION_LONG_TERM, point_id) is not None:
                    continue
                result = self.summarize_session(str(row.user_id), canonical_id, session_type)
                if result.get("created"):
                    summarized += 1
            return {"summarized": summarized}
        except Exception as exc:
            self.record_error(f"sweep_t1: {exc}")
            return {"summarized": 0}

    def sweep_t2(self, now: float | None = None) -> dict[str, int]:
        """T2：遗忘 + 晋升扫描。"""
        try:
            return sweep_forgetting(self._store, float(now if now is not None else self._clock()))
        except Exception as exc:
            self.record_error(f"sweep_t2: {exc}")
            return {"decayed": 0, "forgotten": 0, "promoted": 0}

    def assemble(self, user_id: str, query: str) -> str:
        """装配注入文本块（空串 = 无记忆）。

        本方法在请求路径上（react/opening 节点调用）：任何存储/embedding 异常
        都必须吞掉返回空串（fail-open），不能让记忆故障阻断业务回复。
        """
        try:
            return self._assembler.assemble(user_id, query)
        except Exception as exc:
            self.record_error(f"assemble {user_id}: {exc}")
            return ""

    @staticmethod
    def _point_id(user_id: str, session_type: str, canonical_id: str) -> str:
        return f"{user_id}:{session_type}:{canonical_id}"

    # ---- 观测 ----

    def record_error(self, message: str) -> None:
        self._errors.append(message)

    @property
    def errors(self) -> list[str]:
        return list(self._errors)
