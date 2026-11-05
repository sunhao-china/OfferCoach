"""L1: 题库导入器 — dry-run / 幂等 / force 覆盖 / 校验失败整体拒绝。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db import Question, build_engine
from app.question_bank.importer import BankValidationError, run_import
from tests.support.question_bank import build_seed_bank, write_bank


def _db_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'offercoach_test.db').as_posix()}"


def test_dry_run_validates_without_db(tmp_path):
    data_dir = tmp_path / "seed_v1"
    build_seed_bank(data_dir)
    report = run_import(data_dir, db_url=None, dry_run=True)
    assert report.validated == 200
    assert report.dry_run


def test_import_is_idempotent(tmp_path):
    data_dir = tmp_path / "seed_v1"
    build_seed_bank(data_dir)
    first = run_import(data_dir, db_url=_db_url(tmp_path))
    assert first.inserted == 200
    assert first.skipped == 0
    second = run_import(data_dir, db_url=_db_url(tmp_path))
    assert second.inserted == 0
    assert second.skipped == 200


def test_force_reimports_all_and_updates_changed_question(tmp_path):
    data_dir = tmp_path / "seed_v1"
    records = build_seed_bank(data_dir)
    db_url = _db_url(tmp_path)
    run_import(data_dir, db_url=db_url)
    records[0]["question"] = "修改后的题目文本"
    write_bank(data_dir, records)
    report = run_import(data_dir, db_url=db_url, force=True)
    assert report.updated == 200
    assert report.skipped == 0
    with Session(build_engine(db_url)) as session:
        row = session.get(Question, records[0]["question_id"])
        assert row is not None
        assert row.question == "修改后的题目文本"


def test_invalid_bank_raises_with_all_errors(tmp_path):
    data_dir = tmp_path / "seed_v1"
    records = build_seed_bank(data_dir)
    records[0]["difficulty"] = "超高"
    write_bank(data_dir, records)
    with pytest.raises(BankValidationError) as exc_info:
        run_import(data_dir, db_url=_db_url(tmp_path))
    assert any("difficulty 非法" in e for e in exc_info.value.errors)


def test_missing_db_url_raises(tmp_path):
    data_dir = tmp_path / "seed_v1"
    build_seed_bank(data_dir)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        run_import(data_dir, db_url=None)
