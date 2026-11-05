"""L2: ReAct 子图 — 桩模型脚本化 tool_call → 工具执行 → 最终答案成为最后一条 AI 消息。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graphs.react_agent import build_react_agent
from tests.support.graphs import ScriptedChatModel
from tests.support.question_bank import STORE_SAMPLE_RECORDS, make_store

FINAL_ANSWER = "线程池的核心参数包括 corePoolSize、maximumPoolSize 等。"


def test_react_agent_calls_tool_and_answers(tmp_path):
    """AC4 工具被调用、最终答案成为最后一条 AI 消息。"""
    store = make_store(tmp_path, STORE_SAMPLE_RECORDS)
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
                AIMessage(content=FINAL_ANSWER),
            ]
        )
    )
    graph = build_react_agent(model, store, "你是面试教练，优先检索题库。")
    result = graph.invoke({"messages": [HumanMessage(content="讲讲线程池")]})

    messages = result["messages"]
    assert messages[-1].content == FINAL_ANSWER
    assert isinstance(messages[-1], AIMessage)
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert "q_concurrent_001" in tool_messages[0].content  # 真实工具命中种子库


# ---- react/b1.v2 提示词（specs/02-in-progress/prompts/react-b1-v2.md） ----


def test_b1_prompt_latest_version_has_new_rules(registry):
    """AC1 最新版本含强制作答与上下文利用两节；v1 可显式版本回退。"""
    latest = registry.render("react/b1")
    assert "强制作答约束" in latest
    assert "会话上下文利用" in latest
    legacy = registry.render("react/b1", version=1)
    assert "强制作答约束" not in legacy


def test_b1_prompt_enforces_final_answer(registry):
    """AC2（execution-boundaries AC5 提示词部分）：强制作答约束文本。"""
    rendered = registry.render("react/b1")
    assert "以面向用户的答案文本收尾" in rendered
    assert "只调用工具而不作答是不允许的" in rendered


def test_b1_prompt_uses_session_analysis(registry):
    """AC3（execution-boundaries AC6 提示词部分）：会话结论利用约束文本。"""
    rendered = registry.render("react/b1")
    assert "必须结合该结论回答" in rendered
    assert "先引用结论中的相关要点" in rendered


def test_b1_prompt_snapshot(snapshot, registry):
    """AC4：v2 渲染快照锁定。"""
    assert snapshot == registry.render("react/b1")
