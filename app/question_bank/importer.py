"""题库导入器：校验 + upsert question_bank 表。

对应 specs/question_bank/schema.md §7 导入契约（AC7 幂等 / AC8 dry-run）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import Base, Question, build_engine
from app.question_bank.validator import validate_bank


class BankValidationError(Exception):
    """题库数据未通过校验，errors 为全部错误信息。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"题库校验失败，共 {len(errors)} 个错误")


@dataclass
class ImportReport:
    validated: int
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    dry_run: bool = False


def run_import(
    data_dir: Path,
    db_url: str | None = None,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> ImportReport:
    """校验并导入题库。

    校验失败抛 BankValidationError（不写库）；dry_run 只校验不写库；
    默认跳过已存在的 question_id，force=True 时覆盖更新全部记录。
    """
    records, errors = validate_bank(data_dir)
    if errors:
        raise BankValidationError(errors)
    if dry_run:
        return ImportReport(validated=len(records), dry_run=True)
    if not db_url:
        raise RuntimeError("未设置 DATABASE_URL 环境变量（dry-run 模式不需要）")
    engine = build_engine(db_url)
    Base.metadata.create_all(engine)
    report = ImportReport(validated=len(records))
    with Session(engine) as session:
        for record in records:
            existing = session.get(Question, record["question_id"])
            if existing is not None and not force:
                report.skipped += 1
                continue
            if existing is not None:
                report.updated += 1
            else:
                report.inserted += 1
            session.merge(Question(**record))
        session.commit()
    return report
