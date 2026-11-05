"""L2: A1 模拟面试工作流 — AC1~AC10 全路径 + 提示词快照。"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.graphs.workflows.mock_interview import (
    FINISH_REPLY,
    PROMPT_JUDGE,
    PROMPT_OPENING,
    PROMPT_REPORT,
    PROMPT_SCORE,
    build_mock_interview_graph,
)
from app.hooks.bus import EVENT_ERROR, EVENT_MOCK_INTERVIEW_END
from app.prompts.registry import PromptRegistry
from tests.conftest import TEMPLATES_DIR
from tests.support.question_bank import make_record, make_store

CONFIG = {"configurable": {"thread_id": "mi-1"}}

ENTRY_QUESTIONS = {
    "q_java_basics_001": "谈谈面向对象的三大特性",
    "q_java_basics_002": "String、StringBuilder 与 StringBuffer 的区别",
    "q_java_basics_003": "Java 中 == 与 equals 的区别",
}
MEDIUM_QUESTIONS = {
    "q_jvm_001": "JVM 运行时数据区有哪些",
    "q_jvm_002": "GC 的常见算法与适用场景",
}
HARD_QUESTIONS = {
    "q_concurrent_001": "线上线程池打满导致接口超时，如何排查",
    "q_concurrent_002": "谈谈你对 volatile 的理解",
}

MOCK_BANK_RECORDS = [
    make_record(
        "java_basics",
        1,
        difficulty="entry",
        qtype="knowledge",
        question=ENTRY_QUESTIONS["q_java_basics_001"],
    ),
    make_record(
        "java_basics",
        2,
        difficulty="entry",
        qtype="knowledge",
        question=ENTRY_QUESTIONS["q_java_basics_002"],
    ),
    make_record(
        "java_basics",
        3,
        difficulty="entry",
        qtype="knowledge",
        question=ENTRY_QUESTIONS["q_java_basics_003"],
    ),
    make_record(
        "jvm", 1, difficulty="medium", qtype="knowledge", question=MEDIUM_QUESTIONS["q_jvm_001"]
    ),
    make_record(
        "jvm", 2, difficulty="medium", qtype="knowledge", question=MEDIUM_QUESTIONS["q_jvm_002"]
    ),
    make_record(
        "concurrent",
        1,
        difficulty="hard",
        qtype="scenario",
        question=HARD_QUESTIONS["q_concurrent_001"],
    ),
    make_record(
        "concurrent",
        2,
        difficulty="hard",
        qtype="knowledge",
        question=HARD_QUESTIONS["q_concurrent_002"],
    ),
]


def build_graph(tmp_path, llm, *, records=MOCK_BANK_RECORDS, bus=None, **kwargs):
    store = make_store(tmp_path, records)
    return build_mock_interview_graph(
        llm, store, PromptRegistry(TEMPLATES_DIR), bus=bus, checkpointer=InMemorySaver(), **kwargs
    )


def turn(graph, text, **extra):
    """默认初始难度 entry（与根图缺省 medium 不同，测试需覆盖 entry 题库）；extra 可覆盖。"""
    payload: dict = {"messages": [HumanMessage(content=text)]}
    payload.setdefault("initial_difficulty", "entry")
    payload.update(extra)
    return graph.invoke(payload, CONFIG)


def judge_json(action, *, followup=None, feedback=None):
    return json.dumps(
        {"action": action, "followup": followup, "feedback": feedback}, ensure_ascii=False
    )


def test_ac1_opening_asks_first_question(tmp_path, make_llm):
    """开场白 + 第 1 题（难度=初始档），asked_ids 记录，题面来自题库。"""
    opening_text = "欢迎参加本次模拟面试！"
    llm = make_llm([opening_text])
    graph = build_graph(tmp_path, llm)
    result = turn(graph, "开始模拟面试")
    texts = [m.content for m in result["messages"]]
    assert texts == ["开始模拟面试", opening_text, texts[2]]
    assert texts[2].startswith("第 1 题（入门）：")
    assert texts[2].split("：", 1)[1] in ENTRY_QUESTIONS.values()
    assert result["phase"] == "answering"
    assert result["difficulty"] == "entry"
    assert result["asked_ids"][0] in ENTRY_QUESTIONS
    assert result["current_question"] in ENTRY_QUESTIONS.values()
    assert result["completed"] == 0


def test_ac1b_ask_carries_bank_assets(tmp_path, make_llm):
    """ask 把题库 reference_points/followups 带入 state，与所抽题一致。"""
    llm = make_llm(["开场。"])
    graph = build_graph(tmp_path, llm)
    result = turn(graph, "开始")
    qid = result["current_question_id"]
    record = next(r for r in MOCK_BANK_RECORDS if r["question_id"] == qid)
    assert result["current_reference_points"] == record["reference_points"]
    assert result["current_followups"] == record["followups"]


def test_position_slot_mismatch_falls_back_to_default_bank(tmp_path, make_llm):
    """槽位岗位与题库口径（java_backend）不一致时回退默认岗位，不误判题库耗尽。"""
    llm = make_llm(["开场。"])
    graph = build_graph(tmp_path, llm)
    result = turn(graph, "开始", position="Java 后端开发")
    assert result["current_question"] in ENTRY_QUESTIONS.values()
    assert result["exhausted"] is False


def test_position_spoken_alias_maps_to_bank_position(tmp_path, make_llm):
    """三期：口语岗位「C++」归一化为 cpp_backend 直接抽到该岗位题（不靠回退）。"""
    cpp_records = [
        make_record(
            "cpp_basics",
            1,
            difficulty="entry",
            qtype="knowledge",
            position="cpp_backend",
            source="ai_seed_v3",
            question="谈谈 C++ 的 RAII",
        )
    ]
    llm = make_llm(["开场。"])
    graph = build_graph(tmp_path, llm, records=cpp_records)
    result = turn(graph, "开始", position="C++")
    assert result["current_question"] == "谈谈 C++ 的 RAII"
    assert result["asked_ids"] == ["q_cpp_basics_001"]
    assert result["exhausted"] is False


def test_ac2_followup_chain_records_answer_pairs(tmp_path, make_llm):
    """judge=followup → 追问落盘 + count+1；第二答 move_on → answers 记录主答+追问对。"""
    followup_text = "你提到了核心参数，那拒绝策略有哪些？"
    llm = make_llm(
        [
            "开场。",
            judge_json("followup", followup=followup_text),
            judge_json("move_on", feedback="回答不错。"),
        ]
    )
    graph = build_graph(tmp_path, llm)
    first = turn(graph, "开始")
    qid = first["current_question_id"]
    second = turn(graph, "线程池有核心线程数、最大线程数等参数")
    assert second["followup_count"] == 1
    assert second["messages"][-1].content == followup_text
    assert second["answers"] == []
    third = turn(graph, "拒绝策略有 AbortPolicy、CallerRunsPolicy 等四种")
    assert third["completed"] == 1
    answer = third["answers"][0]
    assert answer["question_id"] == qid
    assert answer["answer"] == "线程池有核心线程数、最大线程数等参数"
    assert answer["followups"] == [
        {"question": followup_text, "answer": "拒绝策略有 AbortPolicy、CallerRunsPolicy 等四种"}
    ]
    assert any(m.content == "回答不错。" for m in third["messages"])  # move_on 点评落盘
    assert third["messages"][-1].content.startswith("第 2 题")  # 之后继续问下一题


def test_ac3_dont_know_gets_feedback_and_counts(tmp_path, make_llm):
    """不会 → move_on + feedback 讲解落盘、题目计为已完成。"""
    feedback = "线程池核心参数包括 corePoolSize、maximumPoolSize、keepAliveTime 与工作队列。"
    llm = make_llm(["开场。", judge_json("move_on", feedback=feedback)])
    graph = build_graph(tmp_path, llm)
    turn(graph, "开始")
    result = turn(graph, "这个我不太会")
    assert any(m.content == feedback for m in result["messages"])  # 要点讲解落盘
    assert result["messages"][-1].content.startswith("第 2 题")  # 之后继续问下一题
    assert result["completed"] == 1
    assert result["answers"][0]["answer"] == "这个我不太会"


def test_ac4_score_cycle_clamps_scores_and_reports(tmp_path, make_llm, bus):
    """每 score_every 题评分：反馈消息 + scores 记录，分数越界钳制到 1-5。"""
    score1 = json.dumps(
        {
            "results": [
                {
                    "question_id": "a",
                    "correctness": 9,
                    "depth": 0,
                    "expression": 4,
                    "comment": "基础扎实",
                    "strengths": ["全面"],
                    "weaknesses": ["细节不足"],
                },
                {
                    "question_id": "b",
                    "correctness": 4,
                    "depth": 4,
                    "expression": 5,
                    "comment": "不错",
                },
            ]
        },
        ensure_ascii=False,
    )
    score2 = json.dumps(
        {
            "results": [
                {
                    "question_id": "c",
                    "correctness": 3,
                    "depth": 3,
                    "expression": 3,
                    "comment": "尚可",
                }
            ]
        },
        ensure_ascii=False,
    )
    report_text = "总结报告……"
    llm = make_llm(
        [
            "开场。",
            judge_json("move_on"),
            judge_json("move_on"),
            score1,
            judge_json("move_on"),
            score2,
            report_text,
        ]
    )
    graph = build_graph(tmp_path, llm, bus=bus, max_questions=3, score_every=2)
    turn(graph, "开始")
    r1 = turn(graph, "第一题回答")
    assert r1["completed"] == 1
    assert "第 2 题" in r1["messages"][-1].content  # 未到评分周期直接下一题
    r2 = turn(graph, "第二题回答")
    assert r2["completed"] == 2
    assert len(r2["scores"]) == 2
    first_score = r2["scores"][0]
    assert first_score["correctness"] == 5  # 9 钳制
    assert first_score["depth"] == 1  # 0 钳制
    assert first_score["expression"] == 4
    assert any("阶段评分" in m.content for m in r2["messages"])  # 中间反馈消息
    assert "第 3 题" in r2["messages"][-1].content  # 评分后继续
    r3 = turn(graph, "第三题回答")
    assert r3["completed"] == 3
    assert len(r3["scores"]) == 3
    assert r3["messages"][-1].content == report_text
    assert r3["phase"] == "done"
    assert bus.events[EVENT_MOCK_INTERVIEW_END][0].payload["completed"] == 3


def test_ac5_difficulty_adaptation_up_and_down(tmp_path, make_llm):
    """均分 ≥4 升档（后续抽题难度变化）；≤2.5 降档。"""
    all_five = json.dumps(
        {
            "results": [
                {"question_id": "a", "correctness": 5, "depth": 5, "expression": 5},
                {"question_id": "b", "correctness": 5, "depth": 5, "expression": 5},
            ]
        }
    )
    all_one = json.dumps(
        {
            "results": [
                {"question_id": "a", "correctness": 1, "depth": 1, "expression": 1},
                {"question_id": "b", "correctness": 1, "depth": 1, "expression": 1},
            ]
        }
    )
    llm = make_llm(
        [
            "开场。",
            judge_json("move_on"),
            judge_json("move_on"),
            all_five,
            judge_json("move_on"),
            judge_json("move_on"),
            all_one,
            "报告。",
        ]
    )
    graph = build_graph(tmp_path, llm, max_questions=4, score_every=2)
    turn(graph, "开始")  # Q1 entry
    turn(graph, "答1")  # Q2 entry
    r_score = turn(graph, "答2")  # 5 分 → 升档 medium → Q3
    assert r_score["difficulty"] == "medium"
    assert "中等" in r_score["messages"][-1].content
    assert r_score["current_question_id"] in MEDIUM_QUESTIONS
    r_q4 = turn(graph, "答3")  # Q4 = 另一道 medium
    assert r_q4["current_question_id"] in set(MEDIUM_QUESTIONS) - {r_score["current_question_id"]}
    r_done = turn(graph, "答4")  # 1 分 → 降档 entry → report
    assert r_done["difficulty"] == "entry"
    assert r_done["completed"] == 4
    assert r_done["phase"] == "done"


def test_ac5b_difficulty_never_exceeds_hard(tmp_path, make_llm):
    all_five = json.dumps(
        {
            "results": [
                {"question_id": "a", "correctness": 5, "depth": 5, "expression": 5},
                {"question_id": "b", "correctness": 5, "depth": 5, "expression": 5},
            ]
        }
    )
    llm = make_llm(["开场。", judge_json("move_on"), judge_json("move_on"), all_five, "报告。"])
    graph = build_graph(tmp_path, llm, max_questions=2, score_every=2)
    turn(graph, "开始", initial_difficulty="hard")
    r1 = turn(graph, "答1")
    assert r1["difficulty"] == "hard"
    r2 = turn(graph, "答2")  # 5 分但已最高档
    assert r2["difficulty"] == "hard"
    assert r2["phase"] == "done"


def test_ac6_early_end_scores_remaining_then_report(tmp_path, make_llm, bus):
    """judge=end → 当前题不计入 → 未评分题补评分 → report → done + 事件。"""
    score_json = json.dumps(
        {
            "results": [
                {
                    "question_id": "a",
                    "correctness": 4,
                    "depth": 4,
                    "expression": 4,
                    "comment": "尚可",
                }
            ]
        }
    )
    llm = make_llm(["开场。", judge_json("move_on"), judge_json("end"), score_json, "报告。"])
    graph = build_graph(tmp_path, llm, bus=bus, max_questions=6, score_every=2)
    turn(graph, "开始")  # Q1
    turn(graph, "答1")  # Q1 完成 → ask Q2
    result = turn(graph, "结束吧")  # Q2 judge=end → 补评分 → report
    assert result["completed"] == 1  # Q2 不计入
    assert len(result["answers"]) == 1
    assert len(result["scores"]) == 1  # 补评分
    assert result["phase"] == "done"
    events = bus.events[EVENT_MOCK_INTERVIEW_END]
    assert len(events) == 1
    assert events[0].payload["completed"] == 1
    assert len(events[0].payload["scores"]) == 1


def test_ac7_full_round_reaches_done(tmp_path, make_llm, bus):
    """completed=max_questions → 最终评分 → report → phase=done。"""
    score_json = json.dumps(
        {
            "results": [
                {"question_id": "a", "correctness": 4, "depth": 3, "expression": 4},
                {"question_id": "b", "correctness": 3, "depth": 4, "expression": 3},
            ]
        }
    )
    llm = make_llm(["开场。", judge_json("move_on"), judge_json("move_on"), score_json, "报告。"])
    graph = build_graph(tmp_path, llm, bus=bus, max_questions=2, score_every=2)
    turn(graph, "开始")
    turn(graph, "答1")
    result = turn(graph, "答2")
    assert result["completed"] == 2
    assert len(result["scores"]) == 2
    assert result["phase"] == "done"
    assert bus.events[EVENT_MOCK_INTERVIEW_END][0].payload["completed"] == 2


def test_ac8_question_exhausted_ends_with_report(tmp_path, make_llm):
    """ask 无题 → exhausted → score → report → done。"""
    score_json = json.dumps(
        {"results": [{"question_id": "a", "correctness": 4, "depth": 4, "expression": 4}]}
    )
    llm = make_llm(["开场。", judge_json("move_on"), score_json, "报告。"])
    graph = build_graph(
        tmp_path, llm, records=MOCK_BANK_RECORDS[:1], max_questions=3, score_every=2
    )
    turn(graph, "开始")  # 唯一一道 entry 题
    result = turn(graph, "答1")  # move_on → ask 无题 → exhausted → score → report
    assert result["exhausted"] is True
    assert result["completed"] == 1
    assert result["phase"] == "done"
    assert any("已用完" in m.content for m in result["messages"])


def test_ac9_messages_after_done_get_finish_reply(tmp_path, make_llm):
    """done 后消息 → 固定礼貌回复，不再触发 judge。"""
    llm = make_llm(["开场。", judge_json("end"), "报告。"])
    graph = build_graph(tmp_path, llm)
    turn(graph, "开始")
    result = turn(graph, "结束")
    assert result["phase"] == "done"
    calls_before = len(llm.calls)
    reply = turn(graph, "谢谢")
    assert reply["messages"][-1].content == FINISH_REPLY
    assert len(llm.calls) == calls_before  # 无 LLM 调用


def test_exit_phrase_short_circuits_judge(tmp_path, make_llm):
    """「结束」等口令（含尾部标点）→ 零 judge 调用直接 end，不计入作答，score→report→done。"""
    llm = make_llm(["开场。", "报告。"])
    graph = build_graph(tmp_path, llm)
    turn(graph, "开始")
    result = turn(graph, "结束。")
    assert result["phase"] == "done"
    assert result["answers"] == []  # 「结束」未被记为作答
    assert result["completed"] == 0
    assert len(llm.calls) == 2  # 仅开场 + 报告（judge/score 均零调用）
    assert result["messages"][-1].content == "报告。"


def test_exit_substring_does_not_short_circuit(tmp_path, make_llm):
    """含「结束」子串的正常作答仍走 judge LLM 正常流转。"""
    llm = make_llm(["开场。", judge_json("move_on", feedback="有道理。")])
    graph = build_graph(tmp_path, llm, max_questions=2, score_every=2)
    turn(graph, "开始")
    result = turn(graph, "线程池的任务什么时候结束？由队列清空决定。")
    assert result["completed"] == 1
    assert len(llm.calls) == 2  # 开场 + judge


def test_ac10_score_parse_failure_defaults_and_error_event(tmp_path, make_llm, bus):
    """评分解析失败 → 默认 3 分 + error 事件，流程继续。"""
    llm = make_llm(
        ["开场。", judge_json("move_on"), judge_json("move_on"), "这不是合法的JSON", "报告。"]
    )
    graph = build_graph(tmp_path, llm, bus=bus, max_questions=2, score_every=2)
    turn(graph, "开始")
    turn(graph, "答1")
    result = turn(graph, "答2")
    assert len(result["scores"]) == 2
    for s in result["scores"]:
        assert (s["correctness"], s["depth"], s["expression"]) == (3, 3, 3)
    assert result["phase"] == "done"
    assert EVENT_ERROR in bus.events
    assert any(e.payload.get("node") == "score" for e in bus.events[EVENT_ERROR])


def test_opening_prompt_snapshot(snapshot, registry):
    rendered = registry.render(
        PROMPT_OPENING,
        position="Java 后端",
        years="3",
        interview_round="1",
        max_questions="6",
        max_followups="2",
        score_every="2",
    )
    assert rendered == snapshot


def test_judge_prompt_snapshot_v1(snapshot, registry):
    """v1 资产锁定：面试已切 v2，v1 渲染结果保持稳定。"""
    rendered = registry.render(
        PROMPT_JUDGE,
        version=1,
        question="谈谈线程池的核心参数",
        answer="corePoolSize、maximumPoolSize 等",
        followups="1. 问：拒绝策略有哪些？\n   答：四种",
        followup_count="1",
        max_followups="2",
    )
    assert rendered == snapshot


def test_judge_prompt_snapshot_v2(snapshot, registry):
    rendered = registry.render(
        PROMPT_JUDGE,
        question="谈谈线程池的核心参数",
        reference_points="1. 核心参数\n2. 拒绝策略",
        bank_followups="1. 参数之间的关系\n2. 拒绝策略的具体应用",
        answer="corePoolSize、maximumPoolSize 等",
        followups="1. 问：拒绝策略有哪些？\n   答：四种",
        followup_count="1",
        max_followups="2",
    )
    assert rendered == snapshot


def test_score_prompt_snapshot_v1(snapshot, registry):
    rendered = registry.render(
        PROMPT_SCORE,
        version=1,
        qa_blocks="题目 1（q_concurrent_001）：谈谈线程池\n回答：核心参数……",
    )
    assert rendered == snapshot


def test_score_prompt_snapshot_v2(snapshot, registry):
    rendered = registry.render(
        PROMPT_SCORE,
        qa_blocks=(
            "题目 1（q_concurrent_001）：谈谈线程池\n评分要点：\n1. 核心参数\n2. 拒绝策略"
            "\n回答：核心参数……"
        ),
    )
    assert rendered == snapshot


def test_report_prompt_snapshot_v1(snapshot, registry):
    rendered = registry.render(
        PROMPT_REPORT,
        version=1,
        completed="2",
        score_blocks="1. 谈谈线程池（难度 entry）\n   得分：正确性 4 / 深度 3 / 表达 4",
    )
    assert rendered == snapshot


def test_report_prompt_snapshot_v2(snapshot, registry):
    rendered = registry.render(
        PROMPT_REPORT,
        completed="2",
        score_blocks=(
            "1. 谈谈线程池（难度 entry）\n   得分：正确性 4 / 深度 3 / 表达 4"
            "\n   亮点：覆盖全面\n   不足：缺少实践细节"
        ),
    )
    assert rendered == snapshot
