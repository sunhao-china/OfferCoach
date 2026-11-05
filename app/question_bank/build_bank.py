"""二期题库构建编排：extract → LLM 规范化 → 校验（dry-run）→ 导入。

依据 specs/02-in-progress/question_bank/java-guide-import.md 模块五契约。
thin CLI 见 scripts/build_java_guide_bank.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.llm_factory import TIER_CHEAP, build_chat_model
from app.db import Question
from app.prompts.registry import PromptRegistry
from app.question_bank.importer import ImportReport, run_import
from app.question_bank.md_extract import extract_candidates
from app.question_bank.normalize import NormalizeReport, normalize_candidates
from app.question_bank.validator import norm_text


@dataclass
class BuildReport:
    candidates: int = 0
    normalized: NormalizeReport | None = None
    validated: int = 0
    imported: ImportReport | None = None


def _load_existing(engine: Engine) -> tuple[set[str], set[str]]:
    """库内现有题目：ids（resume 跳过）+ 归一化题面（跨源去重）。"""
    with Session(engine) as session:
        rows = session.execute(select(Question.question_id, Question.question)).all()
    return {qid for qid, _ in rows}, {norm_text(q) for _, q in rows}


def build_bank(
    vendor_dir: Path,
    output_dir: Path,
    engine: Engine,
    templates_dir: Path,
    *,
    cap: int = 40,
    limit: int | None = None,
    do_import: bool = False,
    llm: object | None = None,
) -> BuildReport:
    """执行二期构建全流程；llm 注入便于测试（缺省 cheap 档真实模型）。"""
    candidates = extract_candidates(vendor_dir)
    report = BuildReport(candidates=len(candidates))
    existing_ids, existing_questions = _load_existing(engine)
    registry = PromptRegistry(templates_dir)
    model = llm if llm is not None else build_chat_model(TIER_CHEAP)
    normalized = normalize_candidates(
        model,
        candidates,
        existing_ids=existing_ids,
        existing_questions=existing_questions,
        cap=cap,
        limit=limit,
        output_dir=output_dir,
        render=lambda c: registry.render(
            "question_bank/normalize", question=c.question, answer=c.answer
        ),
    )
    report.normalized = normalized
    validated = run_import(output_dir, None, dry_run=True)
    report.validated = validated.validated
    if do_import:
        report.imported = run_import(output_dir, engine.url.render_as_string(hide_password=False))
    return report
