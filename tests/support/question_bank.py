"""题库种子数据生成器：按 seed_v1 契约构造合法/变体数据供测试。"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import Base, Question, build_engine
from app.question_bank.store import QuestionStore
from app.question_bank.validator import DISTRIBUTION

# 每个 category 的难度计划：entry 5 / medium 12 / hard 8，按题号顺序分配
DIFFICULTY_PLAN = ["entry"] * 5 + ["medium"] * 12 + ["hard"] * 8


def make_record(
    category: str,
    seq: int,
    *,
    difficulty: str = "medium",
    qtype: str = "knowledge",
    **overrides,
) -> dict:
    """构造一条结构合法的题库记录；overrides 覆盖任意字段。"""
    record = {
        "question_id": f"q_{category}_{seq:03d}",
        "position": "java_backend",
        "category": category,
        "difficulty": difficulty,
        "type": qtype,
        "question": f"测试题目 {category} {seq}",
        "reference_points": ["要点一", "要点二", "要点三"],
        "followups": ["追问一", "追问二"],
        "keywords": ["kw1", "kw2", "kw3"],
        "source": "ai_seed_v1",
    }
    record.update(overrides)
    return record


# store / 工具类测试共用的样例记录（题面含可检索关键词）
STORE_SAMPLE_RECORDS = [
    make_record(
        "concurrent",
        1,
        difficulty="entry",
        qtype="knowledge",
        question="谈谈线程池的核心参数与拒绝策略",
    ),
    make_record(
        "concurrent", 2, difficulty="medium", qtype="coding", question="手写一个简单的阻塞队列"
    ),
    make_record(
        "concurrent",
        3,
        difficulty="hard",
        qtype="scenario",
        question="线上线程池打满导致接口超时，如何排查",
    ),
    make_record("jvm", 1, difficulty="entry", qtype="knowledge", question="JVM 运行时数据区有哪些"),
    make_record(
        "mysql", 1, difficulty="entry", qtype="knowledge", question="MySQL 索引失效的常见场景"
    ),
    make_record(
        "mysql", 2, difficulty="hard", qtype="system_design", question="分库分表后如何进行跨库查询"
    ),
]


def build_seed_bank(data_dir: Path) -> list[dict]:
    """按 seed_v1 契约生成 200 条合法记录并写入 {category}.jsonl 文件。

    只写有分布契约的 seed 类别（DISTRIBUTION 键）；三期新增类别无契约不写。
    """
    records: list[dict] = []
    for category in DISTRIBUTION:
        category_records: list[dict] = []
        seq = 1
        for qtype, count in DISTRIBUTION[category].items():
            for _ in range(count):
                category_records.append(
                    make_record(category, seq, difficulty=DIFFICULTY_PLAN[seq - 1], qtype=qtype)
                )
                seq += 1
        write_bank_category(data_dir, category, category_records)
        records.extend(category_records)
    return records


def write_bank_category(data_dir: Path, category: str, records: list[dict]) -> None:
    """把一个 category 的记录写入 {category}.jsonl。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    content = "\n".join(lines) + "\n" if lines else ""
    (data_dir / f"{category}.jsonl").write_text(content, encoding="utf-8")


def write_bank(data_dir: Path, records: list[dict]) -> None:
    """把全部记录按 category 分组重写（用于制造变体后回写）。"""
    categories = sorted({r.get("category") for r in records if r.get("category")})
    for category in categories:
        write_bank_category(
            data_dir, category, [r for r in records if r.get("category") == category]
        )


def make_store(tmp_path: Path, records: list[dict]) -> QuestionStore:
    """把记录写入 sqlite 文件库并返回查询层（store / 工具 / ReAct 测试共用）。"""
    engine = build_engine(f"sqlite:///{(tmp_path / 'store.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for record in records:
            session.merge(Question(**record))
        session.commit()
    return QuestionStore(engine)
