"""按业务类型的会话执行规则（页面分离核心）。

chat 走根图路由；interview / resume / review **直连对应子图、不经过路由**
（dispatcher/分类器完全不参与）。直连 config 与根图 R6 派生规则一致：
thread_id = 会话线程 + 子图名后缀；顶层直连 invoke 的检查点落 ns=""（2026-10-21 实证），
读取侧用双 ns 兜底（app/graphs/checkpoints.py）。

流式分支（stream_business）：graph.stream(["custom", "values"])——custom 事件即
终稿节点的 token 块，直接出帧；无 custom 事件（静态终稿）时 chunk_text 兜底。
同步 stream + 同步生成器：Sync SqliteSaver 不支持 astream（2026-10-22 实证），
FastAPI 同步生成器本就跑线程池。依据 specs/02-in-progress/api/real-token-streaming.md。
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.api.schemas import HistoryMessage
from app.api.sse import chunk_text, sse_data
from app.core.tracing import build_trace_handler
from app.graphs.checkpoints import MOCK_INTERVIEW_NAMESPACES, read_checkpoint

BUSINESS_SUBGRAPH = {
    "interview": "mock_interview",
    "resume": "resume_analysis",
    "review": "interview_review",
}

INTERVIEW_SUFFIX = ":mock_interview"
REVIEW_SUFFIX = ":interview_review"
RESUME_SUFFIX = ":resume_analysis"

# 根图五目标子图：chat 会话可能路由出 {sid}:{name} 派生线程（R6），删除时需一并清理
SUBGRAPH_NAMES = ("react_agent", "mock_interview", "clarify", "resume_analysis", "interview_review")


def _attach_tracing(config: RunnableConfig, session: Any) -> RunnableConfig:
    """有 Langfuse 密钥时挂 CallbackHandler + 会话元数据；否则原样返回。

    metadata 用 langfuse_* 键（Langfuse v3 约定：session/user/tags 过滤维度）；
    同一 config 经根图 _derive_subgraph_config 透传进子图，节点再传给 LLM 调用。
    """
    handler = build_trace_handler()
    if handler is None:
        return config
    config["callbacks"] = [handler]
    config["metadata"] = {
        "langfuse_session_id": session.id,
        "langfuse_user_id": session.user_id,
        "langfuse_tags": [session.business],
    }
    return config


def _reply(result: dict[str, Any]) -> str:
    """取结果中最后一条 AI 消息文本（与 invoke_chat 同规则）。"""
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def _business_target(
    root: CompiledStateGraph,
    subgraphs: dict[str, CompiledStateGraph],
    session: Any,
    text: str,
    *,
    jd_text: str | None = None,
) -> tuple[CompiledStateGraph, dict[str, Any], RunnableConfig]:
    """按会话 business 解析（图, 输入, config），invoke 与 stream 分支共用。"""
    payload = {
        "user_id": session.user_id,
        "session_id": session.id,
        "messages": [HumanMessage(content=text)],
    }
    if session.business == "chat":
        return root, payload, _attach_tracing(
            {"configurable": {"thread_id": session.id}}, session
        )
    name = BUSINESS_SUBGRAPH[session.business]
    # review 的会话对象是 target（复盘对象），其自身状态存 f"{target}:interview_review"
    session_id = session.target_session_id or session.id
    config: RunnableConfig = {
        "configurable": {
            "thread_id": f"{session_id}:{name}",
            "checkpoint_ns": name,  # 顶层 invoke 忽略，实际落 ns=""；保留以与派生规则对齐
        }
    }
    extra: dict[str, Any] = {"session_id": session_id}
    if session.business == "resume" and jd_text is not None:
        extra["jd_text"] = jd_text  # 仅 resume 管道有该 state 字段
    if session.business == "interview":
        # 会话属性注入（每轮同值无害：opening 只跑一轮，state 经 checkpointer 持久化）
        if session.difficulty is not None:
            extra["initial_difficulty"] = session.difficulty
        if session.position is not None:
            extra["position"] = session.position
    return subgraphs[name], {**payload, **extra}, _attach_tracing(config, session)


def invoke_business(
    root: CompiledStateGraph,
    subgraphs: dict[str, CompiledStateGraph],
    session: Any,
    text: str,
    *,
    jd_text: str | None = None,
) -> str:
    """按会话 business 执行一轮消息，返回 AI 回复文本。"""
    graph, payload, config = _business_target(
        root, subgraphs, session, text, jd_text=jd_text
    )
    result = graph.invoke(payload, config)
    return _reply(result)


def stream_business(
    root: CompiledStateGraph,
    subgraphs: dict[str, CompiledStateGraph],
    session: Any,
    text: str,
    *,
    jd_text: str | None = None,
):
    """按会话 business 流式执行一轮：token 级 custom 事件直出帧；静态终稿 chunk_text 兜底。

    产出 SSE 帧序列：data: {"content": ...} × N → data: {"done": true, ...}；
    中途异常 → data: {"error": ...}（无 done 帧）。
    """
    graph, payload, config = _business_target(
        root, subgraphs, session, text, jd_text=jd_text
    )
    streamed: list[str] = []
    reply = ""
    try:
        for mode, data in graph.stream(payload, config, stream_mode=["custom", "values"]):
            if mode == "custom" and isinstance(data, str):
                streamed.append(data)
                yield sse_data({"content": data})
            elif mode == "values" and isinstance(data, dict):
                reply = _reply(data)
        if not streamed:
            # 终稿由静态节点产出（面试出题/耗尽/结束语/fallback）：按原 30 字分块推送
            for chunk in chunk_text(reply):
                yield sse_data({"content": chunk})
        yield sse_data({"done": True, "session_id": session.id})
    except Exception as exc:  # noqa: BLE001 响应头已发出，异常只能随帧上报
        yield sse_data({"error": str(exc)})


def _session_thread_ids(session: Any) -> list[str]:
    """会话占用的全部检查点线程 id（会话删除时清理用）。

    chat 走根图：自身线程 sid + 根图内路由出的 {sid}:{name} 子线程（R6 派生）；
    直连业务线程与 _business_target 一致；review 只清理复盘自身线程（target 是复盘对象，不动）。
    """
    if session.business == "chat":
        return [session.id, *(f"{session.id}:{name}" for name in SUBGRAPH_NAMES)]
    name = BUSINESS_SUBGRAPH[session.business]
    return [f"{session.target_session_id or session.id}:{name}"]


def delete_checkpoint_threads(checkpointer: Any, session: Any) -> None:
    """删除会话的全部检查点线程（SqliteSaver.delete_thread 幂等，线程不存在无操作）。"""
    for thread_id in _session_thread_ids(session):
        checkpointer.delete_thread(thread_id)


def read_snapshot(checkpointer: Any, session: Any) -> Any | None:
    """按会话 business 读检查点快照（线程/ns 规则与 read_history 同源，历史回顾页复用）。"""
    if session.business == "chat":
        return read_checkpoint(checkpointer, session.id, ("",))
    if session.business == "interview":
        # 双 ns 兜底：根图嵌套调用落 ns=mock_interview，直连落 ns=""
        return read_checkpoint(
            checkpointer, f"{session.id}{INTERVIEW_SUFFIX}", MOCK_INTERVIEW_NAMESPACES
        )
    if session.business == "resume":
        return read_checkpoint(checkpointer, f"{session.id}{RESUME_SUFFIX}", ("",))
    target = session.target_session_id or session.id  # review
    return read_checkpoint(checkpointer, f"{target}{REVIEW_SUFFIX}", ("",))


def snapshot_messages(snapshot: Any) -> list[HistoryMessage]:
    """快照 → 对外可见消息（user/assistant；内部 ToolMessage/工具调用过滤）。"""
    if snapshot is None:
        return []
    messages = snapshot.checkpoint.get("channel_values", {}).get("messages", [])
    history: list[HistoryMessage] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            role: Literal["user", "assistant"] = "user"
        elif isinstance(message, AIMessage):
            if message.tool_calls:
                continue  # ReAct 中间步（工具调用指令），不是给用户的回复
            role = "assistant"
        else:
            continue
        content = message.content
        history.append(
            HistoryMessage(
                role=role, content=content if isinstance(content, str) else str(content)
            )
        )
    return history


def read_history(checkpointer: Any, session: Any) -> list[HistoryMessage]:
    """从 checkpointer 读该会话对外可见消息。"""
    return snapshot_messages(read_snapshot(checkpointer, session))
