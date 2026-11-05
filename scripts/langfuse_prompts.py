"""Langfuse 提示词管理 CLI（薄封装，逻辑在 app.observability.langfuse_prompts）。

用法（需 LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY / LANGFUSE_BASE_URL 环境变量）:
  .venv/Scripts/python.exe scripts/langfuse_prompts.py --list   # 本地模板清单
  .venv/Scripts/python.exe scripts/langfuse_prompts.py --diff   # 云端对比（missing/same/changed）
  .venv/Scripts/python.exe scripts/langfuse_prompts.py --push   # 全量推送（幂等，无变更零写入）
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "prompts" / "templates"


def _require_env() -> None:
    missing = [k for k in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f"缺少环境变量: {', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 YAML 提示词 ↔ Langfuse Prompt Management")
    parser.add_argument("--list", action="store_true", help="列出本地全部模板与版本")
    parser.add_argument("--diff", action="store_true", help="对比云端状态，不写入")
    parser.add_argument("--push", action="store_true", help="全量推送（幂等）")
    args = parser.parse_args()
    if not (args.list or args.diff or args.push):
        sys.exit("请指定 --list / --diff / --push 之一")

    from app.observability.langfuse_prompts import (
        PROMPT_PREFIX,
        diff_prompts,
        push_all_prompts,
    )
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(TEMPLATES_DIR)
    if args.list:
        for name in registry.names():
            print(f"{name}: {registry.versions(name)}")
        return 0

    _require_env()
    from langfuse import Langfuse

    client = Langfuse()
    if args.diff:
        for d in diff_prompts(client, registry):
            print(f"[{d.state:7s}] {PROMPT_PREFIX}/{d.name} sv{d.source_version}")
        return 0

    report = push_all_prompts(client, registry, templates_dir=TEMPLATES_DIR)
    for r in report.results:
        remote = f" → Langfuse v{r.langfuse_version}" if r.langfuse_version else ""
        print(f"[{r.action:7s}] {PROMPT_PREFIX}/{r.name} sv{r.source_version}{remote}")
    print(f"合计: 新建 {report.created} / 跳过 {report.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
