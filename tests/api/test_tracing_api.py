"""L4: Langfuse 追踪接入 — handler 挂载与元数据、chat/直连双路径透传证明。

用 RecordingHandler（BaseCallbackHandler，零网络）替换 build_trace_handler，
证明每条业务请求的 LLM 调用都带着追踪回调——真 langfuse 上云由真实冒烟验证。
依据 specs/02-in-progress/observability/langfuse-tracing.md 的 AC2。
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.callbacks import BaseCallbackHandler

import app.api.executor as executor
from tests.api.test_web_api import VALID, _model, auth_headers, make_client
from tests.conftest import StubLLM


class RecordingHandler(BaseCallbackHandler):
    """记录回调事件的假 handler（不入网）。"""

    def __init__(self) -> None:
        self.llm_starts = 0
        self.chain_starts = 0

    def on_llm_start(self, *args, **kwargs) -> None:
        self.llm_starts += 1

    def on_chain_start(self, *args, **kwargs) -> None:
        self.chain_starts += 1


def test_attach_tracing_builds_callbacks_and_metadata(monkeypatch):
    handler = RecordingHandler()
    monkeypatch.setattr(executor, "build_trace_handler", lambda: handler)
    config = executor._attach_tracing(
        {"configurable": {"thread_id": "s1"}},
        SimpleNamespace(id="s1", user_id="alice", business="interview"),
    )
    assert config["callbacks"] == [handler]
    assert config["metadata"]["langfuse_session_id"] == "s1"
    assert config["metadata"]["langfuse_user_id"] == "alice"
    assert config["metadata"]["langfuse_tags"] == ["interview"]
    assert config["configurable"]["thread_id"] == "s1"  # 原有键不动


def test_attach_tracing_noop_without_handler(monkeypatch):
    monkeypatch.setattr(executor, "build_trace_handler", lambda: None)
    config: dict = {"configurable": {"thread_id": "s1"}}
    out = executor._attach_tracing(
        config, SimpleNamespace(id="s1", user_id="alice", business="chat")
    )
    assert out is config  # 原样返回，无 callbacks/metadata


def test_chat_and_interview_llm_calls_are_traced(tmp_path, registry, monkeypatch):
    recording = RecordingHandler()
    monkeypatch.setattr(executor, "build_trace_handler", lambda: recording)
    model = _model(
        "多态指同一方法调用在不同子类中表现出不同行为，Java 通过重写与重载实现。",
        "开场。",
    )
    client = make_client(tmp_path, registry, llm_classify=StubLLM([VALID]), llm_strong=model)
    headers = auth_headers(client, "alice")

    chat_id = client.post(
        "/api/sessions", json={"business": "chat"}, headers=headers
    ).json()["session_id"]
    response = client.post(
        f"/api/sessions/{chat_id}/messages",
        json={"content": "什么是 Java 的多态？"},
        headers=headers,
    )
    assert response.status_code == 200
    assert recording.chain_starts > 0  # 根图 invoke 本身是 chain
    assert recording.llm_starts > 0  # 意图分类 + ReAct 模型调用均带回调

    interview_id = client.post(
        "/api/sessions",
        json={"business": "interview", "difficulty": "entry", "position": "Java后端"},
        headers=headers,
    ).json()["session_id"]
    before = recording.llm_starts
    response = client.post(
        f"/api/sessions/{interview_id}/messages",
        json={
            "content": (
                "自我介绍：两年 Java 后端经验，熟悉 Spring Boot 与 MySQL，"
                "参与订单系统开发，做过接口性能优化与代码评审，日常编写单元测试。"
            )
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert recording.llm_starts > before  # 直连子图 opening 节点同样透传
