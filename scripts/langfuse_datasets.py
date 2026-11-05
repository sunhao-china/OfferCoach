"""Langfuse 测试集构建 CLI（薄封装，逻辑在 app.observability.langfuse_datasets）。

用法（需 LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY / LANGFUSE_BASE_URL 环境变量）:
  三个全建（intent + judge + b1_qa）:
    .venv/Scripts/python.exe scripts/langfuse_datasets.py --all
  只建一个:
    .venv/Scripts/python.exe scripts/langfuse_datasets.py --intent
  指定题库（b1_qa 抽样来源，缺省 DATABASE_URL 或 dev sqlite）:
    DATABASE_URL=sqlite:///data/offercoach_dev.db \
      .venv/Scripts/python.exe scripts/langfuse_datasets.py --b1-qa
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALS_DIR = PROJECT_ROOT / "data" / "evals"
DEV_DB_URL = f"sqlite:///{(PROJECT_ROOT / 'data' / 'offercoach_dev.db').as_posix()}"

DATASET_DESCRIPTIONS = {
    "oc-intent-v1": "意图识别标注集（data/evals/intent.jsonl）：user_message → intent",
    "oc-judge-v1": "面试官 judge 判定标注集（data/evals/judge.jsonl）：题目+要点+回答 → action",
    "oc-b1-qa-v1": "B1 知识问答题库抽样：question+position → reference_points",
}


def _require_env() -> None:
    missing = [k for k in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f"缺少环境变量: {', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建/补齐 Langfuse 评估测试集（幂等）")
    parser.add_argument("--all", action="store_true", help="构建全部三个数据集")
    parser.add_argument("--intent", action="store_true", help="oc-intent-v1")
    parser.add_argument("--judge", action="store_true", help="oc-judge-v1")
    parser.add_argument("--b1-qa", action="store_true", help="oc-b1-qa-v1")
    parser.add_argument("--b1-target", type=int, default=40, help="b1_qa 抽样条数")
    args = parser.parse_args()
    if not (args.all or args.intent or args.judge or args.b1_qa):
        sys.exit("请指定 --all / --intent / --judge / --b1-qa")
    _require_env()

    from langfuse import Langfuse

    from app.db import build_engine
    from app.observability.langfuse_datasets import (
        DATASET_B1_QA,
        DATASET_INTENT,
        DATASET_JUDGE,
        load_b1_qa_items,
        load_intent_items,
        load_judge_items,
        sync_dataset,
    )

    client = Langfuse()
    jobs: dict[str, list[dict]] = {}
    if args.all or args.intent:
        jobs[DATASET_INTENT] = load_intent_items(EVALS_DIR / "intent.jsonl")
    if args.all or args.judge:
        jobs[DATASET_JUDGE] = load_judge_items(EVALS_DIR / "judge.jsonl")
    if args.all or args.b1_qa:
        db_url = os.environ.get("DATABASE_URL") or DEV_DB_URL
        jobs[DATASET_B1_QA] = load_b1_qa_items(build_engine(db_url), target=args.b1_target)

    for name, items in jobs.items():
        report = sync_dataset(client, name, items, description=DATASET_DESCRIPTIONS[name])
        created = "（新建数据集）" if report.created_dataset else ""
        print(
            f"{name}{created}: 共 {report.total_items} 条，"
            f"推送 {report.pushed} / 跳过 {report.skipped}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
