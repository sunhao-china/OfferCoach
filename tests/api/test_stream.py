"""L3: 消息端点 SSE 流式分支 — token 级流式 + 静态兜底 + error 帧 + 默认 JSON 兼容。

依据 specs/02-in-progress/api/real-token-streaming.md 的 AC1-AC6。
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage

from app.api.sse import CHUNK_SIZE
from tests.api.test_web_api import (
    ANALYZE_JSON,
    EXTRACT_JSON,
    VALID,
    _create,
    _tool_call,
    auth_headers,
    make_client,
)
from tests.conftest import StubLLM
from tests.support.graphs import (
    RaisingStreamChatModel,
    ScriptedChatModel,
    TokenStreamChatModel,
    judge_json,
    score_json,
)

LONG_ANSWER = (
    "线程池的核心参数包括 corePoolSize、maximumPoolSize、keepAliveTime、"
    "workQueue 与拒绝策略，实际调优需要结合任务类型与机器核数。"
)
REPORT_TEXT = "本次面试复盘报告：整体表现良好，建议补充高并发调优案例。"


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


def _stream(client, session_id, content, **extra) -> list[dict]:
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": content, "stream": True, **extra},
        headers=auth_headers(client),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return _parse_sse(response.text)


def _content_frames(events: list[dict]) -> list[str]:
    return [event["content"] for event in events if "content" in event]


def test_stream_chat_token_frames_join_to_reply(tmp_path, registry):
    """AC1/AC2 chat：token 帧拼接 == JSON 分支回复；工具调用指令不外流；末帧 done。"""
    classify = StubLLM([VALID])
    model = TokenStreamChatModel(messages=iter([_tool_call(), AIMessage(content=LONG_ANSWER)]))
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    session_id = _create(client, "chat")
    events = _stream(client, session_id, "讲讲线程池")
    chunks = _content_frames(events)
    assert "".join(chunks) == LONG_ANSWER
    assert len(chunks) > 2  # token 级多帧（逐字桩）
    assert all(chunk for chunk in chunks)
    assert all("search_question_bank" not in chunk for chunk in chunks)  # 工具指令不外流
    assert events[-1] == {"done": True, "session_id": session_id}
    assert len(classify.calls) == 1  # 路由仍执行
    assert "讲讲线程池" in classify.calls[0][-1]


def test_stream_interview_question_round_falls_back_to_chunks(tmp_path, registry):
    """AC4 interview 静态终稿（出题）：无 token 流 → chunk_text 兜底分块。"""
    classify = StubLLM([])
    model = ScriptedChatModel(messages=iter([AIMessage(content="开场。")]))
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    session_id = _create(client, "interview")
    events = _stream(client, session_id, "开始")
    chunks = _content_frames(events)
    reply = "".join(chunks)
    assert "第 1 题" in reply
    assert all(0 < len(chunk) <= CHUNK_SIZE for chunk in chunks)  # 兜底仍按 30 字分块
    assert events[-1]["done"] is True
    assert classify.calls == []  # 直连不经路由


def test_stream_interview_report_token_frames(tmp_path, registry):
    """AC1/AC2 interview 终稿（报告）：token 级流式，评分等内部消息不外流。"""
    classify = StubLLM([])
    model = TokenStreamChatModel(
        messages=iter(
            [
                AIMessage(content="开场。"),
                AIMessage(content=judge_json("move_on")),
                AIMessage(content=score_json(1)),
                AIMessage(content=judge_json("end")),
                AIMessage(content=REPORT_TEXT),
            ]
        )
    )
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    session_id = _create(client, "interview", difficulty="entry")  # entry 题库 2 题
    _stream(client, session_id, "开始")
    _stream(client, session_id, "答1")
    events = _stream(client, session_id, "答2")  # judge end → 报告流式
    chunks = _content_frames(events)
    assert "".join(chunks) == REPORT_TEXT
    assert len(chunks) > 2
    assert all("正确性" not in chunk for chunk in chunks)  # score 内部消息不外流
    assert events[-1] == {"done": True, "session_id": session_id}


def test_stream_resume_with_jd(tmp_path, registry):
    """AC1 resume + jd_text 流式：extract/analyze 不外流，报告 token 帧；JD 互不影响。"""
    classify = StubLLM([])
    report = "报告：与 JD 匹配度较高，但高并发经验不足，建议补充具体量化指标与调优案例。"
    llm = TokenStreamChatModel(
        messages=iter(
            [
                AIMessage(content=EXTRACT_JSON),
                AIMessage(content=ANALYZE_JSON),
                AIMessage(content=report),
            ]
        )
    )
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=llm)
    session_id = _create(client, "resume")
    events = _stream(client, session_id, "李四，2 年 Java 后端", jd_text="JD：要求高并发经验")
    chunks = _content_frames(events)
    assert "".join(chunks) == report
    assert len(chunks) > 2  # token 级多帧
    assert all('"skills"' not in chunk for chunk in chunks)  # extract 内部 JSON 不外流
    assert events[-1]["done"] is True


def test_stream_error_frame_without_done(tmp_path, registry):
    """AC6 流式中途异常 → error 帧 + 连接结束（无 done 帧）。"""
    classify = StubLLM([])
    llm = RaisingStreamChatModel(
        messages=iter([AIMessage(content=EXTRACT_JSON), AIMessage(content=ANALYZE_JSON)])
    )
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=llm)
    session_id = _create(client, "resume")
    events = _stream(client, session_id, "李四，2 年 Java 后端")
    assert events and "error" in events[-1]
    assert "模型流式失败" in events[-1]["error"]
    assert all("done" not in event for event in events)


def test_stream_false_returns_json(tmp_path, registry):
    """AC5 显式 stream=false：仍是 JSON 契约。"""
    classify = StubLLM([VALID])
    model = ScriptedChatModel(messages=iter([_tool_call(), AIMessage(content="答案")]))
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    session_id = _create(client, "chat")
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "线程池", "stream": False},
        headers=auth_headers(client),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["reply"] == "答案"


def test_stream_unknown_session_404(tmp_path, registry):
    model = ScriptedChatModel(messages=iter([]))
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=model)
    response = client.post(
        "/api/sessions/nope/messages",
        json={"content": "你好", "stream": True},
        headers=auth_headers(client),
    )
    assert response.status_code == 404
