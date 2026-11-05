"""L1: 二期构建闭环 — extract→normalize→dry-run 校验→导入，重跑幂等。

依据 specs/02-in-progress/question_bank/java-guide-import.md 的 AC5。
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, Question, build_engine
from app.question_bank.build_bank import build_bank
from tests.conftest import TEMPLATES_DIR, StubLLM
from tests.support.question_bank import make_record

GUIDE_BODY_A = (
    "线程池通过复用线程减少创建销毁开销，核心参数包括核心线程数、最大线程数、"
    "队列容量与拒绝策略，任务提交后先入队，队列满则扩容到最大线程数，"
    "再满则触发拒绝策略，常见策略有丢弃、抛异常、调用者执行等。"
    "理解这些参数的配合关系是线程池面试题的高频考点。"
)
GUIDE_BODY_B = (
    "进程与线程的主要区别在于资源隔离：进程拥有独立的内存空间，线程共享进程"
    "的堆和方法区，线程切换成本低、通信更直接，而进程隔离性更强，"
    "一个进程崩溃不会直接拖垮其他进程，代价是通信与创建开销更大。"
    "面试中常要求结合内存模型和上下文切换开销展开说明。"
)


def _make_vendor(tmp_path: Path) -> Path:
    vendor = tmp_path / "vendor"
    (vendor / "docs/java/concurrent").mkdir(parents=True, exist_ok=True)
    (vendor / "docs/java/concurrent/c01.md").write_text(
        f"### 什么是线程池？\n{GUIDE_BODY_A}", encoding="utf-8"
    )
    (vendor / "docs/java/concurrent/c02.md").write_text(
        f"### 进程和线程的区别是什么？\n{GUIDE_BODY_B}", encoding="utf-8"
    )
    return vendor


def _engine(tmp_path: Path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _db_count(engine) -> int:
    with Session(engine) as session:
        return session.execute(select(func.count()).select_from(Question)).scalar_one()


def _llm_json(question: str) -> str:
    return json.dumps(
        {
            "question": question,
            "reference_points": ["核心概念", "关键参数", "适用场景"],
            "followups": ["为什么这样设计", "实际案例"],
            "difficulty": "medium",
            "type": "knowledge",
            "keywords": ["线程池", "并发", "核心参数"],
        },
        ensure_ascii=False,
    )


def test_build_imports_and_rerun_is_idempotent(tmp_path):
    engine = _engine(tmp_path)
    output_dir = tmp_path / "java_guide_v1"
    llm = StubLLM([_llm_json("什么是线程池？"), _llm_json("进程和线程的区别是什么？")])
    report = build_bank(
        _make_vendor(tmp_path),
        output_dir,
        engine,
        TEMPLATES_DIR,
        cap=40,
        do_import=True,
        llm=llm,
    )
    assert report.candidates == 2
    assert report.normalized is not None and report.normalized.total == 2
    assert report.normalized.failures == []
    assert report.validated == 2
    assert report.imported is not None and report.imported.inserted == 2
    assert _db_count(engine) == 2
    with Session(engine) as session:
        rows = session.execute(select(Question)).scalars().all()
    assert {r.source for r in rows} == {"java_guide"}
    assert {r.category for r in rows} == {"concurrent"}
    assert all(r.position == "java_backend" for r in rows)

    # 重跑：全部已入库 → 零 LLM 调用、不新增
    report2 = build_bank(
        _make_vendor(tmp_path),
        output_dir,
        engine,
        TEMPLATES_DIR,
        cap=40,
        do_import=True,
        llm=StubLLM([]),
    )
    assert report2.normalized is not None
    assert report2.normalized.total == 0
    assert report2.normalized.skipped_existing == 2
    assert report2.validated == 2
    assert report2.imported is not None and report2.imported.inserted == 0
    assert _db_count(engine) == 2


def test_build_without_import_keeps_db_untouched(tmp_path):
    engine = _engine(tmp_path)
    llm = StubLLM([_llm_json("什么是线程池？"), _llm_json("进程和线程的区别是什么？")])
    report = build_bank(
        _make_vendor(tmp_path),
        tmp_path / "java_guide_v1",
        engine,
        TEMPLATES_DIR,
        cap=40,
        do_import=False,
        llm=llm,
    )
    assert report.imported is None
    assert report.validated == 2
    assert _db_count(engine) == 0


def test_build_skips_candidates_duplicating_seed_bank(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.merge(Question(**make_record("concurrent", 1, question="什么是线程池？")))
        session.commit()
    llm = StubLLM([_llm_json("进程和线程的区别是什么？")])
    report = build_bank(
        _make_vendor(tmp_path),
        tmp_path / "java_guide_v1",
        engine,
        TEMPLATES_DIR,
        cap=40,
        do_import=True,
        llm=llm,
    )
    assert report.normalized is not None
    assert report.normalized.total == 1
    assert report.normalized.skipped_duplicate == 1
    assert len(llm.calls) == 1  # 重复题在 LLM 之前拦截
    assert _db_count(engine) == 2  # seed 1 + guide 1
