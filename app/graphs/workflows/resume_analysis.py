"""C 简历分析工作流：extract → analyze → report（一次性管道）。

每节点单次 LLM 调用（R1）；解析失败兜底前进（judge/score 同款策略）。
完成事件 resume_analysis.complete 经 HookBus 发出（R4），报告落会话历史、追问走 B1。
不占 active_graph（R5 一次性不锁定）。依据 specs/03-implemented/workflows/resume-analysis.md。
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.graphs.json_utils import parse_json_text
from app.graphs.root import last_user_text
from app.graphs.streaming import stream_text
from app.hooks.bus import EVENT_ERROR, EVENT_RESUME_ANALYSIS_COMPLETE
from app.prompts.registry import PromptRegistry

PROMPT_EXTRACT = "resume/extract"
PROMPT_ANALYZE = "resume/analyze"
PROMPT_REPORT = "resume/report"

FALLBACK_REPLY = "没能从消息中解析出简历内容。请直接粘贴简历全文，我再帮你分析。"
DEFAULT_POSITION = "通用"


class ResumeAnalysisState(TypedDict, total=False):
    # 输入（根图特化透传）
    messages: Annotated[list[Any], add_messages]
    user_id: str
    session_id: str
    position: str | None
    jd_text: str | None  # 可选：目标岗位 JD，analyze/report 对照分析
    # 管道内部
    extracted: dict | None
    analysis: dict | None


def build_resume_analysis_graph(
    llm: Any, registry: PromptRegistry, *, bus: Any = None, checkpointer: Any = None
) -> CompiledStateGraph:
    """C 简历分析一次性管道；checkpointer 可选——API 层直连调用时需要检查点承载会话消息。"""

    def extract(state: ResumeAnalysisState, config=None) -> dict[str, Any]:
        resume = last_user_text(state.get("messages") or [])
        system = registry.render(PROMPT_EXTRACT, resume=resume)
        raw = llm.invoke([system], config=config).content
        data = parse_json_text(raw)
        if data is None and bus is not None:
            bus.fire(EVENT_ERROR, payload={"node": "extract", "raw": str(raw)[:200]})
        return {"extracted": data}

    def analyze(state: ResumeAnalysisState, config=None) -> dict[str, Any]:
        extracted = state.get("extracted") or {}
        position = state.get("position") or DEFAULT_POSITION
        jd = state.get("jd_text") or ""
        system = registry.render(
            PROMPT_ANALYZE,
            extracted=json.dumps(extracted, ensure_ascii=False),
            position=str(position),
            jd=str(jd),
        )
        raw = llm.invoke([system], config=config).content
        data = parse_json_text(raw)
        if data is None:
            if bus is not None:
                bus.fire(EVENT_ERROR, payload={"node": "analyze", "raw": str(raw)[:200]})
            data = {}  # 解析失败兜底：报告按通用建议产出，流程继续
        return {"analysis": data}

    def report(state: ResumeAnalysisState, config=None) -> dict[str, Any]:
        analysis = state.get("analysis") or {}
        position = state.get("position") or DEFAULT_POSITION
        jd = state.get("jd_text") or ""
        system = registry.render(
            PROMPT_REPORT,
            analysis=json.dumps(analysis, ensure_ascii=False),
            position=str(position),
            jd=str(jd),
        )
        text = stream_text(llm, [system], config=config)  # 终稿 token 流式
        if bus is not None:
            bus.fire(
                EVENT_RESUME_ANALYSIS_COMPLETE,
                payload={
                    "user_id": state.get("user_id", ""),
                    "session_id": state.get("session_id", ""),
                    "position": position,
                    "extracted": state.get("extracted"),
                    "analysis": analysis,
                },
            )
        return {"messages": [AIMessage(content=text)]}

    def fallback(state: ResumeAnalysisState) -> dict[str, Any]:
        return {"messages": [AIMessage(content=FALLBACK_REPLY)]}

    def route_extracted(state: ResumeAnalysisState) -> str:
        return "fallback" if state.get("extracted") is None else "analyze"

    builder = StateGraph(ResumeAnalysisState)
    builder.add_node("extract", extract)
    builder.add_node("analyze", analyze)
    builder.add_node("report", report)
    builder.add_node("fallback", fallback)
    builder.add_edge(START, "extract")
    builder.add_conditional_edges(
        "extract", route_extracted, {"analyze": "analyze", "fallback": "fallback"}
    )
    builder.add_edge("analyze", "report")
    builder.add_edge("report", END)
    builder.add_edge("fallback", END)
    return builder.compile(checkpointer=checkpointer)
