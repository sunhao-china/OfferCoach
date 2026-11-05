"""ReAct 子图（B1 知识问答）：手写 tool-calling 循环 + 题库检索工具。

弃用 create_react_agent（其内部模型调用走 .invoke，不流式）。stream-first 单次调用：
- 产出 tool_calls → 进 ToolNode 循环（工具调用指令不外流）
- 产出文本 → 逐 token 写 custom 事件（终稿流式），与落库内容同一份，无二次调用
步数上限由组合层注入的 recursion_limit 控制（R2）。依据
specs/02-in-progress/api/real-token-streaming.md、specs/03-implemented/graphs/react-agent.md。
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from app.question_bank.store import QuestionStore
from app.tools.question_bank import build_question_bank_tool


class ReactAgentState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    user_id: str  # 根图透传（三层记忆装配用）
    session_id: str
    memory_context: str  # 本轮已装配的记忆文本（工具循环内复用，不重复召回）


def _assemble(chunks: list[Any]) -> AIMessage:
    """chunk 合并（tool_call_chunks 逐索引累积）并转普通 AIMessage 落状态。"""
    response: Any = chunks[0]
    for chunk in chunks[1:]:
        response = response + chunk
    if isinstance(response, AIMessageChunk):
        return AIMessage(
            content=response.content,
            tool_calls=response.tool_calls,
            additional_kwargs=response.additional_kwargs,
        )
    return response


def _last_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def build_react_agent(
    model: BaseChatModel, store: QuestionStore, system_prompt: str, *, memory: Any = None
) -> CompiledStateGraph:
    """B1 执行体；MVP 只挂题库检索工具，其余工具后置（依赖服务器资源）。

    memory 非空时每轮首调用装配 ContextPackage 注入 system（工具循环内复用）。
    """
    search_tool = build_question_bank_tool(store)
    model_with_tools = model.bind_tools([search_tool])

    def agent(state: ReactAgentState, config=None) -> dict[str, Any]:
        writer = get_stream_writer()
        updates: dict[str, Any] = {}
        messages = state.get("messages", [])
        context = state.get("memory_context")
        if memory is not None and (
            context is None or not messages or isinstance(messages[-1], HumanMessage)
        ):
            user_id = state.get("user_id", "")
            query = _last_user_text(messages)
            context = memory.assemble(user_id, query) if user_id else ""
            updates["memory_context"] = context or ""
        system = (
            f"{system_prompt}\n\n{context}" if context else system_prompt
        )
        messages = [SystemMessage(content=system), *messages]
        chunks: list[Any] = []
        for chunk in model_with_tools.stream(messages, config=config):
            chunks.append(chunk)
            content = chunk.content
            if isinstance(content, str) and content:
                # 终稿 token 流式。工具调用轮 content 为空不写；极端情况下若模型在
                # 工具调用前输出前言文本，该文本会已外流——deepseek 函数调用 content
                # 恒为空，接受此边界。
                writer(content)
        if not chunks:
            # 兜底：某些桩模型的流式对空内容消息无块产出，退化为 invoke
            updates["messages"] = [model_with_tools.invoke(messages, config=config)]
            return updates
        updates["messages"] = [_assemble(chunks)]
        return updates

    def should_continue(state: ReactAgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    builder = StateGraph(ReactAgentState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode([search_tool]))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile()
