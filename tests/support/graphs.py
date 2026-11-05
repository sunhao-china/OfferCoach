"""图相关测试桩：脚本化聊天模型、预设决策分发器与捕获桩子图（多测试文件共用）。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import ChatGenerationChunk
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.schemas.route import RouteDecision


def _tool_call_chunks(message: AIMessage) -> list[dict[str, Any]]:
    """AIMessage.tool_calls → AIMessageChunk.tool_call_chunks（chunk 的 args 是 JSON 字符串）。"""
    return [
        {"name": tc["name"], "args": json.dumps(tc["args"], ensure_ascii=False), "id": tc["id"]}
        for tc in (message.tool_calls or [])
    ]


class ScriptedChatModel(GenericFakeChatModel):
    """脚本化模型：bind_tools 返回自身；流式按整块产出（含 tool_calls，单块）。

    覆盖 GenericFakeChatModel 默认逐词 _stream：工具调用消息 content 为空时默认实现
    直接抛 ValueError（无块产出），整块流式保证 ReAct 循环 stream-first 可测。
    """

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        chat_result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        message = chat_result.generations[0].message
        kwargs_chunk = {"content": message.content}
        if message.tool_calls:
            kwargs_chunk["tool_call_chunks"] = _tool_call_chunks(message)
        yield ChatGenerationChunk(message=AIMessageChunk(**kwargs_chunk))


class TokenStreamChatModel(GenericFakeChatModel):
    """逐字流式桩：内容消息按单字符分块（模拟 token 级流），tool_calls 消息整块产出。"""

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        chat_result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        message = chat_result.generations[0].message
        if message.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content=message.content, tool_call_chunks=_tool_call_chunks(message)
                )
            )
            return
        for index, char in enumerate(str(message.content)):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content=char,
                    chunk_position="last" if index == len(str(message.content)) - 1 else None,
                )
            )


class RaisingStreamChatModel(GenericFakeChatModel):
    """流式即抛异常的桩：用于流式中途异常 → error 帧的测试。"""

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("模型流式失败")


class StubDispatcher:
    """按预设序列返回 RouteDecision 的桩分发器。"""

    def __init__(self, decisions: list[RouteDecision]) -> None:
        self._decisions = list(decisions)
        self.calls: list[str] = []
        self.histories: list[str] = []

    def route(self, user_message: str, history_summary: str = "", config=None) -> RouteDecision:
        self.calls.append(user_message)
        self.histories.append(history_summary)
        return self._decisions.pop(0)


def build_capture_stub(text: str, keys: tuple[str, ...]) -> tuple[CompiledStateGraph, dict]:
    """构造只声明 keys 输入键、记录实际收到的输入与 config、返回固定 AI 消息的桩子图。"""
    StubState = TypedDict("StubState", {key: Any for key in keys}, total=False)
    captured: dict[str, Any] = {}

    def node(state: StubState, config=None) -> dict[str, Any]:
        captured["input"] = dict(state)
        captured["config"] = config or {}
        return {"messages": [AIMessage(content=text)]}

    builder = StateGraph(StubState)
    builder.add_node("stub", node)
    builder.add_edge(START, "stub")
    builder.add_edge("stub", END)
    return builder.compile(), captured


def judge_json(action: str, **extra) -> str:
    """mock_interview judge 节点的脚本化 JSON 输出。"""
    return json.dumps({"action": action, **extra}, ensure_ascii=False)


def score_json(count: int) -> str:
    """mock_interview score 节点的脚本化 JSON 输出（各维度 4 分）。"""
    return json.dumps(
        {
            "results": [
                {"question_id": f"fake_{i}", "correctness": 4, "depth": 4, "expression": 4}
                for i in range(count)
            ]
        },
        ensure_ascii=False,
    )
