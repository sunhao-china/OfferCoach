"""L1: 流式辅助与测试桩契约 — stream_text 写入 custom 事件、桩流式行为。

依据 specs/02-in-progress/api/real-token-streaming.md。
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.graphs.streaming import stream_text
from tests.support.graphs import (
    RaisingStreamChatModel,
    ScriptedChatModel,
    TokenStreamChatModel,
)


class S(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]


def _run_stream_node(llm, message_text: str) -> tuple[list[str], str]:
    """在单节点图内跑 stream_text，返回 (custom 事件列表, 节点产出文本)。"""
    captured: dict[str, Any] = {}

    def node(state: S):
        text = stream_text(llm, [HumanMessage(content=message_text)])
        captured["text"] = text
        return {"messages": [AIMessage(content=text)]}

    builder = StateGraph(S)
    builder.add_node("node", node)
    builder.add_edge(START, "node")
    builder.add_edge("node", END)
    graph = builder.compile()
    events: list[str] = []
    for mode, data in graph.stream(
        {"messages": [HumanMessage(content="hi")]}, stream_mode=["custom", "values"]
    ):
        if mode == "custom":
            events.append(data)
    return events, captured["text"]


def test_stream_text_writes_token_events_and_returns_full_text():
    llm = TokenStreamChatModel(messages=iter([AIMessage(content="你好世界")]))
    events, text = _run_stream_node(llm, "hi")
    assert events == ["你", "好", "世", "界"]
    assert text == "你好世界"


def test_stream_text_whole_message_model_single_event():
    llm = ScriptedChatModel(messages=iter([AIMessage(content="整块输出")]))
    events, text = _run_stream_node(llm, "hi")
    assert events == ["整块输出"]
    assert text == "整块输出"


def test_stream_text_outside_node_raises():
    with pytest.raises(RuntimeError):
        stream_text(ScriptedChatModel(messages=iter([])), [HumanMessage(content="x")])


def test_scripted_model_streams_tool_call_as_single_chunk():
    """工具调用消息整块产出（langchain 会追加空收尾块）且 args 可解析回 dict。"""
    llm = ScriptedChatModel(
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
                )
            ]
        )
    )
    chunks = list(llm.stream([HumanMessage(content="x")]))
    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged + chunk
    assert merged.content == ""
    assert merged.tool_calls == [
        {
            "name": "search_question_bank",
            "args": {"keywords": "线程池"},
            "id": "call_1",
            "type": "tool_call",
        }
    ]


def test_token_stream_model_chars_join_to_content():
    llm = TokenStreamChatModel(messages=iter([AIMessage(content="abc")]))
    chunks = list(llm.stream([HumanMessage(content="x")]))
    assert "".join(c.content for c in chunks) == "abc"
    assert len(chunks) == 3


def test_raising_stream_model_raises_on_stream():
    llm = RaisingStreamChatModel(messages=iter([AIMessage(content="x")]))
    with pytest.raises(RuntimeError, match="模型流式失败"):
        list(llm.stream([HumanMessage(content="x")]))
