"""L2: 根图 — 五目标分发 / 决策落状态 / 子图输入键 / 消息合并 / 多轮。"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.graphs.root import MOCK_INTERVIEW_INPUT_KEYS, TARGET_GRAPHS, build_root_graph
from app.graphs.stubs import build_stub_graph
from app.schemas.route import Intent, InterviewSlots, RouteDecision
from tests.support.graphs import StubDispatcher, build_capture_stub

STUB_TEXTS = {name: f"stub:{name}" for name in TARGET_GRAPHS}

# langgraph 1.x：带 checkpointer 的图必须提供 thread_id
CONFIG = {"configurable": {"thread_id": "t1"}}

DISPATCH_CASES = [
    ("react_agent", Intent.KNOWLEDGE_QA),
    ("mock_interview", Intent.MOCK_INTERVIEW),
    ("clarify", Intent.CLARIFY),
    ("resume_analysis", Intent.RESUME_ANALYSIS),
    ("interview_review", Intent.INTERVIEW_REVIEW),
]


@pytest.fixture
def stubs():
    subgraphs, captured = {}, {}
    for name in TARGET_GRAPHS:
        if name == "mock_interview":
            keys = MOCK_INTERVIEW_INPUT_KEYS
        elif name == "react_agent":
            keys = ("messages", "remaining_steps")  # 模拟生产 create_react_agent 的输入 schema
        else:
            keys = ("messages",)
        subgraphs[name], captured[name] = build_capture_stub(STUB_TEXTS[name], keys)
    return subgraphs, captured


def _decision(target: str, slots: InterviewSlots | None = None) -> RouteDecision:
    return RouteDecision(
        intent=Intent.CLARIFY,
        confidence=0.9,
        slots=slots or InterviewSlots(),
        source="llm",
        target_graph=target,
    )


def _invoke(graph, payload, config=CONFIG):
    return graph.invoke(payload, config)


def _texts(messages) -> list[str]:
    return [m.content for m in messages]


@pytest.mark.parametrize("target,intent", DISPATCH_CASES)
def test_dispatch_to_each_target_and_merge_messages(stubs, target, intent):
    """AC1 五目标分发 + AC2 子图消息合并回根状态。"""
    subgraphs, captured = stubs
    decision = RouteDecision(
        intent=intent,
        confidence=0.9,
        slots=InterviewSlots(),
        source="llm",
        target_graph=target,
    )
    dispatcher = StubDispatcher([decision])
    graph = build_root_graph(dispatcher, subgraphs, checkpointer=InMemorySaver())
    result = _invoke(
        graph, {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="你好")]}
    )
    assert _texts(result["messages"]) == ["你好", STUB_TEXTS[target]]
    assert isinstance(result["messages"][1], AIMessage)
    assert [m.content for m in captured[target]["input"]["messages"]] == ["你好"]
    assert dispatcher.calls == ["你好"]  # route 节点取最后一条用户消息文本


def test_route_decision_in_state(stubs):
    """AC3 route_decision 落入状态（model_dump mode=json，checkpointer 序列化可往返）。"""
    subgraphs, _ = stubs
    decision = _decision("react_agent")
    graph = build_root_graph(StubDispatcher([decision]), subgraphs, checkpointer=InMemorySaver())
    result = _invoke(graph, {"messages": [HumanMessage(content="hi")]})
    assert result["route_decision"] == decision.model_dump(mode="json")


def test_subgraph_keys_missing_in_root_state_are_omitted(stubs):
    """子图声明的键若根状态没有（如 react 的 remaining_steps）→ 不透传 None，子图走默认值。"""
    subgraphs, captured = stubs
    graph = build_root_graph(
        StubDispatcher([_decision("react_agent")]), subgraphs, checkpointer=InMemorySaver()
    )
    graph.invoke({"messages": [HumanMessage(content="hi")]}, CONFIG)
    assert set(captured["react_agent"]["input"]) == {"messages"}


def test_generic_subgraph_receives_only_declared_keys(stubs):
    """AC4 桩子图收到的输入键集合 == 声明的键（messages）。"""
    subgraphs, captured = stubs
    graph = build_root_graph(
        StubDispatcher([_decision("clarify")]), subgraphs, checkpointer=InMemorySaver()
    )
    _invoke(graph, {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="hi")]})
    got = captured["clarify"]["input"]
    assert set(got) == {"messages"}
    assert [m.content for m in got["messages"]] == ["hi"]


def test_mock_interview_input_contract(stubs):
    """mock_interview 子图输入：仅声明键、仅最后一条用户消息、槽位透传、难度归一化。"""
    subgraphs, captured = stubs
    decision = _decision(
        "mock_interview",
        InterviewSlots(position="Java 后端", years=3, round="1", difficulty="入门"),
    )
    graph = build_root_graph(StubDispatcher([decision]), subgraphs, checkpointer=InMemorySaver())
    _invoke(
        graph,
        {
            "user_id": "u1",
            "session_id": "s1",
            "messages": [HumanMessage(content="第一问"), HumanMessage(content="第二问")],
        },
    )
    got = captured["mock_interview"]["input"]
    assert set(got) == set(MOCK_INTERVIEW_INPUT_KEYS)
    assert [m.content for m in got["messages"]] == ["第二问"]
    assert got["user_id"] == "u1"
    assert got["session_id"] == "s1"
    assert got["position"] == "Java 后端"
    assert got["years"] == 3
    assert got["interview_round"] == "1"
    assert got["initial_difficulty"] == "entry"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("中等", "medium"),
        ("困难", "hard"),
        ("高级", "hard"),
        ("简单", "entry"),
        (None, "medium"),
        ("entry", "entry"),  # 规范值透传
        ("medium", "medium"),
        ("hard", "hard"),
    ],
)
def test_difficulty_slot_normalization(stubs, raw, expected):
    subgraphs, captured = stubs
    decision = _decision("mock_interview", InterviewSlots(difficulty=raw))
    graph = build_root_graph(StubDispatcher([decision]), subgraphs, checkpointer=InMemorySaver())
    _invoke(graph, {"messages": [HumanMessage(content="hi")]})
    assert captured["mock_interview"]["input"]["initial_difficulty"] == expected


def test_unknown_target_falls_back_to_clarify(stubs):
    subgraphs, _ = stubs
    graph = build_root_graph(
        StubDispatcher([_decision("bogus_target")]), subgraphs, checkpointer=InMemorySaver()
    )
    result = _invoke(graph, {"messages": [HumanMessage(content="hi")]})
    assert result["messages"][-1].content == STUB_TEXTS["clarify"]


def test_multiturn_accumulates_messages_with_checkpointer(stubs):
    subgraphs, _ = stubs
    dispatcher = StubDispatcher([_decision("react_agent"), _decision("clarify")])
    graph = build_root_graph(dispatcher, subgraphs, checkpointer=InMemorySaver())
    _invoke(
        graph, {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="第一轮")]}
    )
    result = _invoke(
        graph, {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="第二轮")]}
    )
    assert _texts(result["messages"]) == [
        "第一轮",
        STUB_TEXTS["react_agent"],
        "第二轮",
        STUB_TEXTS["clarify"],
    ]


def test_stub_graph_returns_fixed_text():
    """AC6 stub 子图：固定文案成为 AI 消息。"""
    graph = build_stub_graph("简历分析功能即将上线")
    result = graph.invoke({"messages": [HumanMessage(content="帮我看看简历")]})
    assert result["messages"][-1].content == "简历分析功能即将上线"
    assert isinstance(result["messages"][-1], AIMessage)


# ---- R5/R6（composition 规格 AC1/AC2/AC6/AC8，stub 层） ----


def test_subgraph_config_thread_id_derived(stubs):
    """AC1 子图 config 派生 thread_id + 稳定 checkpoint_ns（R6 状态隔离）。"""
    subgraphs, captured = stubs
    graph = build_root_graph(
        StubDispatcher([_decision("react_agent")]), subgraphs, checkpointer=InMemorySaver()
    )
    graph.invoke({"messages": [HumanMessage(content="hi")]}, CONFIG)
    configurable = captured["react_agent"]["config"]["configurable"]
    assert configurable["thread_id"] == "t1:react_agent"
    # 桩节点收到的任务 config：固定父 ns + 节点任务后缀（langgraph 任务级命名空间）
    assert configurable["checkpoint_ns"].startswith("react_agent")


def test_invoke_options_merged_into_subgraph_config(stubs):
    """AC6 invoke_options 合并进子图 config（react 的 recursion_limit）。"""
    subgraphs, captured = stubs
    graph = build_root_graph(
        StubDispatcher([_decision("react_agent")]),
        subgraphs,
        checkpointer=InMemorySaver(),
        invoke_options={"react_agent": {"configurable": {"recursion_limit": 8}}},
    )
    graph.invoke({"messages": [HumanMessage(content="hi")]}, CONFIG)
    configurable = captured["react_agent"]["config"]["configurable"]
    assert configurable["thread_id"] == "t1:react_agent"
    assert configurable["recursion_limit"] == 8


def test_active_graph_lock_bypasses_route_until_released(stubs):
    """AC2(stub 层) active 期间绕过 route 直通；predicate False 释放后恢复路由。"""
    subgraphs, _ = stubs
    dispatcher = StubDispatcher([_decision("mock_interview"), _decision("clarify")])
    calls = {"n": 0}

    def predicate(result):
        calls["n"] += 1
        return calls["n"] == 1  # 第一轮锁定，第二轮释放

    graph = build_root_graph(
        dispatcher,
        subgraphs,
        checkpointer=InMemorySaver(),
        active_predicates={"mock_interview": predicate},
    )
    _invoke(graph, {"messages": [HumanMessage(content="开始")]})
    result = _invoke(graph, {"messages": [HumanMessage(content="第一答")]})
    assert dispatcher.calls == ["开始"]  # 第二轮未重新路由
    assert _texts(result["messages"]) == [
        "开始",
        STUB_TEXTS["mock_interview"],
        "第一答",
        STUB_TEXTS["mock_interview"],
    ]
    assert result["active_graph"] is None  # predicate False → 释放
    result2 = _invoke(graph, {"messages": [HumanMessage(content="新消息")]})
    assert dispatcher.calls == ["开始", "新消息"]  # 恢复路由
    assert result2["messages"][-1].content == STUB_TEXTS["clarify"]


def test_unknown_predicate_key_raises(stubs):
    """AC8 active_predicates 键不在 TARGET_GRAPHS → ValueError。"""
    subgraphs, _ = stubs
    with pytest.raises(ValueError, match="active_predicates"):
        build_root_graph(
            StubDispatcher([]),
            subgraphs,
            checkpointer=InMemorySaver(),
            active_predicates={"bogus": lambda result: True},
        )


def test_unknown_invoke_options_key_raises(stubs):
    """AC8 invoke_options 键不在 TARGET_GRAPHS → ValueError。"""
    subgraphs, _ = stubs
    with pytest.raises(ValueError, match="invoke_options"):
        build_root_graph(
            StubDispatcher([]),
            subgraphs,
            checkpointer=InMemorySaver(),
            invoke_options={"bogus": {}},
        )


def test_resume_analysis_input_contract():
    """resume-analysis 规格 AC6：最后一条用户消息 + user_id/session_id + position 槽位。"""
    subgraphs, captured = {}, {}
    for name in TARGET_GRAPHS:
        if name == "resume_analysis":
            keys = ("messages", "user_id", "session_id", "position")
        elif name == "mock_interview":
            keys = MOCK_INTERVIEW_INPUT_KEYS
        elif name == "react_agent":
            keys = ("messages", "remaining_steps")
        else:
            keys = ("messages",)
        subgraphs[name], captured[name] = build_capture_stub(STUB_TEXTS[name], keys)
    decision = _decision("resume_analysis", InterviewSlots(position="Java 后端"))
    graph = build_root_graph(StubDispatcher([decision]), subgraphs, checkpointer=InMemorySaver())
    _invoke(
        graph,
        {
            "user_id": "u1",
            "session_id": "s1",
            "messages": [
                HumanMessage(content="帮我看看简历"),
                HumanMessage(content="张三，Java 3 年，做过订单系统"),
            ],
        },
    )
    got = captured["resume_analysis"]["input"]
    assert set(got) == {"messages", "user_id", "session_id", "position"}
    assert [m.content for m in got["messages"]] == ["张三，Java 3 年，做过订单系统"]
    assert got["user_id"] == "u1"
    assert got["session_id"] == "s1"
    assert got["position"] == "Java 后端"


def test_route_node_passes_history_summary(stubs):
    """R4（冒烟发现的缺口）：route 节点把最近对话摘要传给分类器，追问路由可见上下文。"""
    subgraphs, _ = stubs
    dispatcher = StubDispatcher([_decision("react_agent"), _decision("react_agent")])
    graph = build_root_graph(dispatcher, subgraphs, checkpointer=InMemorySaver())
    _invoke(
        graph,
        {"user_id": "u1", "session_id": "s1", "messages": [HumanMessage(content="帮我看看简历")]},
    )
    _invoke(graph, {"messages": [HumanMessage(content="报告里说的量化项目经历怎么写？")]})
    assert dispatcher.histories[0] == ""  # 首轮无上下文
    assert "帮我看看简历" in dispatcher.histories[1]
    assert STUB_TEXTS["react_agent"] in dispatcher.histories[1]  # 上一轮助手消息进摘要
    assert "报告里说的量化项目经历怎么写？" not in dispatcher.histories[1]  # 待分类消息不进摘要
