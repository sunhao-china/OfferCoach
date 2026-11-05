"""D 面试复盘工作流：load → per_question → summary（一次性管道）。

load 无 LLM：从共享 checkpointer 读同会话 mock_interview 的隔离状态（R6 地址契约）。
每 LLM 节点单次调用（R1）；解析失败兜底前进。
完成事件 interview_review.complete 经 HookBus 发出（R4），报告落会话历史、追问走 B1。
不占 active_graph（R5 一次性不锁定）。依据 specs/03-implemented/workflows/interview-review.md。
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.graphs.checkpoints import MOCK_INTERVIEW_NAMESPACES, read_checkpoint
from app.graphs.json_utils import parse_json_text
from app.graphs.streaming import stream_text
from app.hooks.bus import EVENT_ERROR, EVENT_INTERVIEW_REVIEW_COMPLETE
from app.prompts.registry import PromptRegistry
from app.question_bank.store import QuestionStore

PROMPT_PER_QUESTION = "interview_review/per_question"
PROMPT_SUMMARY = "interview_review/summary"

FALLBACK_REPLY = "当前会话还没有模拟面试记录。先说「开始模拟面试」面几轮，再来复盘。"

# R6 隔离地址契约：与根图派生规则（f"{root}:{name}"）保持一致
REVIEW_SUFFIX = ":interview_review"
MOCK_INTERVIEW_SUFFIX = ":mock_interview"
MOCK_INTERVIEW_NS = "mock_interview"


class InterviewReviewState(TypedDict, total=False):
    # 输入（根图通用路径透传；messages 仅用于报告回传）
    messages: Annotated[list[Any], add_messages]
    user_id: str
    session_id: str
    # 管道内部
    records: list[dict] | None
    reviews: list[dict]


def _root_thread(config: Any, session_id: str) -> str:
    """从自身派生 config 反推根图 thread：去掉 REVIEW_SUFFIX 后缀；缺省用 session_id。"""
    configurable = (config or {}).get("configurable") or {}
    thread_id = configurable.get("thread_id")
    if thread_id and str(thread_id).endswith(REVIEW_SUFFIX):
        return str(thread_id)[: -len(REVIEW_SUFFIX)]
    return session_id


def build_interview_review_graph(
    llm: Any,
    registry: PromptRegistry,
    store: QuestionStore,
    *,
    bus: Any = None,
    checkpointer: Any,
) -> CompiledStateGraph:
    """D 面试复盘一次性管道；checkpointer 必填：load 借它读 mock_interview 隔离状态。

    store 必填：load 按 question_id 回查评分要点，点评才有据可依。
    """

    # 注意：config 参数不要加类型注解——本模块 `from __future__ import annotations`
    # 会把注解变成字符串，langgraph 1.x 识别不出 RunnableConfig 参数、不注入 config。
    def load(state: InterviewReviewState, config=None) -> dict[str, Any]:
        root = _root_thread(config, state.get("session_id", ""))
        # 双 ns 兜底：根图嵌套调用落 ns="mock_interview"，API 层直连 invoke 落 ns=""
        tup = read_checkpoint(
            checkpointer, f"{root}{MOCK_INTERVIEW_SUFFIX}", MOCK_INTERVIEW_NAMESPACES
        )
        values = tup.checkpoint["channel_values"] if tup is not None else {}
        answers = values.get("answers") or []
        if not answers:
            return {"records": None}
        by_qid = {item.get("question_id"): item for item in (values.get("scores") or [])}
        records = []
        for answer in answers:
            score = by_qid.get(answer.get("question_id"), {})
            row = store.get(answer.get("question_id")) if answer.get("question_id") else None
            records.append(
                {
                    "question_id": answer.get("question_id"),
                    "question": answer.get("question", ""),
                    "difficulty": answer.get("difficulty", ""),
                    "answer": answer.get("answer", ""),
                    "followups": answer.get("followups", []),
                    "reference_points": list(row.reference_points) if row else [],
                    "correctness": score.get("correctness"),
                    "depth": score.get("depth"),
                    "expression": score.get("expression"),
                    "comment": score.get("comment", ""),
                    "strengths": score.get("strengths") or [],
                    "weaknesses": score.get("weaknesses") or [],
                }
            )
        return {"records": records}

    def per_question(state: InterviewReviewState, config=None) -> dict[str, Any]:
        records = state.get("records") or []
        lines = []
        for index, record in enumerate(records, start=1):
            points_lines = "\n".join(
                f"  {num}. {point}"
                for num, point in enumerate(record.get("reference_points") or [], start=1)
            )
            followup_lines = "\n".join(
                f"追问：{item.get('question')}\n追问回答：{item.get('answer')}"
                for item in (record.get("followups") or [])
            )
            score_line = (
                f"得分：正确性 {record['correctness']} / 深度 {record['depth']}"
                f" / 表达 {record['expression']}（{record.get('comment') or '无即时点评'}）"
                if record.get("correctness") is not None
                else "得分：（未评分）"
            )
            lines.append(
                f"题目 {index}（{record.get('question_id')}，难度 {record.get('difficulty')}）："
                f"{record.get('question')}\n评分要点：\n{points_lines or '  （无）'}\n"
                f"回答：{record.get('answer')}\n{followup_lines}\n{score_line}"
            )
        system = registry.render(PROMPT_PER_QUESTION, qa_blocks="\n\n".join(lines))
        raw = llm.invoke([system], config=config).content
        data = parse_json_text(raw)
        reviews = data.get("reviews") if data and isinstance(data.get("reviews"), list) else None
        if reviews is None:
            if bus is not None:
                bus.fire(EVENT_ERROR, payload={"node": "per_question", "raw": str(raw)[:200]})
            reviews = [
                {
                    "question_id": r.get("question_id"),
                    "comment": "",
                    "strengths": [],
                    "weaknesses": [],
                }
                for r in records
            ]  # 解析失败兜底：空点评，流程继续
        return {"reviews": reviews}

    def summary(state: InterviewReviewState, config=None) -> dict[str, Any]:
        records = state.get("records") or []
        reviews = state.get("reviews") or []
        blocks = "\n\n".join(
            f"{index}. {record.get('question')}\n   点评：{review.get('comment') or '（无）'}\n"
            f"   亮点：{'、'.join(review.get('strengths') or []) or '（无）'}\n"
            f"   不足：{'、'.join(review.get('weaknesses') or []) or '（无）'}"
            for index, (record, review) in enumerate(zip(records, reviews, strict=False), start=1)
        )
        system = registry.render(
            PROMPT_SUMMARY, completed=str(len(records)), review_blocks=blocks or "（无）"
        )
        text = stream_text(llm, [system], config=config)  # 终稿 token 流式
        if bus is not None:
            bus.fire(
                EVENT_INTERVIEW_REVIEW_COMPLETE,
                payload={
                    "user_id": state.get("user_id", ""),
                    "session_id": state.get("session_id", ""),
                    "completed": len(records),
                    "records": records,
                    "reviews": reviews,
                },
            )
        return {"messages": [AIMessage(content=text)]}

    def fallback(state: InterviewReviewState) -> dict[str, Any]:
        return {"messages": [AIMessage(content=FALLBACK_REPLY)]}

    def route_records(state: InterviewReviewState) -> str:
        return "fallback" if state.get("records") is None else "per_question"

    builder = StateGraph(InterviewReviewState)
    builder.add_node("load", load)
    builder.add_node("per_question", per_question)
    builder.add_node("summary", summary)
    builder.add_node("fallback", fallback)
    builder.add_edge(START, "load")
    builder.add_conditional_edges(
        "load", route_records, {"per_question": "per_question", "fallback": "fallback"}
    )
    builder.add_edge("per_question", "summary")
    builder.add_edge("summary", END)
    builder.add_edge("fallback", END)
    return builder.compile(checkpointer=checkpointer)
