"""Langfuse 桩：提示词/数据集/实验的内存实现（评估模块单测用，零网络）。

接口覆盖 Langfuse SDK 5.0.4 本工程用到的子集：
- get_prompt(name, version=.../label=...) → FakePrompt（.compile(**vars)）
- create_prompt(name, prompt, labels, config) → 自增版本
- get_dataset / create_dataset / create_dataset_item（item 带 .id/.input/.expected_output）
- FakeDataset.run_experiment：逐条 task → evaluator 打分，结果落 experiments 记录
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

_MUSTACHE_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


class FakePrompt:
    """TextPromptClient 桩。"""

    def __init__(self, name: str, content: str, *, version: int, config: dict | None = None):
        self.name = name
        self.content = content
        self.version = version
        self.config = config or {}

    def compile(self, **kwargs: Any) -> str:
        return _MUSTACHE_RE.sub(lambda m: str(kwargs.get(m.group(1), "")), self.content)


class FakeDatasetItem:
    def __init__(self, id: str, input: Any, expected_output: Any, metadata: Any = None):
        self.id = id
        self.input = input
        self.expected_output = expected_output
        self.metadata = metadata or {}


class FakeDataset:
    """DatasetClient 桩：items 列表 + run_experiment（模拟 SDK 的逐条执行与打分）。"""

    def __init__(self, name: str, *, description: str = ""):
        self.name = name
        self.description = description
        self.items: list[FakeDatasetItem] = []
        self.experiments: list[dict] = []

    def run_experiment(
        self,
        *,
        name: str,
        run_name: str | None = None,
        description: str | None = None,
        task: Any,
        evaluators: list[Any] = (),
        max_concurrency: int = 50,
        metadata: dict | None = None,
        experiment_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        runs = []
        for item in self.items:
            output = task(item=item)
            scores = []
            for evaluator in evaluators:
                result = evaluator(
                    input=item.input,
                    output=output,
                    expected_output=item.expected_output,
                    metadata={"dataset_item_id": item.id},
                )
                scores.extend(result if isinstance(result, list) else [result])
            runs.append({"item_id": item.id, "output": output, "scores": scores})
        self.experiments.append(
            {
                "name": name,
                "run_name": run_name,
                "description": description,
                "metadata": metadata or {},
                "runs": runs,
            }
        )
        return SimpleNamespace(dataset_run_id=f"{name}#{run_name}", dataset_run_url="")


class FakeLangfuse:
    """Langfuse 客户端桩：记录全部写入调用供断言。"""

    def __init__(self) -> None:
        self.prompts: dict[str, dict[int | str, FakePrompt]] = {}
        self.prompt_creates: list[dict] = []
        self.datasets: dict[str, FakeDataset] = {}
        self.dataset_creates: list[dict] = []
        self.dataset_item_creates: list[dict] = []

    # ---- prompts ----

    def seed_prompt(
        self,
        name: str,
        content: str,
        *,
        version: int,
        labels: tuple[str, ...] = (),
        config: dict | None = None,
    ) -> FakePrompt:
        prompt = FakePrompt(name, content, version=version, config=config)
        self.prompts.setdefault(name, {})[version] = prompt
        for label in labels:
            self.prompts[name][label] = prompt
        return prompt

    def get_prompt(
        self, name: str, *, version: int | None = None, label: str | None = None, **kwargs: Any
    ) -> FakePrompt:
        by = self.prompts.get(name) or {}
        key = version if version is not None else label
        if key not in by:
            raise LookupError(f"prompt {name} 无 {key}")
        return by[key]

    def create_prompt(
        self,
        *,
        name: str,
        prompt: str,
        labels: list[str] = (),
        tags: Any = None,
        type: str = "text",
        config: Any = None,
        commit_message: str | None = None,
    ) -> FakePrompt:
        versions = [v for v in self.prompts.get(name, {}) if isinstance(v, int)]
        new_version = max(versions, default=0) + 1
        created = FakePrompt(name, prompt, version=new_version, config=config)
        self.prompts.setdefault(name, {})[new_version] = created
        for label in labels:
            self.prompts[name][label] = created
        self.prompt_creates.append(
            {"name": name, "prompt": prompt, "labels": list(labels), "config": config or {}}
        )
        return created

    # ---- datasets ----

    def get_dataset(self, name: str, **kwargs: Any) -> FakeDataset:
        if name not in self.datasets:
            raise LookupError(f"dataset {name} 不存在")
        return self.datasets[name]

    def create_dataset(
        self, *, name: str, description: str | None = None, **kwargs: Any
    ) -> FakeDataset:
        dataset = FakeDataset(name, description=description or "")
        self.datasets[name] = dataset
        self.dataset_creates.append({"name": name, "description": description})
        return dataset

    def create_dataset_item(
        self,
        *,
        dataset_name: str,
        input: Any = None,
        expected_output: Any = None,
        metadata: Any = None,
        id: str | None = None,
        **kwargs: Any,
    ) -> FakeDatasetItem:
        item = FakeDatasetItem(id or "", input, expected_output, metadata)
        self.datasets[dataset_name].items.append(item)
        self.dataset_item_creates.append(
            {
                "dataset_name": dataset_name,
                "id": id,
                "input": input,
                "expected_output": expected_output,
                "metadata": metadata,
            }
        )
        return item
