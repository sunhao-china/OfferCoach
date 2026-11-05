"""题库规范化：md 问答候选 → LLM（cheap 档）→ 结构化记录，逐条写 JSONL。

依据 specs/02-in-progress/question_bank/java-guide-import.md 模块三契约：
- question_id 由 category + 题面归一化哈希确定性派生（q_{category}_g{hash8}），重跑幂等；
- resume：id 已入库或已写入 JSONL → 跳过（零 LLM 调用）；
- 去重：题面归一化命中现有题库（seed 等）→ 跳过；
- cap 每类上限（累计：含已写盘条数）：选够即停，重跑也不超发；
- 输出形状不合法 → 记入失败列表，不写文件不重试；逐条 append + flush（崩溃安全）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.question_bank.llm_json import parse_llm_json, validate_llm_output
from app.question_bank.md_extract import RawQuestion
from app.question_bank.validator import CATEGORIES, norm_text


@dataclass
class NormalizeReport:
    per_category: dict[str, int] = field(default_factory=dict)
    skipped_existing: int = 0
    skipped_duplicate: int = 0
    skipped_cap: int = 0
    failures: list[str] = field(default_factory=list)
    input_chars: int = 0
    output_chars: int = 0

    @property
    def total(self) -> int:
        return sum(self.per_category.values())


def make_question_id(category: str, question: str) -> str:
    digest = hashlib.md5(norm_text(f"{category}|{question}").encode("utf-8")).hexdigest()[:8]
    return f"q_{category}_g{digest}"


def _load_written(output_dir: Path) -> tuple[set[str], dict[str, int]]:
    """读取输出目录 JSONL 已写盘记录：ids（resume 跳过）+ 每类条数（cap 累计基准）。"""
    written: set[str] = set()
    counts: dict[str, int] = {}
    for category in CATEGORIES:
        path = output_dir / f"{category}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("question_id"):
                written.add(data["question_id"])
                counts[category] = counts.get(category, 0) + 1
    return written, counts


def normalize_candidates(
    llm: Any,
    candidates: list[RawQuestion],
    *,
    existing_ids: set[str],
    existing_questions: set[str],
    cap: int,
    output_dir: Path,
    render: Callable[[RawQuestion], str],
    limit: int | None = None,
) -> NormalizeReport:
    """把候选逐条规范化写 JSONL；返回条数/失败/字符统计报告。"""
    report = NormalizeReport()
    output_dir.mkdir(parents=True, exist_ok=True)
    written_ids, written_counts = _load_written(output_dir)
    files = {
        category: (output_dir / f"{category}.jsonl").open("a", encoding="utf-8")
        for category in CATEGORIES
    }
    # cap 累计：已写盘的条数占用预算，重跑不会超发
    budget = {category: max(0, cap - written_counts.get(category, 0)) for category in CATEGORIES}
    try:
        for candidate in candidates:
            if limit is not None and report.total >= limit:
                break
            category = candidate.category
            qid = make_question_id(category, candidate.question)
            if qid in existing_ids or qid in written_ids:
                report.skipped_existing += 1
                continue
            if norm_text(candidate.question) in existing_questions:
                report.skipped_duplicate += 1
                continue
            if budget[category] <= 0:
                report.skipped_cap += 1
                continue
            prompt = render(candidate)
            report.input_chars += len(prompt)
            content = llm.invoke(prompt).content
            content_text = content if isinstance(content, str) else str(content)
            report.output_chars += len(content_text)
            data = parse_llm_json(content_text)
            errors = validate_llm_output(data)
            if errors:
                report.failures.append(f"{qid}: {'; '.join(errors)}")
                continue
            record = {
                "question_id": qid,
                "position": "java_backend",
                "category": category,
                "difficulty": data["difficulty"],
                "type": data["type"],
                "question": data["question"],
                "reference_points": data["reference_points"],
                "followups": data["followups"],
                "keywords": data["keywords"],
                "source": "java_guide",
            }
            files[category].write(json.dumps(record, ensure_ascii=False) + "\n")
            files[category].flush()
            written_ids.add(qid)
            budget[category] -= 1
            report.per_category[category] = report.per_category.get(category, 0) + 1
    finally:
        for f in files.values():
            f.close()
    return report
