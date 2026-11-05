"""Langfuse 测试集构建：三个数据源读取 + 幂等推送（评估实验的输入资产）。

数据集契约（item input / expected_output 均为 dict，可直接喂给模板渲染）：
- oc-intent-v1：data/evals/intent.jsonl 标注集，input 为 router/intent 模板变量
  （user_message / history_summary），expected_output={intent}
- oc-judge-v1：data/evals/judge.jsonl 标注集，input 为 mock_interview/judge 模板
  全变量（reference_points / bank_followups / followups 为 list），expected_output={action}
- oc-b1-qa-v1：题库确定性抽样（全岗位 × 类别 × 难度按组配额，默认 ~40 条），
  input={question, position}，expected_output={reference_points}

幂等：item id = input+expected 规范化 JSON 的 md5 前 12 位；推送前拉取已有 id
集合，只补缺失（重跑零新增）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import Question
from app.evaluation.intent import load_cases

DATASET_INTENT = "oc-intent-v1"
DATASET_JUDGE = "oc-judge-v1"
DATASET_B1_QA = "oc-b1-qa-v1"

DEFAULT_B1_QA_TARGET = 40

_JUDGE_REQUIRED_FIELDS = {
    "id",
    "question",
    "reference_points",
    "bank_followups",
    "answer",
    "followups",
    "followup_count",
    "max_followups",
    "expected_action",
}


class LangfuseClientProtocol(Protocol):
    """langfuse.Langfuse 的最小接口（测试用 Fake 桩）。"""

    def get_dataset(self, name: str, **kwargs: Any) -> Any: ...

    def create_dataset(
        self, *, name: str, description: str | None = None, **kwargs: Any
    ) -> Any: ...

    def create_dataset_item(
        self,
        *,
        dataset_name: str,
        input: Any = None,
        expected_output: Any = None,
        metadata: Any = None,
        id: str | None = None,
        **kwargs: Any,
    ) -> Any: ...


@dataclass
class SyncReport:
    dataset_name: str
    created_dataset: bool = False
    total_items: int = 0
    pushed: int = 0
    skipped: int = 0


def item_id(item: dict) -> str:
    """item 的确定性 id：input+expected 规范化 JSON 的 md5 前 12 位。"""
    payload = json.dumps(
        {"input": item.get("input"), "expected_output": item.get("expected_output")},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]


def load_intent_items(path: str | Path) -> list[dict]:
    """intent.jsonl 标注集 → Langfuse item（复用 app.evaluation.intent 加载器）。"""
    return [
        {
            "input": {"user_message": case.message, "history_summary": "（无）"},
            "expected_output": {"intent": case.expected_intent},
            "metadata": {"source_id": case.id},
        }
        for case in load_cases(path)
    ]


def load_judge_items(path: str | Path) -> list[dict]:
    """judge.jsonl 标注集 → Langfuse item；字段缺失即抛错（标注质量红线）。"""
    items = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        data = json.loads(line)
        missing = _JUDGE_REQUIRED_FIELDS - set(data)
        if missing:
            raise ValueError(
                f"judge 标注集第 {lineno} 行字段缺失 {sorted(missing)}: {data.get('id', '?')}"
            )
        items.append(
            {
                "input": {
                    "question": data["question"],
                    "reference_points": data["reference_points"],
                    "bank_followups": data["bank_followups"],
                    "answer": data["answer"],
                    "followups": data["followups"],
                    "followup_count": data["followup_count"],
                    "max_followups": data["max_followups"],
                },
                "expected_output": {"action": data["expected_action"]},
                "metadata": {"source_id": data["id"]},
            }
        )
    return items


def load_b1_qa_items(engine: Engine, *, target: int = DEFAULT_B1_QA_TARGET) -> list[dict]:
    """从题库确定性抽样 B1 问答用例（无随机：结果完全由库内容决定，可重复）。

    配额：每岗位至少 1 条，剩余名额按岗位题量最大余数法分配；岗位内
    按 (category, difficulty) 组同法配额，组内按序跨步取样。
    """
    with Session(engine) as session:
        rows = list(
            session.scalars(
                select(Question).order_by(
                    Question.position,
                    Question.category,
                    Question.difficulty,
                    Question.question_id,
                )
            )
        )
    groups: dict[tuple[str, str, str], list[Question]] = {}
    for row in rows:
        groups.setdefault((row.position, row.category, row.difficulty), []).append(row)
    if not groups:
        return []
    positions: dict[str, list[tuple[str, str, str]]] = {}
    for key in groups:
        positions.setdefault(key[0], []).append(key)
    pos_sizes = {pos: sum(len(groups[k]) for k in keys) for pos, keys in positions.items()}
    total = sum(pos_sizes.values())
    pos_quotas = _allocate({pos: 1 for pos in positions}, target, pos_sizes, total)
    items: list[dict] = []
    for pos in sorted(positions):
        keys = sorted(positions[pos])
        quota = pos_quotas[pos]
        group_quotas = _allocate({k: 1 for k in keys}, quota, {k: len(groups[k]) for k in keys})
        for key in sorted(group_quotas):
            group = groups[key]
            n = min(group_quotas[key], len(group))
            for j in range(n):
                row = group[(j * len(group)) // n]
                items.append(
                    {
                        "input": {"question": row.question, "position": row.position},
                        "expected_output": {"reference_points": list(row.reference_points or [])},
                        "metadata": {
                            "question_id": row.question_id,
                            "category": row.category,
                            "difficulty": row.difficulty,
                        },
                    }
                )
    return items


def _allocate(
    baseline: dict[Any, int],
    total_budget: int,
    sizes: dict[Any, int],
    size_total: int | None = None,
) -> dict[Any, int]:
    """在 baseline（每人 1 份）之上按 sizes 最大余数法分配剩余名额。

    名额不足一人一份时按 keys 顺序截断；结果总和 = min(名额, 组数+剩余)。
    """
    keys = sorted(baseline)
    if len(keys) > total_budget:
        return {k: 1 for k in keys[:total_budget]}
    result = {k: 1 for k in keys}
    remaining = max(0, total_budget - len(keys))
    if remaining:
        size_total = size_total if size_total is not None else sum(sizes.values())
        quotas = {k: remaining * sizes[k] / size_total for k in keys}
        allocated = {k: int(quotas[k]) for k in keys}
        rest = remaining - sum(allocated.values())
        for k in sorted(keys, key=lambda k: (quotas[k] - allocated[k], sizes[k]), reverse=True)[
            :rest
        ]:
            allocated[k] += 1
        for k, extra in allocated.items():
            result[k] += extra
    return result


def sync_dataset(
    client: LangfuseClientProtocol,
    dataset_name: str,
    items: list[dict],
    *,
    description: str = "",
) -> SyncReport:
    """把 items 幂等推入数据集：dataset 不存在则创建；已有 id 跳过，只补缺失。"""
    report = SyncReport(dataset_name)
    try:
        dataset = client.get_dataset(dataset_name)
    except Exception:  # 数据集不存在（SDK NotFoundError 等）：新建后重取 DatasetClient
        client.create_dataset(name=dataset_name, description=description)
        report.created_dataset = True
        dataset = client.get_dataset(dataset_name)
    existing_ids = {item.id for item in dataset.items}
    for item in items:
        iid = item_id(item)
        if iid in existing_ids:
            report.skipped += 1
            continue
        client.create_dataset_item(
            dataset_name=dataset_name,
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item.get("metadata"),
            id=iid,
        )
        report.pushed += 1
    report.total_items = len(items)
    return report
