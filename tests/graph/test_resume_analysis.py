"""L2: C 简历分析 — extract → analyze → report 一次性管道 + 事件 + 追问走 B1。

依据 specs/03-implemented/workflows/resume-analysis.md 的 AC1-AC5/AC7/AC8。
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.graphs.root import build_root_graph
from app.graphs.stubs import build_stub_graph
from app.graphs.workflows.resume_analysis import (
    FALLBACK_REPLY,
    PROMPT_ANALYZE,
    PROMPT_EXTRACT,
    PROMPT_REPORT,
    build_resume_analysis_graph,
)
from app.hooks.bus import EVENT_ERROR, EVENT_RESUME_ANALYSIS_COMPLETE
from app.schemas.route import Intent, InterviewSlots, RouteDecision
from tests.conftest import StubLLM
from tests.support.graphs import StubDispatcher, build_capture_stub

CONFIG = {"configurable": {"thread_id": "t1"}}

EXTRACT_JSON = json.dumps(
    {
        "skills": ["Java", "MySQL"],
        "years": "3 年",
        "education": "本科",
        "experience": ["某公司后端开发"],
        "projects": ["订单系统"],
    },
    ensure_ascii=False,
)
ANALYZE_JSON = json.dumps(
    {
        "overall": 4,
        "strengths": ["项目经验丰富"],
        "weaknesses": ["缺少高并发描述"],
        "position_match": "与目标岗位较匹配",
    },
    ensure_ascii=False,
)

REPORT_TEXT = "## 修改建议\n1. 补充高并发量化指标。"


def _invoke(
    graph,
    text: str,
    *,
    position: str | None = None,
    jd_text: str | None = None,
    user_id="u1",
    session_id="s1",
):
    payload: dict = {
        "user_id": user_id,
        "session_id": session_id,
        "messages": [HumanMessage(content=text)],
    }
    if position is not None:
        payload["position"] = position
    if jd_text is not None:
        payload["jd_text"] = jd_text
    return graph.invoke(payload)


def _decision(target: str, intent: Intent) -> RouteDecision:
    return RouteDecision(
        intent=intent,
        confidence=0.9,
        slots=InterviewSlots(),
        source="llm",
        target_graph=target,
    )


def test_extract_analyze_report_full_flow_and_event(registry, bus):
    """AC1/AC3/AC5：extracted 落 state、analyze 收到抽取结果、报告落 messages、
    complete 事件 payload 完整、每节点恰好一次 LLM 调用。"""
    llm = StubLLM([EXTRACT_JSON, ANALYZE_JSON, REPORT_TEXT])
    graph = build_resume_analysis_graph(llm, registry, bus=bus)
    result = _invoke(graph, "张三，3 年后端，Java 技术栈", position="Java 后端")
    assert result["extracted"]["skills"] == ["Java", "MySQL"]
    assert result["analysis"]["overall"] == 4
    assert result["messages"][-1].content == REPORT_TEXT
    assert len(llm.calls) == 3  # AC5：extract/analyze/report 各一次
    # analyze 渲染变量：含抽取结果 JSON 与目标岗位
    rendered = llm.calls[1][0]
    assert "Java" in rendered and "Java 后端" in rendered
    (event,) = bus.events[EVENT_RESUME_ANALYSIS_COMPLETE]
    payload = event.payload
    assert payload["user_id"] == "u1"
    assert payload["session_id"] == "s1"
    assert payload["position"] == "Java 后端"
    assert payload["extracted"]["years"] == "3 年"
    assert payload["analysis"]["overall"] == 4


def test_extract_failure_falls_back_without_complete_event(registry, bus):
    """AC2：extract 解析失败 → EVENT_ERROR + 兜底文案；不 fire complete。"""
    llm = StubLLM(["这不是 JSON"])
    graph = build_resume_analysis_graph(llm, registry, bus=bus)
    result = _invoke(graph, "一段话")
    assert result["messages"][-1].content == FALLBACK_REPLY
    assert EVENT_RESUME_ANALYSIS_COMPLETE not in bus.events
    assert EVENT_ERROR in bus.events
    assert len(llm.calls) == 1  # 失败即止，不继续调用


def test_analyze_failure_still_produces_report(registry, bus):
    """AC4：analyze 解析失败 → EVENT_ERROR + 空分析兜底，报告仍产出、complete 仍触发。"""
    llm = StubLLM([EXTRACT_JSON, "坏输出", "报告文本"])
    graph = build_resume_analysis_graph(llm, registry, bus=bus)
    result = _invoke(graph, "张三，Java 3 年")
    assert result["analysis"] == {}
    assert result["messages"][-1].content == "报告文本"
    assert EVENT_ERROR in bus.events
    assert EVENT_RESUME_ANALYSIS_COMPLETE in bus.events


def test_followup_routes_to_react_with_report_in_history(registry, bus):
    """AC7（R4）：报告落会话历史后，追问正常路由到 react_agent 且其输入含报告。"""
    resume_llm = StubLLM([EXTRACT_JSON, ANALYZE_JSON, "报告：加强高并发描述。"])
    react_stub, captured = build_capture_stub("结合报告回答", ("messages",))
    subgraphs = {
        "react_agent": react_stub,
        "resume_analysis": build_resume_analysis_graph(resume_llm, registry, bus=bus),
        "mock_interview": build_stub_graph("stub:mock_interview"),
        "clarify": build_stub_graph("stub:clarify"),
        "interview_review": build_stub_graph("stub:interview_review"),
    }
    dispatcher = StubDispatcher(
        [
            _decision("resume_analysis", Intent.RESUME_ANALYSIS),
            _decision("react_agent", Intent.KNOWLEDGE_QA),
        ]
    )
    graph = build_root_graph(dispatcher, subgraphs, checkpointer=InMemorySaver())
    r1 = graph.invoke(
        {
            "user_id": "u1",
            "session_id": "s1",
            "messages": [HumanMessage(content="这是我的简历：张三……")],
        },
        CONFIG,
    )
    assert r1["messages"][-1].content == "报告：加强高并发描述。"
    r2 = graph.invoke(
        {"messages": [HumanMessage(content="这个建议具体怎么改？")]}, CONFIG
    )
    assert r2["messages"][-1].content == "结合报告回答"
    assert [m.content for m in captured["input"]["messages"]] == [
        "这是我的简历：张三……",
        "报告：加强高并发描述。",
        "这个建议具体怎么改？",
    ]


def test_jd_flows_into_analyze_and_report_prompts(registry, bus):
    """state 带 jd_text 时 analyze/report 渲染提示词含 JD 文本；extract 不含。"""
    llm = StubLLM([EXTRACT_JSON, ANALYZE_JSON, REPORT_TEXT])
    graph = build_resume_analysis_graph(llm, registry, bus=bus)
    result = _invoke(graph, "张三，3 年后端", jd_text="JD：要求高并发经验")
    assert result["messages"][-1].content == REPORT_TEXT
    assert "高并发经验" not in llm.calls[0][0]  # extract 不接收 JD
    assert "高并发经验" in llm.calls[1][0]  # analyze 含 JD
    assert "高并发经验" in llm.calls[2][0]  # report 含 JD


def test_extract_prompt_snapshot(snapshot, registry):
    assert snapshot == registry.render(PROMPT_EXTRACT, resume="张三，Java 3 年")


def test_analyze_prompt_snapshot(snapshot, registry):
    assert snapshot == registry.render(
        PROMPT_ANALYZE, extracted=EXTRACT_JSON, position="Java 后端", jd=""
    )


def test_analyze_prompt_with_jd_snapshot(snapshot, registry):
    assert snapshot == registry.render(
        PROMPT_ANALYZE, extracted=EXTRACT_JSON, position="Java 后端", jd="要求高并发经验"
    )


def test_report_prompt_snapshot(snapshot, registry):
    assert snapshot == registry.render(
        PROMPT_REPORT, analysis=ANALYZE_JSON, position="通用", jd=""
    )


def test_report_prompt_with_jd_snapshot(snapshot, registry):
    assert snapshot == registry.render(
        PROMPT_REPORT, analysis=ANALYZE_JSON, position="通用", jd="要求高并发经验"
    )
