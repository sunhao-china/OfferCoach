"""流式输出辅助：节点内逐 token 写 custom 事件并返回完整文本。

用法（仅「终稿生产者」节点）：

    text = stream_text(llm, messages)
    return {"messages": [AIMessage(content=text)]}

writer 事件仅在 graph.stream(stream_mode 含 "custom") 时对外可见；invoke（JSON 分支）
时事件被丢弃，节点行为与直接 invoke 完全一致。内部节点不调用本函数 → 内部内容
结构性不外流。依据 specs/02-in-progress/api/real-token-streaming.md。
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer


def stream_text(llm: Any, messages: list[Any], config=None) -> str:
    """流式调用 LLM：str 内容块逐块写 custom 事件，返回完整拼接文本。

    config 透传给模型（Langfuse 追踪等 callbacks 随节点 config 流入）。
    """
    writer = get_stream_writer()
    parts: list[str] = []
    for chunk in llm.stream(messages, config=config):
        content = chunk.content
        if isinstance(content, str) and content:
            writer(content)
            parts.append(content)
    return "".join(parts)
