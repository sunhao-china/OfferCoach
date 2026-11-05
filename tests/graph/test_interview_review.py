"""L2: D 面试复盘 — load → per_question → summary 一次性管道 + 事件 + 追问走 B1。

依据 specs/03-implemented/workflows/interview-review.md 的 AC1-AC8。
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.graphs.root import build_root_graph
from app.graphs.stubs import build_stub_graph
from app.graphs.workflows.interview_review import (
    FALLBACK_REPLY,
    PROMPT_PER_QUESTION,
    PROMPT_SUMMARY,
    build_interview_review_graph,
)
from app.graphs.workflows.mock_interview import build_mock_interview_graph
from app.hooks.bus import EVENT_ERROR, EVENT_INTERVIEW_REVIEW_COMPLETE
from app.prompts.registry import PromptRegistry
from app.schemas.route import Intent, InterviewSlots, RouteDecision
from tests.conftest import TEMPLATES_DIR, StubLLM
from tests.support.graphs import (
    ScriptedChatModel,
    StubDispatcher,
    build_capture_stub,
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

REVIEWS_JSON = json.dumps(
    {
        "reviews": [
            {
                "question_id": "fake_1",
                "comment": "知识点完整但缺少结构。",
                "strengths": ["概念准确"],
                "weaknesses": ["表达松散"],
            },
            {
                "question_id": "fake_2",
                "comment": "参数记得全，追问深度不足。",
                "strengths": ["覆盖面广"],
                "weaknesses": ["缺少调优经验"],
            },
        ]
    },
    ensure_ascii=False,
)


def prepare_interview_checkpoint(checkpointer, tmp_path, *, thread="t1"):
    """经根图包装（生产路径）跑真实 mock_interview，在 R6 隔离地址写入 2 题记录。

    直接顶层 invoke 子图会把检查点写到 ns=""（langgraph 顶层调用行为），
    与根图包装的派生地址（f"{thread}:mock_interview" + ns "mock_interview"）不一致，
    复盘 load 按生产地址读取，夹具必须复刻生产路径。
    返回 store（复盘图需要 store 回查评分要点）。
    """
    model = ScriptedChatModel(
        messages=iter(
            [
                AIMessage(content="开场。"),
                AIMessage(content=judge_json("move_on")),
                AIMessage(content=score_json(1)),
                AIMessage(content=judge_json("move_on")),
                AIMessage(content=score_json(1)),
                AIMessage(content="报告。"),
            ]
        )
    )
    store = make_store(tmp_path, BANK_RECORDS)
    interview = build_mock_interview_graph(
        model,
        store,
        PromptRegistry(TEMPLATES_DIR),
        max_questions=2,
        score_every=1,
        checkpointer=checkpointer,
    )
    decision = RouteDecision(
        intent=Intent.MOCK_INTERVIEW,
        confidence=0.9,
        slots=InterviewSlots(difficulty="entry"),
        source="llm",
        target_graph="mock_interview",
    )
    subgraphs = {
        "react_agent": build_stub_graph("stub:react"),
        "mock_interview": interview,
        "clarify": build_stub_graph("stub:clarify"),
        "resume_analysis": build_stub_graph("stub:resume"),
        "interview_review": build_stub_graph("stub:review"),
    }
    root = build_root_graph(
        StubDispatcher([decision]),
        subgraphs,
        checkpointer=checkpointer,
        active_predicates={"mock_interview": lambda result: result.get("phase") != "done"},
    )
    config = {"configurable": {"thread_id": thread}}
    root.invoke(
        {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="开始")]}, config
    )
    root.invoke({"messages": [HumanMessage(content="答1")]}, config)
    root.invoke({"messages": [HumanMessage(content="答2")]}, config)
    return store


def _review(graph, session_id="s1", config=None):
    return graph.invoke(
        {"user_id": "u1", "session_id": session_id},
        config or {"configurable": {"thread_id": "t1:interview_review"}},
    )


def _decision(target: str, intent: Intent) -> RouteDecision:
    return RouteDecision(
        intent=intent,
        confidence=0.9,
        slots=InterviewSlots(),
        source="llm",
        target_graph=target,
    )


def test_load_merges_records_and_full_flow(registry, bus, tmp_path):
    """AC1/AC3/AC5：记录按 question_id 合并、报告落 messages、complete 事件、
    LLM 恰好 2 次调用（load 无 LLM）。"""
    checkpointer = InMemorySaver()
    store = prepare_interview_checkpoint(checkpointer, tmp_path)
    llm = StubLLM([REVIEWS_JSON, "复盘报告：改进计划……"])
    graph = build_interview_review_graph(llm, registry, store, bus=bus, checkpointer=checkpointer)
    result = _review(graph)
    records = result["records"]
    assert len(records) == 2
    assert records[0]["answer"] == "答1"
    assert records[0]["correctness"] == 4
    assert records[0]["reference_points"] == ["要点一", "要点二", "要点三"]  # 按题回查
    assert records[1]["followups"] == []
    assert records[1]["comment"] == ""
    assert result["messages"][-1].content == "复盘报告：改进计划……"
    assert len(llm.calls) == 2  # AC5：per_question/summary 各一次
    (event,) = bus.events[EVENT_INTERVIEW_REVIEW_COMPLETE]
    assert event.payload["user_id"] == "u1"
    assert event.payload["session_id"] == "s1"
    assert event.payload["completed"] == 2
    assert len(event.payload["reviews"]) == 2


def test_no_records_falls_back_without_event(registry, bus, tmp_path):
    """AC2：无面试记录 → 兜底文案、不 fire complete、零 LLM 调用。"""
    llm = StubLLM([])
    graph = build_interview_review_graph(
        llm, registry, make_store(tmp_path, []), bus=bus, checkpointer=InMemorySaver()
    )
    result = _review(graph, session_id="s-none")
    assert result["messages"][-1].content == FALLBACK_REPLY
    assert EVENT_INTERVIEW_REVIEW_COMPLETE not in bus.events
    assert llm.calls == []


def test_per_question_failure_still_produces_report(registry, bus, tmp_path):
    """AC4：per_question 解析失败 → EVENT_ERROR + 空点评兜底，报告仍产出。"""
    checkpointer = InMemorySaver()
    store = prepare_interview_checkpoint(checkpointer, tmp_path)
    llm = StubLLM(["坏输出", "复盘报告。"])
    graph = build_interview_review_graph(llm, registry, store, bus=bus, checkpointer=checkpointer)
    result = _review(graph)
    assert result["reviews"] == [
        {"question_id": r["question_id"], "comment": "", "strengths": [], "weaknesses": []}
        for r in result["records"]
    ]
    assert result["messages"][-1].content == "复盘报告。"
    assert EVENT_ERROR in bus.events
    assert EVENT_INTERVIEW_REVIEW_COMPLETE in bus.events


def test_root_thread_falls_back_to_session_id(registry, bus, tmp_path):
    """AC8：config thread_id 不带 REVIEW_SUFFIX 时按 session_id 推导 mock_interview 地址。"""
    checkpointer = InMemorySaver()
    store = prepare_interview_checkpoint(checkpointer, tmp_path, thread="s1")
    llm = StubLLM([REVIEWS_JSON, "复盘报告。"])
    graph = build_interview_review_graph(llm, registry, store, bus=bus, checkpointer=checkpointer)
    result = _review(graph, config={"configurable": {"thread_id": "plain"}})
    assert result["records"] is not None
    assert len(result["records"]) == 2


def test_followup_routes_to_react_with_report_in_history(registry, bus, tmp_path):
    """AC6（R4）：同一会话内 面试 → 复盘 → 追问；追问路由到 react_agent 且输入含复盘报告。

    夹具在同一根线程 t1 上跑完面试（会话历史含面试消息，即真实场景）；
    断言聚焦 AC6：react 输入含报告文本、末尾为追问消息。
    """
    checkpointer = InMemorySaver()
    store = prepare_interview_checkpoint(checkpointer, tmp_path)
    review_llm = StubLLM([REVIEWS_JSON, "复盘报告：改进计划……"])
    react_stub, captured = build_capture_stub("结合报告回答", ("messages",))
    subgraphs = {
        "react_agent": react_stub,
        "interview_review": build_interview_review_graph(
            review_llm, registry, store, bus=bus, checkpointer=checkpointer
        ),
        "mock_interview": build_stub_graph("stub:mock_interview"),
        "clarify": build_stub_graph("stub:clarify"),
        "resume_analysis": build_stub_graph("stub:resume_analysis"),
    }
    dispatcher = StubDispatcher(
        [
            _decision("interview_review", Intent.INTERVIEW_REVIEW),
            _decision("react_agent", Intent.KNOWLEDGE_QA),
        ]
    )
    graph = build_root_graph(dispatcher, subgraphs, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "t1"}}
    r1 = graph.invoke(
        {
            "user_id": "u1",
            "session_id": "s1",
            "messages": [HumanMessage(content="帮我复盘一下")],
        },
        config,
    )
    assert r1["messages"][-1].content == "复盘报告：改进计划……"
    r2 = graph.invoke({"messages": [HumanMessage(content="改进计划怎么执行？")]}, config)
    assert r2["messages"][-1].content == "结合报告回答"
    react_input = [m.content for m in captured["input"]["messages"]]
    assert "复盘报告：改进计划……" in react_input  # 分析结果在 B1 输入内（R4）
    assert react_input[-1] == "改进计划怎么执行？"


def test_per_question_prompt_snapshot_v1(snapshot, registry):
    """v1 资产锁定：复盘已切 v2，v1 渲染结果保持稳定。"""
    assert snapshot == registry.render(
        PROMPT_PER_QUESTION,
        version=1,
        qa_blocks="题目 1（q_concurrent_001，难度 entry）：谈谈线程池\n回答：核心参数……",
    )


def test_per_question_prompt_snapshot_v2(snapshot, registry):
    assert snapshot == registry.render(
        PROMPT_PER_QUESTION,
        qa_blocks=(
            "题目 1（q_concurrent_001，难度 entry）：谈谈线程池\n"
            "评分要点：\n  1. 核心参数\n回答：核心参数……"
        ),
    )


def test_summary_prompt_snapshot(snapshot, registry):
    assert snapshot == registry.render(
        PROMPT_SUMMARY,
        completed="2",
        review_blocks="1. 谈谈线程池\n   点评：覆盖较全",
    )
