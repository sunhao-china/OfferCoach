"""组合层：根图与五子图的真实组件接线 + 本地聊天入口。

依赖注入全部组件：生产用 deepseek 分档 LLM（app.core.llm_factory），测试用桩。
R5/R6 机制（active_graph 锁定 / 派生 thread_id 隔离）由根图内建，本模块只做接线。
依据 specs/03-implemented/graphs/composition.md。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.graphs.clarify import build_clarify_graph
from app.graphs.react_agent import build_react_agent
from app.graphs.root import build_root_graph
from app.graphs.workflows.interview_review import build_interview_review_graph
from app.graphs.workflows.mock_interview import build_mock_interview_graph
from app.graphs.workflows.resume_analysis import build_resume_analysis_graph
from app.prompts.registry import PromptRegistry
from app.question_bank.store import QuestionStore
from app.router.dispatcher import RouteDispatcher

PROMPT_REACT = "react/b1"
RECURSION_LIMIT_REACT = 8  # R2：ReAct 步数上限（MVP）


def build_subgraphs(
    *,
    store: QuestionStore,
    registry: PromptRegistry,
    llm_classify: Any,
    llm_strong: Any,
    bus: Any = None,
    checkpointer: Any = None,
    memory: Any = None,
    max_questions: int = 6,
    max_followups: int = 2,
    score_every: int = 2,
) -> dict[str, CompiledStateGraph]:
    """接线五子图（不含根图与分发器）；API 层直连会话（interview/resume/review）按名取用。

    memory 缺省 None = 记忆装配关闭（测试零行为）；生产由 build_runtime 注入。
    """
    return {
        "react_agent": build_react_agent(
            llm_strong, store, registry.render(PROMPT_REACT), memory=memory
        ),
        "mock_interview": build_mock_interview_graph(
            llm_strong,
            store,
            registry,
            bus=bus,
            max_questions=max_questions,
            max_followups=max_followups,
            score_every=score_every,
            checkpointer=checkpointer,
            memory=memory,
        ),
        "clarify": build_clarify_graph(llm_classify, registry),
        "resume_analysis": build_resume_analysis_graph(
            llm_strong, registry, bus=bus, checkpointer=checkpointer
        ),
        "interview_review": build_interview_review_graph(
            llm_strong, registry, store, bus=bus, checkpointer=checkpointer
        ),
    }


def build_app_graph(
    *,
    store: QuestionStore,
    registry: PromptRegistry,
    llm_classify: Any,
    llm_strong: Any,
    bus: Any = None,
    checkpointer: Any = None,
    dispatcher: Any = None,
    memory: Any = None,
    max_questions: int = 6,
    max_followups: int = 2,
    score_every: int = 2,
    subgraphs: dict[str, CompiledStateGraph] | None = None,
) -> CompiledStateGraph:
    """接线五子图 + 分发器；返回带 checkpointer 的根图（R5/R6 已内建）。

    - llm_classify：意图分类 + 澄清（生产 cheap 档）
    - llm_strong：B1 ReAct + A1 模拟面试（生产 strong 档）
    - dispatcher 缺省用 llm_classify 构造 RouteDispatcher；测试注入桩
    - subgraphs 缺省内部构建；API 层直连会话需共享同一批子图实例时外部传入
    """
    dispatcher = dispatcher or RouteDispatcher(llm_classify, registry, bus=bus)
    if subgraphs is None:
        subgraphs = build_subgraphs(
            store=store,
            registry=registry,
            llm_classify=llm_classify,
            llm_strong=llm_strong,
            bus=bus,
            checkpointer=checkpointer,
            memory=memory,
            max_questions=max_questions,
            max_followups=max_followups,
            score_every=score_every,
        )
    return build_root_graph(
        dispatcher,
        subgraphs,
        checkpointer=checkpointer,
        active_predicates={"mock_interview": lambda result: result.get("phase") != "done"},
        invoke_options={
            "react_agent": {"configurable": {"recursion_limit": RECURSION_LIMIT_REACT}}
        },
    )


def invoke_chat(
    graph: CompiledStateGraph,
    user_id: str,
    session_id: str,
    text: str,
    *,
    config: RunnableConfig | None = None,
) -> str:
    """本地聊天入口：一轮对话，返回最后一条 AI 消息文本。"""
    invoke_config: RunnableConfig = config or {"configurable": {"thread_id": session_id}}
    payload: dict[str, Any] = {
        "user_id": user_id,
        "session_id": session_id,
        "messages": [HumanMessage(content=text)],
    }
    result = graph.invoke(payload, invoke_config)
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""
