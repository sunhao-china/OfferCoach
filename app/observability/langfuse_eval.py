"""Langfuse 评估运行器：数据集 × 提示词 × 真实 LLM → 云端实验 + 分数。

设计（SDK 5.0.4）：
- task / evaluator 均为普通函数（SDK 协议 `(*args, **kwargs)`）；
- 提示词来源二选一：Langfuse Prompt（`--prompt offercoach/router/intent --prompt-version N`
  即提示词替换实验），或本地 YAML（`--prompt-local router/intent` 兜底）；
- 逐条：渲染提示词 → cheap 档 LLM → 解析 → 打分；trace 挂 dataset_item、分数写回
  实验 run，均由 SDK 的 dataset.run_experiment 完成；
- 打分器：intent/judge 确定性（匹配 + JSON 合法性）；b1_qa 用 LLM-as-judge
  （evaluation/b1_quality 模板，coverage 0~5 + relevance 0~2）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.graphs.json_utils import parse_json_text
from app.prompts.registry import PromptRegistry
from app.router.intent import parse_intent_json

TASK_INTENT = "intent"
TASK_JUDGE = "judge"
TASK_B1_QA = "b1_qa"
TASKS = (TASK_INTENT, TASK_JUDGE, TASK_B1_QA)

B1_QUALITY_PROMPT = "evaluation/b1_quality"

_B1_ANSWER_PROMPT = (
    "你是 OfferCoach，一名资深技术面试教练。请回答下面的面试题："
    "先给结论，再分点展开，覆盖关键要点，语言简洁。\n\n"
    "面试题（{position}）：{question}"
)

Renderer = Callable[..., str]
TaskFn = Callable[..., dict]
EvaluatorFn = Callable[..., list[dict]]


class LangfuseClientProtocol(Protocol):
    """langfuse.Langfuse 的最小接口（测试用 Fake 桩）。"""

    def get_prompt(
        self, name: str, *, version: int | None = None, label: str | None = None, **kwargs: Any
    ) -> Any: ...

    def get_dataset(self, name: str, **kwargs: Any) -> Any: ...


@dataclass
class RunSpec:
    """一次评估实验的参数（脚本层解析后传入）。"""

    dataset_name: str
    task_name: str
    run_name: str | None = None
    description: str = ""
    prompt_name: str | None = None  # Langfuse 提示词名（如 offercoach/router/intent）
    prompt_version: int | None = None  # 指定 Langfuse 版本（提示词替换）；缺省 latest
    prompt_local: str | None = None  # 本地 registry 名（如 router/intent）
    prompt_local_version: int | None = None
    experiment_name: str | None = None  # 缺省 = dataset 名（同名实验多 run 对比）
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.task_name not in TASKS:
            raise ValueError(f"未知任务: {self.task_name}（可选 {TASKS}）")


def _item_data(item: Any) -> tuple[dict, dict]:
    """归一化 SDK 传入的 item：兼容 DatasetItem 对象与 dict（单测直调）。"""
    if isinstance(item, dict):
        return item.get("input") or {}, item.get("expected_output") or {}
    return item.input or {}, item.expected_output or {}


def _numbered(points: list) -> str:
    if not points:
        return "（无）"
    return "\n".join(f"{i}. {p}" for i, p in enumerate(points, 1))


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def langfuse_renderer(
    client: LangfuseClientProtocol, prompt_name: str, version: int | None
) -> Renderer:
    """按 Langfuse 提示词渲染（{{var}} 占位符）；取一次避免逐条拉取。"""
    if version is not None:
        prompt = client.get_prompt(prompt_name, version=version)
    else:
        prompt = client.get_prompt(prompt_name, label="latest")

    def render(**vars: Any) -> str:
        return prompt.compile(**vars)

    return render


def local_renderer(
    registry: PromptRegistry, prompt_name: str, version: int | None = None
) -> Renderer:
    """按本地 YAML 模板渲染（$var 占位符，生产同源）。"""

    def render(**vars: Any) -> str:
        return registry.render(prompt_name, version, **vars)

    return render


# ---------- 任务函数 ----------


def make_intent_task(renderer: Renderer, llm: Any) -> TaskFn:
    """意图分类任务：渲染 → LLM → parse_intent_json；失败记 intent=None（0 分）。"""

    def task(*, item: Any, **kwargs: Any) -> dict:
        data, _ = _item_data(item)
        try:
            prompt = renderer(
                user_message=data.get("user_message", ""),
                history_summary=data.get("history_summary", "（无）"),
            )
            response = llm.invoke([prompt])
            return {"intent": parse_intent_json(response.content).intent.value}
        except Exception:
            return {"intent": None}

    return task


def make_judge_task(renderer: Renderer, llm: Any) -> TaskFn:
    """面试官判断任务：judge 模板全变量渲染 → 解析 action JSON。"""

    def task(*, item: Any, **kwargs: Any) -> dict:
        data, _ = _item_data(item)
        followups = data.get("followups") or []
        followup_text = "；".join(str(f) for f in followups) if followups else "（无）"
        try:
            prompt = renderer(
                question=data.get("question", ""),
                reference_points=_numbered(data.get("reference_points") or []),
                bank_followups=_numbered(data.get("bank_followups") or []),
                answer=data.get("answer", ""),
                followups=followup_text,
                followup_count=str(data.get("followup_count", 0)),
                max_followups=str(data.get("max_followups", 3)),
            )
            response = llm.invoke([prompt])
            parsed = parse_json_text(str(response.content))
            if parsed is None:
                return {"json_valid": False}
            return {
                "action": parsed.get("action"),
                "followup": parsed.get("followup"),
                "feedback": parsed.get("feedback"),
                "json_valid": True,
            }
        except Exception:
            return {"json_valid": False}

    return task


def make_b1_qa_task(llm: Any) -> TaskFn:
    """B1 问答任务：内联作答指令（被测对象是模型本身的回答质量）。"""

    def task(*, item: Any, **kwargs: Any) -> dict:
        data, _ = _item_data(item)
        prompt = _B1_ANSWER_PROMPT.format(
            position=data.get("position") or "通用", question=data.get("question", "")
        )
        try:
            response = llm.invoke([prompt])
            return {"answer": str(response.content or "")}
        except Exception:
            return {"answer": ""}

    return task


# ---------- 打分器 ----------


def intent_evaluator(
    *, input: Any, output: Any, expected_output: Any, metadata: Any, **kwargs: Any
) -> list[dict]:
    expected = (expected_output or {}).get("intent")
    actual = (output or {}).get("intent")
    matched = actual is not None and actual == expected
    return [
        {
            "name": "intent_match",
            "value": 1.0 if matched else 0.0,
            "comment": "" if matched else f"期望 {expected}, 实际 {actual}",
        },
        {"name": "json_valid", "value": 1.0 if actual is not None else 0.0},
    ]


def judge_evaluator(
    *, input: Any, output: Any, expected_output: Any, metadata: Any, **kwargs: Any
) -> list[dict]:
    expected = (expected_output or {}).get("action")
    valid = bool((output or {}).get("json_valid"))
    action = (output or {}).get("action")
    matched = valid and action == expected
    return [
        {
            "name": "action_match",
            "value": 1.0 if matched else 0.0,
            "comment": "" if matched else f"期望 {expected}, 实际 {action}",
        },
        {"name": "json_valid", "value": 1.0 if valid else 0.0},
    ]


def make_b1_quality_evaluator(renderer: Renderer, judge_llm: Any) -> EvaluatorFn:
    """B1 LLM-as-judge：coverage（要点覆盖 0~5）+ relevance（相关性 0~2）。"""

    def evaluator(
        *, input: Any, output: Any, expected_output: Any, metadata: Any, **kwargs: Any
    ) -> list[dict]:
        points = (expected_output or {}).get("reference_points") or []
        answer = (output or {}).get("answer") or ""
        coverage, relevance, comment = 0, 0, "judge 解析失败"
        try:
            prompt = renderer(
                question=(input or {}).get("question", ""),
                reference_points=_numbered(points),
                answer=answer,
            )
            response = judge_llm.invoke([prompt])
            parsed = parse_json_text(str(response.content))
            if parsed is not None:
                coverage = _clamp(_as_int(parsed.get("coverage")), 0, 5)
                relevance = _clamp(_as_int(parsed.get("relevance")), 0, 2)
                comment = str(parsed.get("comment", ""))[:500]
        except Exception as exc:
            comment = f"judge 调用失败: {exc}"
        return [
            {"name": "coverage", "value": float(coverage), "comment": comment},
            {"name": "relevance", "value": float(relevance), "comment": comment},
        ]

    return evaluator


# ---------- 运行器 ----------


def run_experiment(
    client: LangfuseClientProtocol,
    *,
    spec: RunSpec,
    llm: Any,
    judge_llm: Any | None = None,
    registry: PromptRegistry | None = None,
) -> Any:
    """按 RunSpec 组装 task/evaluator 并执行 dataset.run_experiment。

    同名实验（experiment_name 缺省 = dataset 名）多次执行生成多个 run，
    云端可对比不同提示词版本（提示词替换实验）。
    """
    renderer: Renderer | None = None
    if spec.prompt_name:
        renderer = langfuse_renderer(client, spec.prompt_name, spec.prompt_version)
    elif spec.prompt_local and registry is not None:
        renderer = local_renderer(registry, spec.prompt_local, spec.prompt_local_version)

    if spec.task_name == TASK_INTENT:
        if renderer is None:
            raise ValueError("intent 任务需要 --prompt 或 --prompt-local")
        task = make_intent_task(renderer, llm)
        evaluators: list[EvaluatorFn] = [intent_evaluator]
    elif spec.task_name == TASK_JUDGE:
        if renderer is None:
            raise ValueError("judge 任务需要 --prompt 或 --prompt-local")
        task = make_judge_task(renderer, llm)
        evaluators = [judge_evaluator]
    else:  # TASK_B1_QA
        if registry is None:
            raise ValueError("b1_qa 任务需要 registry（b1_quality judge 模板）")
        task = make_b1_qa_task(llm)
        evaluators = [
            make_b1_quality_evaluator(local_renderer(registry, B1_QUALITY_PROMPT), judge_llm or llm)
        ]

    dataset = client.get_dataset(spec.dataset_name)
    prompt_ref = (
        f"v{spec.prompt_version}"
        if spec.prompt_name and spec.prompt_version is not None
        else spec.prompt_name or spec.prompt_local or "inline"
    )
    run_name = spec.run_name or f"{spec.task_name}-{prompt_ref}-{time.strftime('%Y%m%d%H%M%S')}"
    return dataset.run_experiment(
        name=spec.experiment_name or spec.dataset_name,
        run_name=run_name,
        description=spec.description,
        task=task,
        evaluators=evaluators,
        metadata=spec.metadata,
    )
