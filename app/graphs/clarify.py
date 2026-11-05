"""澄清子图：意图不明时由 LLM 发起一次追问（最多一问，1-2 句）。"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.graphs.root import last_user_text
from app.graphs.streaming import stream_text
from app.prompts.registry import PromptRegistry

CLARIFY_PROMPT = "clarify/clarify"


class ClarifyState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]


def build_clarify_graph(llm: Any, registry: PromptRegistry) -> CompiledStateGraph:
    """单节点：系统提示词（clarify/clarify）+ 最后一条用户消息 → AI 追问。"""
    system = registry.render(CLARIFY_PROMPT)

    def clarify(state: ClarifyState, config=None) -> dict[str, Any]:
        answer = stream_text(
            llm, [system, last_user_text(state.get("messages") or [])], config=config
        )
        return {"messages": [AIMessage(content=answer)]}

    builder = StateGraph(ClarifyState)
    builder.add_node("clarify", clarify)
    builder.add_edge(START, "clarify")
    builder.add_edge("clarify", END)
    return builder.compile()
