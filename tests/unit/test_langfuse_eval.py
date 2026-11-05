"""Langfuse 评估运行器单测（AC4）：task/打分/提示词替换/实验接线（StubLLM + FakeLangfuse）。"""

from __future__ import annotations

import pytest

from app.observability.langfuse_eval import (
    RunSpec,
    intent_evaluator,
    judge_evaluator,
    langfuse_renderer,
    local_renderer,
    make_b1_qa_task,
    make_b1_quality_evaluator,
    make_intent_task,
    make_judge_task,
    run_experiment,
)
from tests.conftest import StubLLM
from tests.support.langfuse import FakeLangfuse

INTENT_JSON = '{"intent": "mock_interview", "confidence": 0.9}'
JUDGE_JSON = '{"action": "followup", "followup": "为什么？", "feedback": null}'


def _dataset_item(input: dict, expected: dict):
    from tests.support.langfuse import FakeDatasetItem

    return FakeDatasetItem("item-1", input, expected)


# ---------- intent 任务 ----------


def test_intent_task_correct_parse():
    llm = StubLLM([INTENT_JSON])
    task = make_intent_task(_renderer(""), llm)
    assert task(
        item=_dataset_item({"user_message": "来场面试", "history_summary": "（无）"}, {})
    ) == {"intent": "mock_interview"}


def _renderer(template: str):
    """固定模板渲染器：无视变量，直接返回 template。"""

    def render(**vars):
        return template

    return render


def test_intent_task_renders_user_message_and_history():
    llm = StubLLM([INTENT_JSON])
    renderer = _renderer("用户：$user_message / 历史：$history_summary")
    task = make_intent_task(renderer, llm)
    task(item=_dataset_item({"user_message": "帮我模拟面试", "history_summary": "（无）"}, {}))
    assert llm.calls[0][0] == "用户：$user_message / 历史：$history_summary"


def test_intent_task_parse_failure_scores_none():
    llm = StubLLM(["抱歉，我不明白你的意思"])
    task = make_intent_task(_renderer(""), llm)
    assert task(item=_dataset_item({"user_message": "x"}, {})) == {"intent": None}


def test_intent_task_bad_schema_scores_none():
    llm = StubLLM(['{"intent": "unknown_intent", "confidence": 0.9}'])
    task = make_intent_task(_renderer(""), llm)
    assert task(item=_dataset_item({"user_message": "x"}, {})) == {"intent": None}


# ---------- judge 任务 ----------


def test_judge_task_parses_action_and_numbers_points():
    llm = StubLLM([JUDGE_JSON])
    task = make_judge_task(_renderer(""), llm)
    item = _dataset_item(
        {
            "question": "什么是 GIL？",
            "reference_points": ["要点A", "要点B"],
            "bank_followups": ["追问一"],
            "answer": "GIL 是全局锁",
            "followups": [],
            "followup_count": 0,
            "max_followups": 3,
        },
        {"action": "followup"},
    )
    output = task(item=item)
    assert output["action"] == "followup" and output["json_valid"] is True


def test_judge_task_renders_all_variables():
    llm = StubLLM([JUDGE_JSON])
    seen: dict = {}

    def render(**vars):
        seen.update(vars)
        return ""

    task = make_judge_task(render, llm)
    task(
        item=_dataset_item(
            {
                "question": "题",
                "reference_points": ["要点A", "要点B"],
                "bank_followups": ["追问一", "追问二"],
                "answer": "答",
                "followups": ["已追问1"],
                "followup_count": 1,
                "max_followups": 3,
            },
            {"action": "followup"},
        )
    )
    assert seen["reference_points"] == "1. 要点A\n2. 要点B"
    assert seen["bank_followups"] == "1. 追问一\n2. 追问二"
    assert seen["followups"] == "已追问1"
    assert seen["followup_count"] == "1" and seen["max_followups"] == "3"


def test_judge_task_invalid_json():
    llm = StubLLM(["不是 JSON"])
    task = make_judge_task(_renderer(""), llm)
    output = task(item=_dataset_item({"question": "题", "answer": "答"}, {}))
    assert output == {"json_valid": False}


# ---------- 打分器 ----------


def test_intent_evaluator_scores():
    base = dict(input={}, metadata={})
    assert intent_evaluator(
        output={"intent": "chitchat"}, expected_output={"intent": "chitchat"}, **base
    ) == [
        {"name": "intent_match", "value": 1.0, "comment": ""},
        {"name": "json_valid", "value": 1.0},
    ]
    wrong = intent_evaluator(
        output={"intent": "knowledge_qa"}, expected_output={"intent": "chitchat"}, **base
    )
    assert wrong[0]["value"] == 0.0 and wrong[1]["value"] == 1.0
    failed = intent_evaluator(
        output={"intent": None}, expected_output={"intent": "chitchat"}, **base
    )
    assert failed[0]["value"] == 0.0 and failed[1]["value"] == 0.0


def test_judge_evaluator_scores():
    base = dict(input={}, metadata={})
    ok = judge_evaluator(
        output={"action": "end", "json_valid": True}, expected_output={"action": "end"}, **base
    )
    assert ok[0]["value"] == 1.0 and ok[1]["value"] == 1.0
    mismatch = judge_evaluator(
        output={"action": "move_on", "json_valid": True}, expected_output={"action": "end"}, **base
    )
    assert mismatch[0]["value"] == 0.0 and mismatch[1]["value"] == 1.0
    invalid = judge_evaluator(
        output={"json_valid": False}, expected_output={"action": "end"}, **base
    )
    assert invalid[0]["value"] == 0.0 and invalid[1]["value"] == 0.0


# ---------- b1_qa 任务与 LLM-as-judge ----------


def test_b1_qa_task_prompt_contains_question_and_position():
    llm = StubLLM(["GIL 是全局解释器锁……"])
    task = make_b1_qa_task(llm)
    output = task(
        item=_dataset_item(
            {"question": "什么是 GIL？", "position": "python_backend"}, {"reference_points": ["x"]}
        )
    )
    assert output == {"answer": "GIL 是全局解释器锁……"}
    assert "什么是 GIL？" in llm.calls[0][0] and "python_backend" in llm.calls[0][0]


def test_b1_quality_evaluator_scores_and_clamps(registry):
    judge = StubLLM(
        [
            '{"coverage": 4, "relevance": 2, "comment": "覆盖好"}',
            '{"coverage": 99, "relevance": -3, "comment": "越界"}',
            "不是 JSON",
        ]
    )
    evaluator = make_b1_quality_evaluator(local_renderer(registry, "evaluation/b1_quality"), judge)
    base = dict(
        input={"question": "什么是 GIL？"},
        expected_output={"reference_points": ["要点1", "要点2"]},
        metadata={},
    )
    first = evaluator(output={"answer": "答"}, **base)
    assert first == [
        {"name": "coverage", "value": 4.0, "comment": "覆盖好"},
        {"name": "relevance", "value": 2.0, "comment": "覆盖好"},
    ]
    second = evaluator(output={"answer": "答"}, **base)
    assert second[0]["value"] == 5.0 and second[1]["value"] == 0.0
    third = evaluator(output={"answer": "答"}, **base)
    assert third[0]["value"] == 0.0 and third[1]["value"] == 0.0
    assert third[0]["comment"] == "judge 解析失败"


def test_b1_quality_evaluator_renders_local_template(registry):
    judge = StubLLM(['{"coverage": 2, "relevance": 1, "comment": "ok"}'])
    evaluator = make_b1_quality_evaluator(local_renderer(registry, "evaluation/b1_quality"), judge)
    evaluator(
        input={"question": "什么是 GIL？"},
        output={"answer": "GIL 是全局锁"},
        expected_output={"reference_points": ["要点1", "要点2"]},
        metadata={},
    )
    prompt = judge.calls[0][0]
    assert "什么是 GIL？" in prompt and "1. 要点1" in prompt and "2. 要点2" in prompt


# ---------- 运行器接线 ----------


def test_run_spec_validation():
    with pytest.raises(ValueError):
        RunSpec(dataset_name="ds", task_name="unknown")
    with pytest.raises(ValueError):
        run_experiment(
            FakeLangfuse(), spec=RunSpec(dataset_name="ds", task_name="intent"), llm=StubLLM([])
        )


def test_run_experiment_intent_end_to_end():
    client = FakeLangfuse()
    client.seed_prompt(
        "offercoach/router/intent",
        "意图分类，用户：{{user_message}}。输出 JSON。",
        version=1,
        labels=("sv1", "latest"),
    )
    dataset = client.create_dataset(name="oc-intent-v1")
    from tests.support.langfuse import FakeDatasetItem

    dataset.items = [
        FakeDatasetItem(
            "a1", {"user_message": "你好", "history_summary": "（无）"}, {"intent": "chitchat"}
        ),
        FakeDatasetItem(
            "a2",
            {"user_message": "面试", "history_summary": "（无）"},
            {"intent": "mock_interview"},
        ),
    ]
    llm = StubLLM(
        [
            '{"intent": "chitchat", "confidence": 0.9}',
            '{"intent": "mock_interview", "confidence": 0.9}',
        ]
    )
    spec = RunSpec(
        dataset_name="oc-intent-v1",
        task_name="intent",
        prompt_name="offercoach/router/intent",
        run_name="intent-latest",
    )
    result = run_experiment(client, spec=spec, llm=llm)
    experiment = dataset.experiments[0]
    assert experiment["name"] == "oc-intent-v1"  # 实验名缺省=数据集名
    assert experiment["run_name"] == "intent-latest"
    assert [r["item_id"] for r in experiment["runs"]] == ["a1", "a2"]
    assert [s["name"] for s in experiment["runs"][0]["scores"]] == ["intent_match", "json_valid"]
    assert experiment["runs"][0]["scores"][0]["value"] == 1.0
    assert result.dataset_run_id
    # 提示词从 Langfuse 拉取（mustache 编译）
    assert "用户：你好" in llm.calls[0][0]


def test_run_experiment_prompt_version_replacement():
    """提示词替换：同一数据集以不同版本跑，任务渲染的提示词不同。"""
    client = FakeLangfuse()
    client.seed_prompt(
        "offercoach/mock_interview/judge", "旧版判题：{{question}}", version=1, labels=("sv1",)
    )
    client.seed_prompt(
        "offercoach/mock_interview/judge",
        "新版判题：{{question}}",
        version=2,
        labels=("sv2", "latest"),
    )
    dataset = client.create_dataset(name="oc-judge-v1")
    from tests.support.langfuse import FakeDatasetItem

    dataset.items = [
        FakeDatasetItem(
            "j1",
            {
                "question": "什么是 GIL？",
                "reference_points": ["要点1"],
                "bank_followups": [],
                "answer": "GIL 是全局锁",
                "followups": [],
                "followup_count": 0,
                "max_followups": 3,
            },
            {"action": "move_on"},
        )
    ]
    llm = StubLLM(['{"action": "move_on", "followup": null, "feedback": null}'])
    spec = RunSpec(
        dataset_name="oc-judge-v1",
        task_name="judge",
        prompt_name="offercoach/mock_interview/judge",
        prompt_version=1,
    )
    run_experiment(client, spec=spec, llm=llm)
    assert llm.calls[0][0].startswith("旧版判题")
    # 最新版（label=latest）路径
    llm2 = StubLLM(['{"action": "move_on", "followup": null, "feedback": null}'])
    run_experiment(
        client,
        spec=RunSpec(
            dataset_name="oc-judge-v1",
            task_name="judge",
            prompt_name="offercoach/mock_interview/judge",
        ),
        llm=llm2,
    )
    assert llm2.calls[0][0].startswith("新版判题")
    # 两个 run 同名实验，云端可对比
    names = [e["name"] for e in dataset.experiments]
    assert names == ["oc-judge-v1", "oc-judge-v1"]
    assert len({e["run_name"] for e in dataset.experiments}) == 2


def test_run_experiment_b1_qa_with_local_registry(registry):
    client = FakeLangfuse()
    dataset = client.create_dataset(name="oc-b1-qa-v1")
    from tests.support.langfuse import FakeDatasetItem

    dataset.items = [
        FakeDatasetItem(
            "b1",
            {"question": "什么是 GIL？", "position": "python_backend"},
            {"reference_points": ["全局解释器锁"]},
        )
    ]
    llm = StubLLM(["GIL 是全局解释器锁，限制多线程并行。"])
    judge = StubLLM(['{"coverage": 1, "relevance": 2, "comment": "切题"}'])
    spec = RunSpec(dataset_name="oc-b1-qa-v1", task_name="b1_qa")
    run_experiment(client, spec=spec, llm=llm, judge_llm=judge, registry=registry)
    experiment = dataset.experiments[0]
    scores = experiment["runs"][0]["scores"]
    assert [(s["name"], s["value"]) for s in scores] == [
        ("coverage", 1.0),
        ("relevance", 2.0),
    ]


def test_langfuse_renderer_uses_latest_label_by_default():
    client = FakeLangfuse()
    client.seed_prompt("offercoach/x", "内容：{{v}}", version=3, labels=("latest",))
    render = langfuse_renderer(client, "offercoach/x", None)
    assert render(v="值") == "内容：值"
    render_v3 = langfuse_renderer(client, "offercoach/x", 3)
    assert render_v3(v="值") == "内容：值"
