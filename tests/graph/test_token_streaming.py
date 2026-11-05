"""L2: token 级流式 — 四图终稿节点写 custom 事件、内部节点零事件、根图转发。

依据 specs/02-in-progress/api/real-token-streaming.md 的 AC1-AC4。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.graphs.clarify import build_clarify_graph
from app.graphs.react_agent import build_react_agent
from app.graphs.root import build_root_graph
from app.graphs.stubs import build_stub_graph
from app.graphs.workflows.interview_review import build_interview_review_graph
from app.graphs.workflows.mock_interview import build_mock_interview_graph
from app.graphs.workflows.resume_analysis import build_resume_analysis_graph
from app.prompts.registry import PromptRegistry
from app.schemas.route import Intent, InterviewSlots, RouteDecision
from tests.conftest import TEMPLATES_DIR, StubLLM
from tests.graph.test_interview_review import prepare_interview_checkpoint
from tests.support.graphs import (
    ScriptedChatModel,
    StubDispatcher,
    TokenStreamChatModel,
    judge_json,
    score_json,
)
from tests.support.question_bank import make_record, make_store

BANK_RECORDS = [
    make_record("java_basics", 1, difficulty="entry", question="谈谈面向对象的三大特性"),
    make_record(
        "concurrent",
        1,
        difficulty="entry",
        qtype="knowledge",
        question="谈谈线程池的核心参数与拒绝策略",
    ),
    make_record("jvm", 1, difficulty="medium", question="JVM 运行时数据区有哪些"),
]

FINAL_ANSWER = "线程池的核心参数包括 corePoolSize、maximumPoolSize 等。"

REPORT_TEXT = "复盘报告：建议补充高并发调优案例与量化指标。"


def _tool_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_question_bank",
                "args": {"keywords": "线程池"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )


def _stream_events(graph, payload: dict[str, Any], config=None) -> tuple[list[str], dict[str, Any]]:
    """跑 graph.stream(["custom", "values"])，返回 (custom 事件, 最终 state)。"""
    events: list[str] = []
    state: dict[str, Any] = {}
    for mode, data in graph.stream(payload, config, stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(data)
        else:
            state = data
    return events, state


def test_interview_static_question_round_has_no_custom_events(tmp_path):
    """AC4 图级：开场 + 出题均为静态/非终稿流式节点 → 零 custom 事件（API 走 chunk_text 兜底）。"""
    model = ScriptedChatModel(messages=iter([AIMessage(content="开场。")]))
    store = make_store(tmp_path, BANK_RECORDS)
    graph = build_mock_interview_graph(model, store, PromptRegistry(TEMPLATES_DIR), max_questions=2)
    events, state = _stream_events(
        graph,
        {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="开始")]},
        {"configurable": {"thread_id": "mi-1"}},
    )
    assert events == []
    assert "第 1 题" in state["messages"][-1].content


def test_interview_report_streams_and_internal_nodes_silent(tmp_path, registry):
    """AC1/AC2 图级：opening/judge/score 零 custom 事件，仅终稿报告 token 级流式。

    脚本序列（judge end 轮 score 无未评分题、不调 LLM）：
    开场 / judge move_on / score(1) / judge end / 报告
    """
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
    store = make_store(tmp_path, BANK_RECORDS)
    graph = build_mock_interview_graph(
        model, store, registry, max_questions=2, score_every=1, checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "mi-2"}}
    payload = {
        "user_id": "u1",
        "session_id": "s1",
        "initial_difficulty": "entry",  # entry 题库 2 题，两轮后 judge end 收尾
        "messages": [HumanMessage(content="开始")],
    }
    events1, _ = _stream_events(graph, payload, config)  # 开场 + 第 1 题：零事件
    assert events1 == []
    _stream_events(graph, {**payload, "messages": [HumanMessage(content="答1")]}, config)
    events, state = _stream_events(
        graph, {**payload, "messages": [HumanMessage(content="答2")]}, config
    )  # judge end → 报告流式
    assert "".join(events) == REPORT_TEXT
    assert len(events) > 2  # token 级多帧
    assert state["messages"][-1].content == REPORT_TEXT


def test_resume_report_streams_and_extract_analyze_silent(tmp_path, registry):
    """AC2 图级：extract/analyze 零 custom 事件，报告 token 级流式。"""
    model = TokenStreamChatModel(
        messages=iter(
            [
                AIMessage(content='{"skills": ["Java"]}'),
                AIMessage(content='{"overall": 4}'),
                AIMessage(content="分析报告。"),
            ]
        )
    )
    graph = build_resume_analysis_graph(model, registry)
    events, state = _stream_events(
        graph, {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="简历")]}
    )
    assert "".join(events) == "分析报告。"
    assert state["messages"][-1].content == "分析报告。"


def test_review_summary_streams_and_per_question_silent(tmp_path, registry):
    """AC2 图级：load/per_question 零 custom 事件，复盘总结流式。"""
    checkpointer = InMemorySaver()
    store = prepare_interview_checkpoint(checkpointer, tmp_path)
    llm = TokenStreamChatModel(
        messages=iter(
            [
                AIMessage(content='{"reviews": [{"question_id": "fake_1", "comment": "c"}]}'),
                AIMessage(content=REPORT_TEXT),
            ]
        )
    )
    graph = build_interview_review_graph(llm, registry, store, checkpointer=checkpointer)
    events, state = _stream_events(
        graph,
        {"user_id": "u1", "session_id": "s1"},
        {"configurable": {"thread_id": "t1:interview_review"}},
    )
    assert "".join(events) == REPORT_TEXT
    assert state["messages"][-1].content == REPORT_TEXT


def test_clarify_streams(tmp_path, registry):
    llm = TokenStreamChatModel(messages=iter([AIMessage(content="你想练哪个方向？")]))
    graph = build_clarify_graph(llm, registry)
    events, _ = _stream_events(graph, {"messages": [HumanMessage(content="面试")]})
    assert "".join(events) == "你想练哪个方向？"


def test_react_tool_round_silent_and_final_answer_streams(tmp_path, registry):
    """AC1/AC2 图级：工具调用轮零 custom 事件，最终答案 token 流式且落库一致。"""
    store = make_store(tmp_path, BANK_RECORDS)
    model = ScriptedChatModel(messages=iter([_tool_call(), AIMessage(content=FINAL_ANSWER)]))
    graph = build_react_agent(model, store, registry.render("react/b1"))
    events, state = _stream_events(graph, {"messages": [HumanMessage(content="讲讲线程池")]})
    assert "".join(events) == FINAL_ANSWER
    assert state["messages"][-1].content == FINAL_ANSWER


def test_root_forwards_subgraph_custom_events(tmp_path, registry):
    """AC3 图级：chat 经根图路由（react）时 custom 事件经包装节点转发；分类器不外流。"""
    store = make_store(tmp_path, BANK_RECORDS)
    react = build_react_agent(
        TokenStreamChatModel(messages=iter([AIMessage(content=FINAL_ANSWER)])),
        store,
        registry.render("react/b1"),
    )
    decision = RouteDecision(
        intent=Intent.KNOWLEDGE_QA,
        confidence=0.9,
        slots=InterviewSlots(),
        source="llm",
        target_graph="react_agent",
    )
    root = build_root_graph(
        StubDispatcher([decision]),
        {
            "react_agent": react,
            "mock_interview": build_stub_graph("stub:interview"),
            "clarify": build_stub_graph("stub:clarify"),
            "resume_analysis": build_stub_graph("stub:resume"),
            "interview_review": build_stub_graph("stub:review"),
        },
    )
    events, state = _stream_events(
        root, {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="线程池")]}
    )
    assert "".join(events) == FINAL_ANSWER
    assert state["messages"][-1].content == FINAL_ANSWER


def test_root_forwards_clarify_events(tmp_path, registry):
    """AC3 图级：路由到 clarify 的 custom 事件同样经根图转发。"""
    clarify = build_clarify_graph(StubLLM(["你想练哪个方向？"]), PromptRegistry(TEMPLATES_DIR))
    decision = RouteDecision(
        intent=Intent.CLARIFY,
        confidence=0.5,
        slots=InterviewSlots(),
        source="llm",
        target_graph="clarify",
    )
    root = build_root_graph(
        StubDispatcher([decision]),
        {
            "react_agent": build_stub_graph("stub:react"),
            "mock_interview": build_stub_graph("stub:interview"),
            "clarify": clarify,
            "resume_analysis": build_stub_graph("stub:resume"),
            "interview_review": build_stub_graph("stub:review"),
        },
    )
    events, state = _stream_events(
        root, {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="面试")]}
    )
    assert "".join(events) == "你想练哪个方向？"
    assert state["messages"][-1].content == "你想练哪个方向？"
