"""题库扩充三期生成编排：按岗位计划批量 LLM 生成 → 逐条写 JSONL → dry-run 校验。

与二期 normalizer 的区别：无候选素材，题目由 LLM 原创；question_id 为
q_{category}_{seq:03d} 顺序号（按文件续号）；难度按槽位契约 entry 12 / medium 20 / hard 8。

复用二期机制：cap 累计（含已写盘条数）、resume 零 LLM 调用、norm_text 跨文件去重、
非法输出记失败不重试（重跑自然补）、逐条 append + flush（崩溃安全）。
thin CLI 见 scripts/gen_bank_v3.py。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.llm_factory import TIER_CHEAP, build_chat_model
from app.db import Question
from app.prompts.registry import PromptRegistry
from app.question_bank.importer import BankValidationError, run_import
from app.question_bank.llm_json import parse_llm_json, validate_llm_output
from app.question_bank.validator import DIFFICULTIES, norm_text

PROMPT_GENERATE = "question_bank/generate"
SOURCE = "ai_seed_v3"
# 每个类别 × 难度槽位的目标题量（合计 40/类）；类型分布为提示词建议（非硬契约）
SLOT_DIFFICULTY_COUNTS: dict[str, int] = {"entry": 12, "medium": 20, "hard": 8}
# 槽位连续无产出批次数上限：防止 LLM 持续失败导致死循环
MAX_STALLED_BATCHES = 3
# 防重复提示中最多列出的已有题面条数
EXISTING_PREVIEW_LIMIT = 10

# 岗位计划：display 用于提示词口吻，categories 为落盘类别，type_mix 为每 40 题的建议类型分布
POSITION_PLANS: dict[str, dict[str, Any]] = {
    "python_backend": {
        "display": "Python 后端",
        "categories": (
            "python_basics",
            "python_concurrent",
            "python_web",
            "python_framework",
            "python_db",
        ),
        "type_mix": {"knowledge": 16, "scenario": 10, "coding": 10, "system_design": 4},
    },
    "c_backend": {
        "display": "C 后端",
        "categories": ("c_basics", "c_memory", "c_system", "c_algorithm"),
        "type_mix": {"knowledge": 12, "scenario": 10, "coding": 14, "system_design": 4},
    },
    "cpp_backend": {
        "display": "C++ 后端",
        "categories": ("cpp_basics", "cpp_stl", "cpp_concurrent", "cpp_modern"),
        "type_mix": {"knowledge": 12, "scenario": 10, "coding": 14, "system_design": 4},
    },
    "csharp_backend": {
        "display": "C# 后端",
        "categories": ("csharp_basics", "csharp_net", "csharp_ef", "csharp_web"),
        "type_mix": {"knowledge": 14, "scenario": 12, "coding": 10, "system_design": 4},
    },
    "ai_agent": {
        "display": "AI Agent",
        "categories": ("agent_core", "agent_llm", "agent_tooling", "agent_system"),
        "type_mix": {"knowledge": 12, "scenario": 16, "coding": 4, "system_design": 8},
    },
    "project_manager": {
        "display": "项目经理",
        "categories": ("pm_process", "pm_agile", "pm_risk", "pm_soft"),
        "type_mix": {"knowledge": 8, "scenario": 24, "coding": 0, "system_design": 8},
    },
}

_SEQ_ID_RE = re.compile(r"^q_(.+)_(\d{3})$")


@dataclass
class V3Report:
    per_position: dict[str, int] = field(default_factory=dict)
    skipped_duplicate: int = 0
    failures: list[str] = field(default_factory=list)
    input_chars: int = 0
    output_chars: int = 0
    validated: int = 0

    @property
    def total(self) -> int:
        return sum(self.per_position.values())


def _type_guidance(mix: dict[str, int], batch: int) -> str:
    """把每 40 题的类型建议按批次规模折算（最大余数法补足），供提示词参考。"""
    total = sum(mix.values())
    if total <= 0:
        return "不限定"
    targets: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    for qtype, count in mix.items():
        exact = count * batch / total
        base = int(exact)
        targets[qtype] = base
        remainders.append((exact - base, qtype))
    for _, qtype in sorted(remainders, reverse=True)[: batch - sum(targets.values())]:
        targets[qtype] += 1
    parts = [f"{qtype} {n} 题" for qtype, n in targets.items() if n]
    return "、".join(parts) or "不限定"


def _load_written(output_dir: Path, categories: tuple[str, ...]) -> dict[str, dict]:
    """读取输出目录已写盘记录：{category: {difficulty: 条数, next_seq, questions}}。"""
    state: dict[str, dict] = {}
    for category in categories:
        path = output_dir / f"{category}.jsonl"
        counts: dict[str, int] = {}
        next_seq = 1
        questions: list[str] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                difficulty = data.get("difficulty")
                if isinstance(difficulty, str):
                    counts[difficulty] = counts.get(difficulty, 0) + 1
                if data.get("question"):
                    questions.append(str(data["question"]))
                match = _SEQ_ID_RE.fullmatch(str(data.get("question_id", "")))
                if match and match.group(1) == category:
                    next_seq = max(next_seq, int(match.group(2)) + 1)
        state[category] = {"counts": counts, "next_seq": next_seq, "questions": questions}
    return state


def generate_v3(
    output_base: Path,
    templates_dir: Path,
    *,
    engine: Engine | None = None,
    positions: tuple[str, ...] | None = None,
    categories: tuple[str, ...] | None = None,
    limit: int | None = None,
    batch_size: int = 5,
    difficulty_counts: dict[str, int] | None = None,
    llm: Any = None,
) -> V3Report:
    """按岗位计划批量生成题库并写 JSONL；返回统计报告（含 dry-run 校验通过数）。

    positions 缺省为全部岗位计划；categories 缺省为岗位计划的全部类别（试跑可只跑单类）；
    llm 注入便于测试（缺省 cheap 档真实模型）。
    """
    report = V3Report()
    selected = positions or tuple(POSITION_PLANS)
    counts = difficulty_counts or SLOT_DIFFICULTY_COUNTS
    registry = PromptRegistry(templates_dir)
    model = llm if llm is not None else build_chat_model(TIER_CHEAP)
    existing_questions: set[str] = set()
    if engine is not None:
        with Session(engine) as session:
            rows = session.execute(select(Question.question)).all()
        existing_questions = {norm_text(q) for (q,) in rows}
    for position in selected:
        if limit is not None and report.total >= limit:
            break
        plan = POSITION_PLANS[position]
        plan_categories = tuple(categories) if categories is not None else plan["categories"]
        output_dir = output_base / f"v3_{position}"
        output_dir.mkdir(parents=True, exist_ok=True)
        state = _load_written(output_dir, plan_categories)
        for category in plan_categories:
            existing_questions |= {norm_text(q) for q in state[category]["questions"]}
        for category in plan_categories:
            if limit is not None and report.total >= limit:
                break
            cat_state = state[category]
            seq = cat_state["next_seq"]
            file = (output_dir / f"{category}.jsonl").open("a", encoding="utf-8")
            try:
                for difficulty in DIFFICULTIES:
                    if limit is not None and report.total >= limit:
                        break
                    need = counts.get(difficulty, 0) - cat_state["counts"].get(difficulty, 0)
                    stalled = 0
                    while (
                        need > 0
                        and stalled < MAX_STALLED_BATCHES
                        and (limit is None or report.total < limit)
                    ):
                        batch = min(batch_size, need)
                        existing = (
                            "\n".join(
                                f"{i}. {q}"
                                for i, q in enumerate(
                                    cat_state["questions"][-EXISTING_PREVIEW_LIMIT:], start=1
                                )
                            )
                            or "（无）"
                        )
                        prompt = registry.render(
                            PROMPT_GENERATE,
                            position=str(plan["display"]),
                            category=category,
                            difficulty=difficulty,
                            count=str(batch),
                            type_guidance=_type_guidance(plan["type_mix"], batch),
                            existing=existing,
                        )
                        report.input_chars += len(prompt)
                        content = model.invoke(prompt).content
                        content_text = content if isinstance(content, str) else str(content)
                        report.output_chars += len(content_text)
                        data = parse_llm_json(content_text)
                        items = (
                            data.get("questions")
                            if isinstance(data, dict) and isinstance(data.get("questions"), list)
                            else None
                        )
                        if items is None:
                            report.failures.append(
                                f"{position}/{category}/{difficulty}: LLM 输出缺少 questions 数组"
                            )
                            stalled += 1
                            continue
                        items = items[:batch]  # 超产截断，守住难度槽位契约
                        written = 0
                        for item in items:
                            errors = validate_llm_output(item)
                            if errors:
                                report.failures.append(
                                    f"{position}/{category}/{difficulty}: {'; '.join(errors)}"
                                )
                                continue
                            if item["difficulty"] != difficulty:
                                report.failures.append(
                                    f"{position}/{category}/{difficulty}: "
                                    f"难度不符（要求 {difficulty}，输出 {item['difficulty']}）"
                                )
                                continue
                            if norm_text(item["question"]) in existing_questions:
                                report.skipped_duplicate += 1
                                continue
                            record = {
                                "question_id": f"q_{category}_{seq:03d}",
                                "position": position,
                                "category": category,
                                "difficulty": difficulty,
                                "type": item["type"],
                                "question": item["question"],
                                "reference_points": item["reference_points"],
                                "followups": item["followups"],
                                "keywords": item["keywords"],
                                "source": SOURCE,
                            }
                            file.write(json.dumps(record, ensure_ascii=False) + "\n")
                            file.flush()
                            seq += 1
                            written += 1
                            need -= 1
                            existing_questions.add(norm_text(item["question"]))
                            cat_state["questions"].append(item["question"])
                            report.per_position[position] = report.per_position.get(position, 0) + 1
                        stalled = 0 if written else stalled + 1
                    if need > 0 and stalled >= MAX_STALLED_BATCHES:
                        report.failures.append(
                            f"{position}/{category}/{difficulty}: "
                            f"连续 {MAX_STALLED_BATCHES} 批无产出，该槽位缺 {need} 题（重跑可补）"
                        )
            finally:
                file.close()
        try:
            validated = run_import(output_dir, None, dry_run=True)
            report.validated += validated.validated
        except BankValidationError as exc:  # 生成数据不合法时保留现场供排查
            report.failures.append(f"{position}: dry-run 校验失败: {'; '.join(exc.errors[:5])}")
    return report
