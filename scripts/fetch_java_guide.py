"""JavaGuide 抓取 CLI（薄封装，逻辑在 app.question_bank.fetcher）。

用法:
  python scripts/fetch_java_guide.py
重取: 手动删除 data/question_bank/vendor/java_guide 后再运行。
"""

from __future__ import annotations

from pathlib import Path

from app.question_bank.fetcher import fetch_java_guide

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENDOR_DIR = PROJECT_ROOT / "data" / "question_bank" / "vendor" / "java_guide"


def main() -> int:
    report = fetch_java_guide(DEFAULT_VENDOR_DIR)
    print(f"已抓取 JavaGuide @ {report.url} commit={report.commit}")
    print(f"目录: {report.vendor_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
