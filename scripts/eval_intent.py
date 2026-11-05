"""意图识别评估入口（L4，需要真实模型 API Key）：
python scripts/eval_intent.py --data data/evals/intent.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="意图识别评估（标注集 → 准确率）")
    parser.add_argument("--data", default="data/evals/intent.jsonl", help="标注集路径")
    args = parser.parse_args()

    from app.core.config import settings
    from app.evaluation.intent import load_cases, run_eval
    from app.prompts.registry import PromptRegistry

    try:
        from app.core.llm_factory import build_chat_model  # Phase 2 接线
    except ImportError:
        build_chat_model = None
    if build_chat_model is None:
        raise SystemExit(
            "真实模型工厂尚未接线（Phase 2：app/core/llm_factory.py）。"
            "评估器本身可用桩模型测试，见 tests/evals/test_intent_eval.py。"
        )

    llm = build_chat_model()
    registry = PromptRegistry(settings.prompts_dir)
    cases = load_cases(args.data)
    report = run_eval(llm, registry, cases)
    print(f"准确率: {report.accuracy:.1%} ({report.correct}/{report.total})")
    for failure in report.failures:
        print(
            f"  ✗ [{failure.id}] {failure.message!r}: "
            f"期望 {failure.expected}, 实际 {failure.actual}"
        )


if __name__ == "__main__":
    main()
