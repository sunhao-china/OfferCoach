"""意图识别评估（L4）：标注集 → 准确率 + 逐例错误。

评估器与真实模型解耦：run_eval 接受任意 LLM（含桩），因此评估器本身可被单元测试覆盖；
真实模型跑批在 scripts/eval_intent.py 中接线（Phase 2 模型工厂）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.prompts.registry import PromptRegistry
from app.router.dispatcher import RouteDispatcher


class EvalCase(BaseModel):
    id: str
    message: str
    expected_intent: str


class EvalFailure(BaseModel):
    id: str
    message: str
    expected: str
    actual: str


class EvalReport(BaseModel):
    total: int
    correct: int
    failures: list[EvalFailure]

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def load_cases(path: str | Path) -> list[EvalCase]:
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(EvalCase.model_validate(json.loads(line)))
    return cases


def run_eval(llm: Any, registry: PromptRegistry, cases: list[EvalCase]) -> EvalReport:
    dispatcher = RouteDispatcher(llm, registry)
    failures: list[EvalFailure] = []
    correct = 0
    for case in cases:
        decision = dispatcher.route(case.message)
        if decision.intent.value == case.expected_intent:
            correct += 1
        else:
            failures.append(
                EvalFailure(
                    id=case.id,
                    message=case.message,
                    expected=case.expected_intent,
                    actual=decision.intent.value,
                )
            )
    return EvalReport(total=len(cases), correct=correct, failures=failures)
