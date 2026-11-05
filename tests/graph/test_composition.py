"""L2: 组合层 — 五子图真实接线 + R5/R6（active_graph 锁定 / 派生 thread_id 隔离）。

依据 specs/03-implemented/graphs/composition.md 的 AC2-AC5/AC7/AC9。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.composition import build_app_graph, invoke_chat
from app.prompts.registry import PromptRegistry
from app.schemas.route import Intent, InterviewSlots, RouteDecision
from tests.conftest import TEMPLATES_DIR, StubLLM
from tests.support.graphs import ScriptedChatModel, StubDispatcher, judge_json, score_json
from tests.support.question_bank import make_record, make_store

CONFIG = {"configurable": {"thread_id": "c1"}}

BANK_RECORDS = [
    make_record("java_basics", 1, difficulty="entry", question="谈谈面向对象的三大特性"),
    make_record("java_basics", 2, difficulty="entry", question="String 与 StringBuilder 的区别"),
    make_record(
        "concurrent",
        1,
        difficulty="entry",
        qtype="knowledge",
        question="谈谈线程池的核心参数与拒绝策略",
    ),
    make_record("jvm", 1, difficulty="medium", question="JVM 运行时数据区有哪些"),
]


def make_decision(target: str, intent: Intent = Intent.CLARIFY, slots=None) -> RouteDecision:
    return RouteDecision(
        intent=intent,
        confidence=0.9,
        slots=slots or InterviewSlots(),
        source="llm",
        target_graph=target,
    )


def build_app(
    tmp_path, *, llm_strong, llm_classify=None, dispatcher=None, checkpointer=None, **kwargs
):
    store = make_store(tmp_path, BANK_RECORDS)
    registry = PromptRegistry(TEMPLATES_DIR)
    return build_app_graph(
        store=store,
        registry=registry,
        llm_classify=llm_classify or StubLLM([]),
        llm_strong=llm_strong,
        checkpointer=checkpointer or InMemorySaver(),
        dispatcher=dispatcher,
        **kwargs,
    )


def sub_state_values(checkpointer, thread_id: str, name: str) -> dict:
    """读取子图自身状态（R6：派生 thread_id + 稳定 ns）。"""
    tup = checkpointer.get_tuple(
        {"configurable": {"thread_id": f"{thread_id}:{name}", "checkpoint_ns": name}}
    )
    assert tup is not None
    return tup.checkpoint["channel_values"]


def test_chitchat_routes_to_real_react_agent_with_tool(tmp_path):
    """AC7 闲聊 → 真实 ReAct 执行体：scripted tool_call → 真查 sqlite 题库 → 作答。"""
    model = ScriptedChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_question_bank",
                            "args": {"keywords": "线程池"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="线程池核心参数包括 corePoolSize、maximumPoolSize 等。"),
            ]
        )
    )
    dispatcher = StubDispatcher([make_decision("react_agent", Intent.CHITCHAT)])
    app = build_app(tmp_path, llm_strong=model, dispatcher=dispatcher)
    result = app.invoke(
        {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="线程池是啥")]},
        CONFIG,
    )
    assert result["messages"][-1].content == "线程池核心参数包括 corePoolSize、maximumPoolSize 等。"


def test_interview_lock_full_flow_and_release(tmp_path):
    """AC2/AC3 面试进行中锁定直通；结束口令短接 judge（零调用）→ score → report →
    done 释放；下一轮恢复路由。"""
    mock_llm = ScriptedChatModel(
        messages=iter(
            [
                AIMessage(content="欢迎参加本次模拟面试！"),
                AIMessage(content=judge_json("move_on", feedback="回答不错。")),
                AIMessage(content=score_json(1)),  # 结束口令确定性短接，judge 不再消耗响应
                AIMessage(content="总结报告……"),
            ]
        )
    )
    clarify_llm = StubLLM(["你是想开始模拟面试吗？"])
    dispatcher = StubDispatcher(
        [
            make_decision(
                "mock_interview", Intent.MOCK_INTERVIEW, InterviewSlots(difficulty="entry")
            ),
            make_decision("clarify", Intent.CLARIFY),
        ]
    )
    app = build_app(tmp_path, llm_strong=mock_llm, llm_classify=clarify_llm, dispatcher=dispatcher)
    r1 = app.invoke(
        {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="开始模拟面试")]},
        CONFIG,
    )
    assert r1["active_graph"] == "mock_interview"
    r2 = app.invoke({"messages": [HumanMessage(content="第一题回答")]}, CONFIG)
    assert dispatcher.calls == ["开始模拟面试"]  # AC2：面试中不重新路由
    assert r2["messages"][-1].content.startswith("第 2 题")  # judge move_on 推进
    r3 = app.invoke({"messages": [HumanMessage(content="结束吧")]}, CONFIG)
    assert r3["messages"][-1].content == "总结报告……"  # report 产出
    assert r3["active_graph"] is None  # AC3：done 释放
    r4 = app.invoke({"messages": [HumanMessage(content="你好")]}, CONFIG)
    assert dispatcher.calls == ["开始模拟面试", "你好"]  # 恢复路由
    assert r4["messages"][-1].content == "你是想开始模拟面试吗？"


def test_ac4_isolation_and_ac5_unique_messages(tmp_path):
    """AC4 面试前闲聊不进入 mock_interview 状态；AC5 根图消息无重复。"""
    model = ScriptedChatModel(
        messages=iter(
            [
                AIMessage(content="你好，我是面试教练。"),
                AIMessage(content="开场。"),
                AIMessage(content=judge_json("move_on")),
                AIMessage(content=judge_json("move_on")),
                AIMessage(content=score_json(2)),
                AIMessage(content=judge_json("move_on")),
                AIMessage(content=score_json(2)),
                AIMessage(content="报告。"),
            ]
        )
    )
    dispatcher = StubDispatcher(
        [
            make_decision("react_agent", Intent.CHITCHAT),
            make_decision(
                "mock_interview", Intent.MOCK_INTERVIEW, InterviewSlots(difficulty="entry")
            ),
        ]
    )
    checkpointer = InMemorySaver()
    app = build_app(
        tmp_path,
        llm_strong=model,
        dispatcher=dispatcher,
        max_questions=3,
        checkpointer=checkpointer,
    )
    r0 = app.invoke(
        {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="你好")]}, CONFIG
    )
    assert r0["messages"][-1].content == "你好，我是面试教练。"
    app.invoke({"messages": [HumanMessage(content="开始模拟面试")]}, CONFIG)
    app.invoke({"messages": [HumanMessage(content="答1")]}, CONFIG)
    app.invoke({"messages": [HumanMessage(content="答2")]}, CONFIG)
    r4 = app.invoke({"messages": [HumanMessage(content="答3")]}, CONFIG)

    # AC4：mock_interview 自身状态（派生 thread + 稳定 ns）不含面试前闲聊
    sub_values = sub_state_values(checkpointer, "c1", "mock_interview")
    sub_contents = [m.content for m in sub_values["messages"]]
    assert "你好" not in sub_contents
    assert "你好，我是面试教练。" not in sub_contents
    assert "开场。" in sub_contents
    # AC5：根图消息无重复
    root_contents = [m.content for m in r4["messages"]]
    assert len(root_contents) == len(set(root_contents))
    assert sub_values["completed"] == 3
    assert sub_values["phase"] == "done"
    assert r4["active_graph"] is None


@pytest.mark.parametrize("target", ["clarify", "bogus_target"])
def test_clarify_and_unknown_target_fallbacks(tmp_path, target):
    """AC7 澄清目标与未知目标（降级 clarify）在组合图中均可达。"""
    clarify_llm = StubLLM(["请说明你的需求。"])
    dispatcher = StubDispatcher([make_decision(target)])
    unused_model = ScriptedChatModel(messages=iter([]))  # react 构建必需但本用例不调用
    app = build_app(
        tmp_path, llm_strong=unused_model, llm_classify=clarify_llm, dispatcher=dispatcher
    )
    result = app.invoke({"messages": [HumanMessage(content="hi")]}, CONFIG)
    assert result["messages"][-1].content == "请说明你的需求。"


def test_invoke_chat_returns_last_ai_text_and_accumulates(tmp_path):
    """AC9 聊天入口返回最后 AI 文本；同一 session 跨轮累积。"""
    clarify_llm = StubLLM(["你想问什么？", "请再说一遍。"])
    dispatcher = StubDispatcher([make_decision("clarify"), make_decision("clarify")])
    unused_model = ScriptedChatModel(messages=iter([]))  # react 构建必需但本用例不调用
    app = build_app(
        tmp_path, llm_strong=unused_model, llm_classify=clarify_llm, dispatcher=dispatcher
    )
    assert invoke_chat(app, "u1", "s9", "在吗") == "你想问什么？"
    assert invoke_chat(app, "u1", "s9", "嗯") == "请再说一遍。"
    state = app.get_state({"configurable": {"thread_id": "s9"}})
    assert [m.content for m in state.values["messages"]] == [
        "在吗",
        "你想问什么？",
        "嗯",
        "请再说一遍。",
    ]
