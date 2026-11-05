"""L2: 澄清子图 — 桩 LLM 响应成为 AI 消息；提示词经注册表渲染。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from app.graphs.clarify import build_clarify_graph


def test_clarify_returns_llm_answer(registry, make_llm):
    """AC5 桩 LLM 响应成为 AI 消息。"""
    llm = make_llm(["你想进行模拟面试还是知识问答？"])
    graph = build_clarify_graph(llm, registry)
    result = graph.invoke({"messages": [HumanMessage(content="帮帮我")]})
    assert [m.content for m in result["messages"]] == ["帮帮我", "你想进行模拟面试还是知识问答？"]
    assert isinstance(result["messages"][0], HumanMessage)
    assert isinstance(result["messages"][1], AIMessage)


def test_clarify_uses_rendered_prompt_and_last_user_text(registry, make_llm):
    llm = make_llm(["请再说清楚一点"])
    graph = build_clarify_graph(llm, registry)
    graph.invoke({"messages": [HumanMessage(content="第一条"), HumanMessage(content="帮帮我")]})
    assert len(llm.calls) == 1
    system, user_text = llm.calls[0]
    assert "OfferCoach" in system  # 生产提示词资产已渲染
    assert user_text == "帮帮我"  # 只取最后一条用户消息
