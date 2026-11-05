"""题库导入 CLI（薄封装，逻辑在 app.question_bank.importer）。

用法:
  python scripts/import_questions.py --dry-run
  python scripts/import_questions.py --force
数据目录默认 data/question_bank/seed_v1，连接串取环境变量 DATABASE_URL。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.question_bank.importer import BankValidationError, run_import  # noqa: E402

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "question_bank" / "seed_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="校验并导入题库到 MySQL question_bank 表")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="题库数据目录")
    parser.add_argument("--force", action="store_true", help="已存在的题按新数据覆盖更新；默认跳过")
    parser.add_argument("--dry-run", action="store_true", help="只校验不写库")
    args = parser.parse_args()
    try:
        report = run_import(
            args.data_dir,
            os.environ.get("DATABASE_URL"),
            force=args.force,
            dry_run=args.dry_run,
        )
    except BankValidationError as exc:
        for error in exc.errors:
            print(f"[错误] {error}", file=sys.stderr)
        print(f"校验失败：共 {len(exc.errors)} 个错误，未写入任何数据", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1
    if report.dry_run:
        print(f"dry-run 校验通过：{report.validated} 道题全部合法，未写库")
    else:
        print(
            f"导入完成：校验 {report.validated} 道，新增 {report.inserted}，"
            f"更新 {report.updated}，跳过 {report.skipped}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
