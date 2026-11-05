"""根图：路由决策 → 五个目标子图分发。

子图以 dict 注入；节点包装时只透传子图输入 schema 声明的键，
子图产出的 messages 经 add_messages 合并回根状态；config 透传以共享 checkpointer。
R5：active_graph 锁定进行中的有状态子图会话（绕过 route 直通）；
R6：子图 config 派生 thread_id 隔离状态。
依据 specs/03-implemented/graphs/root.md、specs/03-implemented/graphs/composition.md。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.router.dispatcher import RouteDispatcher

TARGET_GRAPHS = (
    "react_agent",
    "mock_interview",
    "clarify",
    "resume_analysis",
    "interview_review",
)

DIFFICULTY_SLOT_MAP = {
    "入门": "entry",
    "初级": "entry",
    "简单": "entry",
    "中等": "medium",
    "困难": "hard",
    "高级": "hard",
    # 规范值透传（子图再归一化时保持原档）
    "entry": "entry",
    "medium": "medium",
    "hard": "hard",
}
DEFAULT_DIFFICULTY = "medium"

MOCK_INTERVIEW_INPUT_KEYS = (
    "messages",
    "user_id",
    "session_id",
    "position",
    "years",
    "interview_round",
    "initial_difficulty",
)


class RootState(TypedDict, total=False):
    user_id: str
    session_id: str
    messages: Annotated[list[Any], add_messages]
    route_decision: dict[str, Any] | None
    active_graph: str | None  # R5：有状态子图会话未结束时的锁定目标


def last_user_text(messages: list[Any]) -> str:
    """取最后一条 HumanMessage 的文本；没有则返回空串。"""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


# 意图分类上下文摘要：条数与单条长度上限（防 token 膨胀）
SUMMARY_MAX_MESSAGES = 4
SUMMARY_MAX_CHARS = 400


def _history_summary(messages: list[Any]) -> str:
    """意图分类用最近对话摘要（R4：追问「报告里说的 X」需见到刚产出的分析报告，才能分到
    knowledge_qa 而非重复触发 resume_analysis / interview_review）。最后一条用户消息是待分类
    对象本身，不进摘要。"""
    prior = list(messages)
    if prior and isinstance(prior[-1], HumanMessage):
        prior.pop()
    lines: list[str] = []
    for message in prior[-SUMMARY_MAX_MESSAGES:]:
        role = "用户" if isinstance(message, HumanMessage) else "助手"
        content = message.content
        text = content if isinstance(content, str) else str(content)
        lines.append(f"{role}：{text[:SUMMARY_MAX_CHARS]}")
    return "\n".join(lines)


def normalize_difficulty(value: str | None) -> str:
    """槽位难度归一化：入门/简单/初级→entry，中等→medium，困难/高级→hard，缺省 medium。"""
    if value is None:
        return DEFAULT_DIFFICULTY
    return DIFFICULTY_SLOT_MAP.get(value, DEFAULT_DIFFICULTY)


def _mock_interview_input(state: RootState, declared_keys: set[str]) -> dict[str, Any]:
    """mock_interview 特化输入：仅最后一条用户消息 + 槽位（难度归一化）。"""
    decision = state.get("route_decision") or {}
    slots = decision.get("slots") or {}
    messages = state.get("messages") or []
    values: dict[str, Any] = {
        "messages": [messages[-1]] if messages else [],
        "user_id": state.get("user_id", ""),
        "session_id": state.get("session_id", ""),
        "position": slots.get("position"),
        "years": slots.get("years"),
        "interview_round": slots.get("round"),
        "initial_difficulty": normalize_difficulty(slots.get("difficulty")),
    }
    return {key: value for key, value in values.items() if key in declared_keys}


def _resume_analysis_input(state: RootState, declared_keys: set[str]) -> dict[str, Any]:
    """resume_analysis 特化输入：仅最后一条用户消息（简历全文）+ 槽位 position。"""
    decision = state.get("route_decision") or {}
    slots = decision.get("slots") or {}
    messages = state.get("messages") or []
    values: dict[str, Any] = {
        "messages": [messages[-1]] if messages else [],
        "user_id": state.get("user_id", ""),
        "session_id": state.get("session_id", ""),
        "position": slots.get("position"),
    }
    return {key: value for key, value in values.items() if key in declared_keys}


def _subgraph_input(name: str, subgraph: CompiledStateGraph, state: RootState) -> dict[str, Any]:
    """只透传子图声明且根状态真实存在的键；缺失键走子图默认值（如 react 的 remaining_steps）。"""
    declared_keys = set(subgraph.get_input_jsonschema().get("properties", {}))
    if name == "mock_interview":
        return _mock_interview_input(state, declared_keys)
    if name == "resume_analysis":
        return _resume_analysis_input(state, declared_keys)
    return {key: state.get(key) for key in declared_keys if key in state}


def _derive_subgraph_config(
    config: RunnableConfig, name: str, options: dict[str, Any] | None
) -> RunnableConfig:
    """R6：派生子图 thread_id 隔离状态（同 thread 会通道互串）；合并 options（如 recursion_limit）。

    同时把 checkpoint_ns 固定为子图名：根图 Pregel 给节点任务传的 ns 含随机
    task_id（如 mock_interview:<uuid>），若透传会让子图每轮换命名空间、多轮状态丢失。
    """
    configurable = dict(config.get("configurable") or {})
    thread_id = configurable.get("thread_id")
    if thread_id is not None:
        configurable["thread_id"] = f"{thread_id}:{name}"
    configurable["checkpoint_ns"] = name
    if options:
        configurable.update(options.get("configurable") or {})
    return {**config, "configurable": configurable}


def _make_subgraph_node(
    name: str,
    subgraph: CompiledStateGraph,
    *,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
    options: dict[str, Any] | None = None,
):
    def run(state: RootState, config: RunnableConfig) -> dict[str, Any]:
        # 子图 custom 事件经外层 writer 转发（嵌套 invoke 不传播流事件——2026-10-22 实证），
        # 使 chat 会话经根图路由时也能 token 级流式；invoke 分支写入被丢弃，行为不变。
        writer = get_stream_writer()
        sub_input = _subgraph_input(name, subgraph, state)
        result: dict[str, Any] = {}
        for mode, data in subgraph.stream(
            sub_input,
            _derive_subgraph_config(config, name, options),
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                writer(data)
            elif isinstance(data, dict):
                result = data
        updates: dict[str, Any] = {"messages": result.get("messages", [])}
        if predicate is not None:
            # R5：会话仍活跃 → 锁定直通；结束 → 释放回正常路由
            updates["active_graph"] = name if predicate(result) else None
        return updates

    return run


def _pick_entry(state: RootState) -> str:
    """R5：活跃子图会话未结束 → 绕过 route 直通该子图；否则正常路由。"""
    active = state.get("active_graph")
    return active if active in TARGET_GRAPHS else "route"


def _pick_target(state: RootState) -> str:
    decision = state.get("route_decision")
    target = decision.get("target_graph") if decision else None
    if target not in TARGET_GRAPHS:
        return "clarify"  # 未知目标降级澄清
    return target


def build_root_graph(
    dispatcher: RouteDispatcher,
    subgraphs: dict[str, CompiledStateGraph],
    *,
    checkpointer: Any = None,
    active_predicates: dict[str, Callable[[dict[str, Any]], bool]] | None = None,
    invoke_options: dict[str, dict[str, Any]] | None = None,
) -> CompiledStateGraph:
    """根图：START 先查 active_graph 锁定（R5），否则 route → 分发到五个目标子图。"""
    missing = [name for name in TARGET_GRAPHS if name not in subgraphs]
    if missing:
        raise ValueError(f"缺少子图: {', '.join(missing)}")
    predicates = active_predicates or {}
    unknown_predicates = [name for name in predicates if name not in TARGET_GRAPHS]
    if unknown_predicates:
        raise ValueError(f"active_predicates 含未知子图: {', '.join(unknown_predicates)}")
    options = invoke_options or {}
    unknown_options = [name for name in options if name not in TARGET_GRAPHS]
    if unknown_options:
        raise ValueError(f"invoke_options 含未知子图: {', '.join(unknown_options)}")

    builder = StateGraph(RootState)

    def route(state: RootState, config=None) -> dict[str, Any]:
        messages = state.get("messages") or []
        decision = dispatcher.route(
            last_user_text(messages), _history_summary(messages), config=config
        )
        # mode="json"：枚举转字符串，checkpointer 序列化可往返
        return {"route_decision": decision.model_dump(mode="json")}

    builder.add_node("route", route)
    for name in TARGET_GRAPHS:
        builder.add_node(
            name,
            _make_subgraph_node(
                name,
                subgraphs[name],
                predicate=predicates.get(name),
                options=options.get(name),
            ),
        )
        builder.add_edge(name, END)
    builder.add_conditional_edges(
        START, _pick_entry, {"route": "route", **{name: name for name in TARGET_GRAPHS}}
    )
    builder.add_conditional_edges("route", _pick_target, {name: name for name in TARGET_GRAPHS})
    return builder.compile(checkpointer=checkpointer)
