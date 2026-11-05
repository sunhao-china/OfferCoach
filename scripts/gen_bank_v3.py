"""题库扩充三期构建 CLI（薄封装，逻辑在 app.question_bank.generate_v3）。

用法:
  试跑（全岗位合计限 10 题，成本评估）:
    DEEPSEEK_API_KEY=... PYTHONIOENCODING=utf-8 \
      .venv/Scripts/python.exe scripts/gen_bank_v3.py --limit 10
  全量 6 岗位（每类 entry 12 / medium 20 / hard 8，重跑自动续跑零浪费）:
    .venv/Scripts/python.exe scripts/gen_bank_v3.py
  生成 + dry-run 校验 + 导入 dev DB:
    DATABASE_URL=sqlite:///data/offercoach_dev.db \
      .venv/Scripts/python.exe scripts/gen_bank_v3.py --import
  只跑指定岗位:
    .venv/Scripts/python.exe scripts/gen_bank_v3.py --position python_backend --position ai_agent
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.db import build_engine
from app.question_bank.generate_v3 import POSITION_PLANS, generate_v3
from app.question_bank.importer import run_import

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_BASE = PROJECT_ROOT / "data" / "question_bank"
TEMPLATES_DIR = PROJECT_ROOT / "app" / "prompts" / "templates"


def main() -> int:
    parser = argparse.ArgumentParser(description="题库扩充三期：多岗位批量 LLM 生成 + 导入")
    parser.add_argument(
        "--position",
        action="append",
        choices=sorted(POSITION_PLANS),
        default=None,
        help="只生成指定岗位（可重复；缺省全部 6 个）",
    )
    parser.add_argument("--limit", type=int, default=None, help="试跑上限：全岗位合计新增 N 题")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument(
        "--import", dest="do_import", action="store_true", help="dry-run 校验通过后真实导入"
    )
    parser.add_argument("--output-base", type=Path, default=OUTPUT_BASE)
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    engine = build_engine(db_url) if db_url else None

    report = generate_v3(
        args.output_base,
        TEMPLATES_DIR,
        engine=engine,
        positions=tuple(args.position) if args.position else None,
        limit=args.limit,
        batch_size=args.batch_size,
    )
    positions = args.position or sorted(POSITION_PLANS)
    print(f"新增: {report.total} 条 {dict(report.per_position)}")
    print(f"跳过(与库内/已写盘题面重复): {report.skipped_duplicate} | 失败 {len(report.failures)}")
    print(
        f"字符量: 输入 {report.input_chars} / 输出 {report.output_chars}"
        f"（粗略 token 估算 ~{report.input_chars + report.output_chars}）"
    )
    for failure in report.failures[:10]:
        print(f"  失败: {failure}")
    print(f"dry-run 校验: {report.validated} 条通过")
    if args.do_import:
        total = 0
        for position in positions:
            dir_path = args.output_base / f"v3_{position}"
            if not any(dir_path.glob("*.jsonl")):
                continue
            imp = run_import(dir_path, db_url)
            total += imp.inserted
            print(f"导入 {position}: 新增 {imp.inserted} / 更新 {imp.updated} / 跳过 {imp.skipped}")
        print(f"导入合计新增: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
