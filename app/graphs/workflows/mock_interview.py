"""A1 模拟面试工作流：opening → ask → judge → (followup | score) → report → done。

多轮状态经 checkpointer 持久化（与根图共享同一实例，config 透传）。
全部 LLM 调用经 PromptRegistry 渲染；依赖注入 llm / store / registry / bus。
依据 specs/03-implemented/graphs/mock-interview.md。
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.graphs.json_utils import parse_json_text
from app.graphs.root import last_user_text, normalize_difficulty
from app.graphs.streaming import stream_text
from app.hooks.bus import EVENT_ERROR, EVENT_MOCK_INTERVIEW_END
from app.prompts.registry import PromptRegistry
from app.question_bank.store import DEFAULT_POSITION, QuestionStore, normalize_position

PHASE_OPENING = "opening"
PHASE_ANSWERING = "answering"
PHASE_DONE = "done"

DIFFICULTY_LABELS = {"entry": "入门", "medium": "中等", "hard": "困难"}
DIFFICULTY_ORDER = ("entry", "medium", "hard")
SCORE_FLOOR, SCORE_CEIL, SCORE_DEFAULT = 1, 5, 3

JUDGE_ACTIONS = ("followup", "move_on", "end")

PROMPT_OPENING = "mock_interview/opening"
PROMPT_JUDGE = "mock_interview/judge"
PROMPT_SCORE = "mock_interview/score"
PROMPT_REPORT = "mock_interview/report"

FINISH_REPLY = "本次模拟面试已结束。可以说「开始模拟面试」再来一轮，或试试知识问答。"

DEFAULT_POSITION_DISPLAY = "Java 后端"
DEFAULT_YEARS = 3
DEFAULT_ROUND = "1"


class MockInterviewState(TypedDict, total=False):
    # 会话（根图透传）
    messages: Annotated[list[Any], add_messages]
    user_id: str
    session_id: str
    position: str | None
    years: int | None
    interview_round: str | None
    initial_difficulty: str | None
    # 流程
    phase: str
    difficulty: str
    # 当前题
    current_question_id: str | None
    current_question: str
    current_reference_points: list[str]
    current_followups: list[str]
    followup_count: int
    pending_main_answer: str
    pending_followups: list[dict]
    last_followup_text: str | None  # judge 产出、append_followup 消费的内部通道
    # 进度
    asked_ids: list[str]
    answers: list[dict]
    scores: list[dict]
    scored_count: int
    completed: int
    exhausted: bool
    # 路由
    last_judge_action: str | None


def _clamp(value: Any) -> int:
    """分数钳制到 1-5；非数值回退默认 3 分。"""
    try:
        return max(SCORE_FLOOR, min(SCORE_CEIL, int(value)))
    except (TypeError, ValueError):
        return SCORE_DEFAULT


def _numbered(items: Any) -> str:
    """编号列表文本（提示词用）；空则「（无）」。"""
    lines = [f"{index}. {item}" for index, item in enumerate(list(items or []), start=1)]
    return "\n".join(lines) or "（无）"


# 确定性退出口令：精确匹配（去尾部标点）。LLM 判别兜底，避免把「结束」当作答。
EXIT_PHRASES = ("结束", "结束吧", "结束面试", "不面了", "停止面试")


def _is_exit(text: str) -> bool:
    normalized = text.strip().rstrip("。！!？?…~")
    return normalized in EXIT_PHRASES


def _adapt_difficulty(current: str, new_scores: list[dict]) -> str:
    """本轮均分 ≥4 升档、≤2.5 降档；档位不越界。"""
    if not new_scores:
        return current
    average = sum((s["correctness"] + s["depth"] + s["expression"]) / 3 for s in new_scores) / len(
        new_scores
    )
    index = DIFFICULTY_ORDER.index(current) if current in DIFFICULTY_ORDER else 1
    if average >= 4:
        index = min(index + 1, len(DIFFICULTY_ORDER) - 1)
    elif average <= 2.5:
        index = max(index - 1, 0)
    return DIFFICULTY_ORDER[index]


def build_mock_interview_graph(
    llm: Any,
    store: QuestionStore,
    registry: PromptRegistry,
    *,
    bus: Any = None,
    max_questions: int = 6,
    max_followups: int = 2,
    score_every: int = 2,
    checkpointer: Any = None,
    memory: Any = None,
) -> CompiledStateGraph:
    """A1 模拟面试状态机；checkpointer 为多轮状态持久化必需（与根图共享实例）。

    memory 非空时开场把 ContextPackage 注入面试官提示词（画像 + 长期记忆）。
    """
    if score_every < 1:
        raise ValueError(f"score_every 必须 ≥1，收到 {score_every}")

    def opening(state: MockInterviewState, config=None) -> dict[str, Any]:
        difficulty = normalize_difficulty(state.get("initial_difficulty"))
        position = state.get("position") or DEFAULT_POSITION_DISPLAY
        years = state.get("years") or DEFAULT_YEARS
        interview_round = state.get("interview_round") or DEFAULT_ROUND
        system = registry.render(
            PROMPT_OPENING,
            position=str(position),
            years=str(years),
            interview_round=str(interview_round),
            max_questions=str(max_questions),
            max_followups=str(max_followups),
            score_every=str(score_every),
        )
        if memory is not None:
            user_id = state.get("user_id", "")
            # 记忆检索用归一化岗位（"Java 后端"→java_backend），展示仍用原始输入
            norm_position = normalize_position(str(position))
            context = (
                memory.assemble(user_id, f"{norm_position} {years} 模拟面试") if user_id else ""
            )
            if context:
                system = f"{system}\n\n{context}"
        text = llm.invoke([system], config=config).content
        return {
            "messages": [AIMessage(content=text)],
            "phase": PHASE_ANSWERING,
            "difficulty": difficulty,
            "asked_ids": [],
            "answers": [],
            "scores": [],
            "completed": 0,
            "scored_count": 0,
            "exhausted": False,
            "followup_count": 0,
            "pending_followups": [],
        }

    def ask(state: MockInterviewState) -> dict[str, Any]:
        difficulty = state.get("difficulty", DIFFICULTY_ORDER[1])
        # 口语岗位输入（"Java 后端"/"C++"）归一化到库内 position；未命中别名时按原文查
        raw_position = state.get("position")
        position = normalize_position(raw_position) if raw_position else DEFAULT_POSITION
        question = store.random_pick(
            position=position,
            difficulty=difficulty,
            exclude_ids=state.get("asked_ids", []),
        )
        if question is None and position != DEFAULT_POSITION:
            # 岗位与题库口径不一致（未知岗位或该岗无此难度题）时回退默认岗位，避免误判题库耗尽
            question = store.random_pick(
                position=DEFAULT_POSITION,
                difficulty=difficulty,
                exclude_ids=state.get("asked_ids", []),
            )
        if question is None:
            return {
                "exhausted": True,
                "messages": [AIMessage(content="题库中该难度的题目已用完，本次面试即将结束。")],
            }
        asked_ids = list(state.get("asked_ids", []))
        asked_ids.append(question.question_id)
        label = DIFFICULTY_LABELS.get(difficulty, difficulty)
        number = state.get("completed", 0) + 1
        return {
            "messages": [AIMessage(content=f"第 {number} 题（{label}）：{question.question}")],
            "current_question_id": question.question_id,
            "current_question": question.question,
            "current_reference_points": list(question.reference_points or []),
            "current_followups": list(question.followups or []),
            "asked_ids": asked_ids,
            "followup_count": 0,
            "pending_main_answer": "",
            "pending_followups": [],
            "last_judge_action": None,
        }

    def judge(state: MockInterviewState, config=None) -> dict[str, Any]:
        answer = last_user_text(state.get("messages") or [])
        if _is_exit(answer):
            # 确定性退出：不调 LLM、不计入作答；score（无未评分题零调用）→ report
            return {"last_judge_action": "end"}
        followup_count = state.get("followup_count", 0)
        pending_followups = list(state.get("pending_followups", []))
        main_answer = answer
        if followup_count == 0:
            updates: dict[str, Any] = {"pending_main_answer": answer}
        else:
            if pending_followups:
                pending_followups[-1]["answer"] = answer
            main_answer = state.get("pending_main_answer", "")
            updates = {"pending_followups": pending_followups}

        followup_lines = (
            "（无）"
            if not pending_followups
            else "\n".join(
                f"{i}. 问：{item['question']}\n   答：{item['answer']}"
                for i, item in enumerate(pending_followups, start=1)
            )
        )
        system = registry.render(
            PROMPT_JUDGE,
            question=state.get("current_question", ""),
            reference_points=_numbered(state.get("current_reference_points")),
            bank_followups=_numbered(state.get("current_followups")),
            answer=answer,
            followups=followup_lines,
            followup_count=str(followup_count),
            max_followups=str(max_followups),
        )
        raw = llm.invoke([system], config=config).content
        data = parse_json_text(raw)
        action = (data or {}).get("action")
        if action not in JUDGE_ACTIONS:
            if data is None and bus is not None:
                bus.fire(EVENT_ERROR, payload={"node": "judge", "raw": str(raw)[:200]})
            action = "move_on"  # 解析失败兜底：视为回答完毕，保证流程前进
        if action == "followup" and followup_count >= max_followups:
            action = "move_on"  # 防御：追问已满不再追问
        followup = (data or {}).get("followup") if action == "followup" else None
        feedback = (data or {}).get("feedback") if action == "move_on" else None
        updates.update({"last_judge_action": action})
        if action == "followup":
            updates["last_followup_text"] = followup or "请再展开说说。"
        if action == "move_on":
            answers = list(state.get("answers", []))
            answers.append(
                {
                    "question_id": state.get("current_question_id"),
                    "question": state.get("current_question", ""),
                    "difficulty": state.get("difficulty", DIFFICULTY_ORDER[1]),
                    "answer": main_answer,
                    "followups": pending_followups,
                }
            )
            updates["answers"] = answers
            updates["completed"] = state.get("completed", 0) + 1
        if feedback:
            updates["messages"] = [AIMessage(content=feedback)]
        return updates

    def append_followup(state: MockInterviewState) -> dict[str, Any]:
        text = state.get("last_followup_text") or "请再展开说说。"
        pending = list(state.get("pending_followups", []))
        pending.append({"question": text, "answer": ""})
        return {
            "messages": [AIMessage(content=text)],
            "pending_followups": pending,
            "followup_count": state.get("followup_count", 0) + 1,
        }

    def score(state: MockInterviewState, config=None) -> dict[str, Any]:
        answers = state.get("answers", [])
        scores = state.get("scores", [])
        scored_ids = {item["question_id"] for item in scores}
        unscored = [a for a in answers if a["question_id"] not in scored_ids]
        if not unscored:
            return {}
        blocks = []
        for i, a in enumerate(unscored, start=1):
            row = store.get(a["question_id"]) if a.get("question_id") else None
            points = _numbered(row.reference_points if row else None)
            blocks.append(
                f"题目 {i}（{a['question_id']}）：{a['question']}\n"
                f"评分要点：\n{points}\n回答：{a['answer']}"
            )
        qa_blocks = "\n\n".join(blocks)
        system = registry.render(PROMPT_SCORE, qa_blocks=qa_blocks)
        raw = llm.invoke([system], config=config).content
        data = parse_json_text(raw)
        results = data.get("results") if data and isinstance(data.get("results"), list) else None
        if results is None:
            if bus is not None:
                bus.fire(EVENT_ERROR, payload={"node": "score", "raw": str(raw)[:200]})
            results = [{} for _ in unscored]  # 解析失败：默认 3 分，流程继续
        unscored_ids = {a["question_id"] for a in unscored}
        new_scores: list[dict] = []
        for index, answer in enumerate(unscored):
            item = (
                results[index] if index < len(results) and isinstance(results[index], dict) else {}
            )
            claimed = item.get("question_id")
            # LLM 声称的 id 仅在能对上未评分题目时采用，否则按位置回退真实 id（保证去重一致）
            qid = claimed if claimed in unscored_ids else answer["question_id"]
            new_scores.append(
                {
                    "question_id": qid,
                    "correctness": _clamp(item.get("correctness")),
                    "depth": _clamp(item.get("depth")),
                    "expression": _clamp(item.get("expression")),
                    "comment": item.get("comment", ""),
                    "strengths": item.get("strengths") or [],
                    "weaknesses": item.get("weaknesses") or [],
                }
            )
        difficulty = _adapt_difficulty(state.get("difficulty", DIFFICULTY_ORDER[1]), new_scores)
        total_scored = len(scores) + len(new_scores)
        lines = [f"阶段评分：本轮 {len(new_scores)} 题（累计已评 {total_scored} 题）："]
        lines += [
            f"{i}. 正确性 {s['correctness']} / 深度 {s['depth']} / 表达 {s['expression']}"
            f"（{s['comment'] or '无点评'}）"
            for i, s in enumerate(new_scores, start=1)
        ]
        return {
            "scores": [*scores, *new_scores],
            "scored_count": state.get("scored_count", 0) + len(new_scores),
            "difficulty": difficulty,
            "messages": [AIMessage(content="\n".join(lines))],
        }

    def report(state: MockInterviewState, config=None) -> dict[str, Any]:
        answers = state.get("answers", [])
        scores = state.get("scores", [])
        by_qid = {item["question_id"]: item for item in scores}
        blocks = []
        for i, a in enumerate(answers, start=1):
            s = by_qid.get(a["question_id"])
            if s is None:
                continue
            lines = [
                f"{i}. {a['question']}（难度 {a['difficulty']}）",
                f"   得分：正确性 {s['correctness']} / 深度 {s['depth']} / 表达 {s['expression']}",
            ]
            strengths = [x for x in (s.get("strengths") or []) if x]
            weaknesses = [x for x in (s.get("weaknesses") or []) if x]
            if strengths:
                lines.append(f"   亮点：{'、'.join(strengths)}")
            if weaknesses:
                lines.append(f"   不足：{'、'.join(weaknesses)}")
            blocks.append("\n".join(lines))
        system = registry.render(
            PROMPT_REPORT,
            completed=str(state.get("completed", 0)),
            score_blocks="\n\n".join(blocks) or "（无）",
        )
        text = stream_text(llm, [system], config=config)  # 终稿 token 流式
        if bus is not None:
            bus.fire(
                EVENT_MOCK_INTERVIEW_END,
                payload={
                    "user_id": state.get("user_id", ""),
                    "session_id": state.get("session_id", ""),
                    "completed": state.get("completed", 0),
                    "scores": scores,
                },
            )
        return {"messages": [AIMessage(content=text)], "phase": PHASE_DONE}

    def finish(state: MockInterviewState) -> dict[str, Any]:
        return {"messages": [AIMessage(content=FINISH_REPLY)]}

    def route_entry(state: MockInterviewState) -> str:
        phase = state.get("phase")
        if phase == PHASE_DONE:
            return "finish"
        if phase == PHASE_ANSWERING:
            return "judge"
        return "opening"

    def cond_action(state: MockInterviewState) -> str:
        return "append_followup" if state.get("last_judge_action") == "followup" else "next"

    def cond_next(state: MockInterviewState) -> str:
        if state.get("last_judge_action") == "end":
            return "score"  # 提前结束：补评分后出报告
        if state.get("completed", 0) >= max_questions:
            return "score"
        if state.get("completed", 0) % score_every == 0:
            return "score"
        return "ask"

    def cond_after_ask(state: MockInterviewState) -> str:
        return "score" if state.get("exhausted") else END

    def cond_after_score(state: MockInterviewState) -> str:
        if state.get("last_judge_action") == "end" or state.get("exhausted"):
            return "report"
        if state.get("completed", 0) >= max_questions:
            return "report"
        return "ask"

    builder = StateGraph(MockInterviewState)
    builder.add_node("opening", opening)
    builder.add_node("ask", ask)
    builder.add_node("judge", judge)
    builder.add_node("append_followup", append_followup)
    builder.add_node("score", score)
    builder.add_node("report", report)
    builder.add_node("finish", finish)
    builder.add_node("next", lambda state: {})  # 纯路由节点

    builder.add_conditional_edges(
        START, route_entry, {"opening": "opening", "judge": "judge", "finish": "finish"}
    )
    builder.add_edge("opening", "ask")
    builder.add_conditional_edges("ask", cond_after_ask, {"score": "score", END: END})
    builder.add_conditional_edges(
        "judge", cond_action, {"append_followup": "append_followup", "next": "next"}
    )
    builder.add_edge("append_followup", END)
    builder.add_conditional_edges("next", cond_next, {"score": "score", "ask": "ask"})
    builder.add_conditional_edges("score", cond_after_score, {"report": "report", "ask": "ask"})
    builder.add_edge("report", END)
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)
