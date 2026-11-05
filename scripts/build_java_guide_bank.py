"""题库扩充二期构建 CLI（薄封装，逻辑在 app.question_bank.build_bank）。

用法:
  先抓仓库:  python scripts/fetch_java_guide.py
  试跑 20 题:
    DATABASE_URL=sqlite:///data/offercoach_dev.db python scripts/build_java_guide_bank.py --limit 20
  全量（每类上限 40，dry-run 校验通过后导入）:
    DATABASE_URL=sqlite:///data/offercoach_dev.db python scripts/build_java_guide_bank.py --import
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from app.db import build_engine
from app.question_bank.build_bank import build_bank

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = PROJECT_ROOT / "data" / "question_bank" / "vendor" / "java_guide"
OUTPUT_DIR = PROJECT_ROOT / "data" / "question_bank" / "java_guide_v1"
TEMPLATES_DIR = PROJECT_ROOT / "app" / "prompts" / "templates"


def main() -> int:
    parser = argparse.ArgumentParser(description="JavaGuide 题库构建：提取 + LLM 规范化 + 导入")
    parser.add_argument("--cap", type=int, default=40, help="每类新增上限（默认 40）")
    parser.add_argument("--limit", type=int, default=None, help="试跑前 N 题（成本评估用）")
    parser.add_argument(
        "--import", dest="do_import", action="store_true", help="dry-run 校验通过后真实导入"
    )
    parser.add_argument("--vendor-dir", type=Path, default=VENDOR_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    if not (args.vendor_dir / "MANIFEST.json").exists():
        print("vendor 目录缺失：请先运行 scripts/fetch_java_guide.py", file=sys.stderr)
        return 1
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("未设置 DATABASE_URL（dev 用 sqlite:///data/offercoach_dev.db）", file=sys.stderr)
        return 1

    report = build_bank(
        args.vendor_dir,
        args.output_dir,
        build_engine(db_url),
        TEMPLATES_DIR,
        cap=args.cap,
        limit=args.limit,
        do_import=args.do_import,
    )
    normalized = report.normalized
    assert normalized is not None
    print(f"提取候选: {report.candidates} 条")
    print(
        f"新增: {normalized.total} 条 {dict(normalized.per_category)} | "
        f"跳过(已存在 {normalized.skipped_existing} / 与 seed 重复 {normalized.skipped_duplicate} "
        f"/ 超 cap {normalized.skipped_cap}) | 失败 {len(normalized.failures)}"
    )
    print(
        f"字符量: 输入 {normalized.input_chars} / 输出 {normalized.output_chars}"
        f"（粗略 token 估算 ~{(normalized.input_chars + normalized.output_chars) / 1.0:.0f}）"
    )
    for failure in normalized.failures[:10]:
        print(f"  失败: {failure}")
    print(f"dry-run 校验: {report.validated} 条通过")
    if report.imported is not None:
        imp = report.imported
        print(f"导入: 新增 {imp.inserted} / 更新 {imp.updated} / 跳过 {imp.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
