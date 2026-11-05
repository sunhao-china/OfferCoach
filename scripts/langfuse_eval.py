"""Langfuse 评估实验 CLI（薄封装，逻辑在 app.observability.langfuse_eval）。

用法（需 LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY / LANGFUSE_BASE_URL + DEEPSEEK_API_KEY）:
  意图识别，用 Langfuse 提示词最新版:
    .venv/Scripts/python.exe scripts/langfuse_eval.py \
      --dataset oc-intent-v1 --task intent --prompt offercoach/router/intent
  提示词替换对比实验（intent v1 vs v2，同名实验下两个 run）:
    .venv/Scripts/python.exe scripts/langfuse_eval.py \
      --dataset oc-intent-v1 --task intent --prompt offercoach/router/intent --prompt-version 1
    .venv/Scripts/python.exe scripts/langfuse_eval.py \
      --dataset oc-intent-v1 --task intent --prompt offercoach/router/intent --prompt-version 2
  judge 判定 / B1 问答（本地 b1_quality judge 模板）:
    .venv/Scripts/python.exe scripts/langfuse_eval.py --dataset oc-judge-v1 --task judge \
      --prompt offercoach/mock_interview/judge
    .venv/Scripts/python.exe scripts/langfuse_eval.py --dataset oc-b1-qa-v1 --task b1_qa
  本地 YAML 兜底:
    .venv/Scripts/python.exe scripts/langfuse_eval.py \
      --dataset oc-intent-v1 --task intent --prompt-local router/intent
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "prompts" / "templates"


def _require_env() -> None:
    missing = [
        k
        for k in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY", "DEEPSEEK_API_KEY")
        if not os.environ.get(k)
    ]
    if missing:
        sys.exit(f"缺少环境变量: {', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Langfuse 数据集 → 真实 LLM 评估实验")
    parser.add_argument("--dataset", required=True, help="Langfuse 数据集名（如 oc-intent-v1）")
    parser.add_argument("--task", required=True, choices=["intent", "judge", "b1_qa"])
    parser.add_argument(
        "--prompt", default=None, help="Langfuse 提示词名（如 offercoach/router/intent）"
    )
    parser.add_argument(
        "--prompt-version", type=int, default=None, help="指定 Langfuse 版本（替换实验）"
    )
    parser.add_argument(
        "--prompt-local", default=None, help="本地 registry 提示词名（如 router/intent）"
    )
    parser.add_argument("--run-name", default=None, help="自定义 run 名（缺省自动生成）")
    parser.add_argument("--experiment-name", default=None, help="实验名（缺省=数据集名）")
    args = parser.parse_args()
    _require_env()

    from langfuse import Langfuse

    from app.core.llm_factory import TIER_CHEAP, build_chat_model
    from app.observability.langfuse_eval import RunSpec, run_experiment
    from app.prompts.registry import PromptRegistry

    llm = build_chat_model(TIER_CHEAP)
    registry = PromptRegistry(TEMPLATES_DIR)
    spec = RunSpec(
        dataset_name=args.dataset,
        task_name=args.task,
        run_name=args.run_name,
        description=f"Langfuse eval: {args.dataset} / {args.task}",
        prompt_name=args.prompt,
        prompt_version=args.prompt_version,
        prompt_local=args.prompt_local,
        experiment_name=args.experiment_name,
        metadata={"task": args.task, "source": "scripts/langfuse_eval.py"},
    )
    result = run_experiment(Langfuse(), spec=spec, llm=llm, registry=registry)
    run_id = getattr(result, "experiment_id", None) or getattr(result, "dataset_run_id", None)
    print(f"实验完成: {spec.experiment_name or spec.dataset_name} run={run_id}")
    url = getattr(result, "experiment_url", None) or getattr(result, "dataset_run_url", None)
    if url:
        print(f"云端: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
