"""测试替身子图：单节点返回固定文案，供根图/工作流测试注入各业务桩。"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict


class StubState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]


def build_stub_graph(text: str) -> CompiledStateGraph:
    """单节点：追加一条固定文案的 AI 消息。"""

    def stub(state: StubState) -> dict[str, Any]:
        return {"messages": [AIMessage(content=text)]}

    builder = StateGraph(StubState)
    builder.add_node("stub", stub)
    builder.add_edge(START, "stub")
    builder.add_edge("stub", END)
    return builder.compile()
