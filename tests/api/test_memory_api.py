"""L3: 三层记忆端到端（AC5）— 事件总结 / last_message_at / T1 兜底 / 装配注入。

依据 specs/02-in-progress/architecture/three-tier-memory.md 的 AC5。
make_client 带 memory=service（注入路径），事件总结走 daemon 线程（轮询等待）。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage

from app.core.embedding import DeterministicEmbedder
from app.hooks.bus import HookBus
from app.memory.service import MemoryService
from app.memory.store import COLLECTION_LONG_TERM, COLLECTION_PROFILE, FakeMemoryStore
from tests.api.test_web_api import (
    FINAL_ANSWER,
    INTERVIEW_SCRIPT,
    VALID,
    _create,
    _send,
    make_client,
)
from tests.conftest import StubLLM
from tests.support.graphs import ScriptedChatModel

SUMMARIZE_JSON = json.dumps(
    {
        "topic": "线程池与 CAS 问答",
        "importance": 0.7,
        "summary": {
            "key_content": [{"point": "线程池核心参数"}],
            "weak_points": ["CAS 理解浅"],
            "strengths": ["表达清晰"],
            "next_practice_suggestions": ["练 JUC"],
        },
        "profile_candidates": [],
    },
    ensure_ascii=False,
)

PROFILE_STATEMENT = "偏好简洁直接的反馈"


class RecordingChatModel(ScriptedChatModel):
    """记录每次调用的消息列表（含 system），供注入断言；响应序列同脚本化模型。

    GenericFakeChatModel 是 pydantic 模型，calls 必须声明为字段（实例属性赋值会被拒）。
    """

    calls: list[list[Any]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _service(registry, store, llm, bus) -> MemoryService:
    service = MemoryService(
        store=store,
        embedder=DeterministicEmbedder(),
        llm=llm,
        registry=registry,
        checkpointer=None,  # 事件/T1 测试在 client 构建后回填真实实例
        sessions=None,
        bus=bus,
    )
    assert service.ensure() is True
    return service


def _wire(client, service) -> None:
    """回填运行时的 checkpointer/sessions 并订阅事件（模拟 build_runtime 的接线）。"""
    service._checkpointer = client._runtime.checkpointer
    service._sessions = client._runtime.sessions
    service.register()


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _seed_profile(store, user_id: str = "user1") -> None:
    store.upsert(
        COLLECTION_PROFILE,
        f"{user_id}:p1",
        [0.1] * 32,
        {
            "memory_type": "profile",
            "user_id": user_id,
            "category": "preference",
            "statement": PROFILE_STATEMENT,
            "importance": 0.9,
            "status": "active",
            "created_at": 100.0,
            "last_recalled_at": 100.0,
            "recall_count": 0,
        },
    )


def test_interview_end_event_triggers_summary(tmp_path, registry):
    """AC5 事件驱动：interview 跑完 → 结束事件 → daemon 线程总结 → 长期点落库。"""
    store = FakeMemoryStore()
    bus = HookBus()
    service = _service(registry, store, StubLLM([SUMMARIZE_JSON]), bus)
    llm_strong = RecordingChatModel(messages=iter([AIMessage(content=t) for t in INTERVIEW_SCRIPT]))
    client = make_client(
        tmp_path,
        registry,
        llm_classify=StubLLM([VALID]),
        llm_strong=llm_strong,
        bus=bus,
        memory=service,
    )
    _wire(client, service)
    session_id = _create(client, "interview")
    _send(client, session_id, "开始吧")
    _send(client, session_id, "答1")
    _send(client, session_id, "答2")  # 2 题答满 → 报告节点 → 结束事件
    assert bus.events["mock_interview.end"]  # 事件已 fire
    point_id = f"user1:mock_interview:{session_id}"
    assert _wait_until(lambda: store.get(COLLECTION_LONG_TERM, point_id) is not None)
    payload = store.get(COLLECTION_LONG_TERM, point_id)["payload"]
    assert payload["topic"] == "线程池与 CAS 问答"
    assert payload["status"] == "active"
    assert payload["user_id"] == "user1"


def test_send_message_updates_last_message_at(tmp_path, registry):
    """AC5 chat 会话发消息 → last_message_at 更新。"""
    store = FakeMemoryStore()
    service = _service(registry, store, StubLLM([]), None)
    llm_strong = RecordingChatModel(messages=iter([AIMessage(content=FINAL_ANSWER)]))
    client = make_client(
        tmp_path, registry, llm_classify=StubLLM([VALID]), llm_strong=llm_strong, memory=service
    )
    session_id = _create(client, "chat")
    row_before = client._runtime.sessions.get(session_id)
    _send(client, session_id, "线程池参数有哪些")
    row = client._runtime.sessions.get(session_id)
    assert row.last_message_at is not None
    assert row.last_message_at >= row_before.last_message_at


def test_t1_sweep_summarizes_stale_chat_session(tmp_path, registry):
    """AC5 T1 兜底：静默超时的 chat 会话（无结束事件）补总结；幂等不重复。"""
    store = FakeMemoryStore()
    service = _service(registry, store, StubLLM([SUMMARIZE_JSON]), None)
    llm_strong = RecordingChatModel(messages=iter([AIMessage(content=FINAL_ANSWER)]))
    client = make_client(
        tmp_path, registry, llm_classify=StubLLM([VALID]), llm_strong=llm_strong, memory=service
    )
    _wire(client, service)
    session_id = _create(client, "chat")
    _send(client, session_id, "线程池参数有哪些")
    cutoff = datetime.now() + timedelta(days=1)  # 全部视为 stale
    assert service.sweep_t1(cutoff) == {"summarized": 1}
    point = store.get(COLLECTION_LONG_TERM, f"user1:chat:{session_id}")
    assert point is not None
    assert point["payload"]["session_type"] == "chat"
    assert service.sweep_t1(cutoff) == {"summarized": 0}  # 幂等：已有长期点跳过


def test_chat_reply_injects_profile_context(tmp_path, registry):
    """AC5 注入：B1 chat 新消息的 system 提示词含已存画像。"""
    store = FakeMemoryStore()
    _seed_profile(store)
    service = _service(registry, store, StubLLM([]), None)
    llm_strong = RecordingChatModel(messages=iter([AIMessage(content=FINAL_ANSWER)]))
    client = make_client(
        tmp_path, registry, llm_classify=StubLLM([VALID]), llm_strong=llm_strong, memory=service
    )
    session_id = _create(client, "chat")
    _send(client, session_id, "线程池参数有哪些")
    agent_calls = [call for call in llm_strong.calls if len(call) > 1]
    assert agent_calls, "react agent 应被调用"
    system = agent_calls[0][0].content
    assert "用户画像" in system
    assert PROFILE_STATEMENT in system


def test_interview_opening_injects_profile_context(tmp_path, registry):
    """AC5 注入：interview 开场提示词含已存画像。"""
    store = FakeMemoryStore()
    _seed_profile(store)
    service = _service(registry, store, StubLLM([]), None)
    llm_strong = RecordingChatModel(messages=iter([AIMessage(content=t) for t in INTERVIEW_SCRIPT]))
    client = make_client(
        tmp_path, registry, llm_classify=StubLLM([VALID]), llm_strong=llm_strong, memory=service
    )
    session_id = _create(client, "interview")
    _send(client, session_id, "开始吧")
    # 桩模型把裸字符串 system 提示词转成 HumanMessage 记录：断言 content 即可
    opening_system = llm_strong.calls[0][0].content
    assert "用户画像" in opening_system
    assert PROFILE_STATEMENT in opening_system
