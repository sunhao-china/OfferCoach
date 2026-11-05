"""Langfuse Prompt 管理：本地 YAML 模板 ↔ Langfuse 提示词（评估/实验用，生产仍读本地）。

- 名称映射：registry 名（如 mock_interview/judge）→ `offercoach/{name}`
- 占位符：本地 string.Template（$var）↔ Langfuse Mustache（{{var}}）
- push：按 source_version 升序推送；每个版本打标签 `sv{source_version}`，最高版本再打
  `latest`；内容 sha256 无变更跳过（幂等重跑零写入）
- config 记录 source_version / sha256 / template_path，供云端审计与 diff
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.prompts.registry import PromptRegistry

PROMPT_PREFIX = "offercoach"
LABEL_LATEST = "latest"

_VAR_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")
_MUSTACHE_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


def to_langfuse_template(template: str) -> str:
    """本地 `$var` → Langfuse `{{var}}`。"""
    return _VAR_RE.sub(r"{{\1}}", template)


def from_langfuse_template(template: str) -> str:
    """Langfuse `{{var}}` → 本地 `$var`（往返/本地渲染用）。"""
    return _MUSTACHE_RE.sub(r"$\1", template)


def template_sha256(template: str) -> str:
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def label_for(version: int) -> str:
    return f"sv{version}"


class LangfuseClientProtocol(Protocol):
    """langfuse.Langfuse 的最小接口（测试用 Fake 桩）。"""

    def get_prompt(
        self, name: str, *, version: int | None = None, label: str | None = None
    ) -> Any: ...

    def create_prompt(
        self, *, name: str, prompt: str, labels: list[str] | None = None, config: Any = None
    ) -> Any: ...


@dataclass
class PushResult:
    name: str
    source_version: int
    action: str  # created / skipped
    langfuse_version: int | None = None


@dataclass
class PromptDiff:
    name: str
    source_version: int
    state: str  # missing / same / changed


@dataclass
class PushReport:
    results: list[PushResult] = field(default_factory=list)

    @property
    def created(self) -> int:
        return sum(1 for r in self.results if r.action == "created")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.action == "skipped")


def push_all_prompts(
    client: LangfuseClientProtocol,
    registry: PromptRegistry,
    *,
    templates_dir: Path,
    prefix: str = PROMPT_PREFIX,
) -> PushReport:
    """把 registry 全部模板推入 Langfuse；返回每版本推送结果。"""
    report = PushReport()
    for name in registry.names():
        latest_local = max(registry.versions(name))
        for version in registry.versions(name):
            spec = registry.get(name, version)
            lf_name = f"{prefix}/{name}"
            digest = template_sha256(spec.template)
            remote = _fetch_quiet(client, lf_name, label=label_for(version))
            if remote is not None and (remote.config or {}).get("sha256") == digest:
                report.results.append(PushResult(name, version, "skipped"))
                continue
            labels = [label_for(version)]
            if version == latest_local:
                labels.append(LABEL_LATEST)
            created = client.create_prompt(
                name=lf_name,
                prompt=to_langfuse_template(spec.template),
                labels=labels,
                config={
                    "source_version": version,
                    "sha256": digest,
                    # as_posix：Windows 下也存正斜杠路径（云端审计可读）
                    "template_path": (
                        Path(templates_dir).joinpath(*name.split("/")).as_posix()
                        + f".v{version}.yaml"
                    ),
                },
            )
            report.results.append(
                PushResult(name, version, "created", getattr(created, "version", None))
            )
    return report


def diff_prompts(
    client: LangfuseClientProtocol,
    registry: PromptRegistry,
    *,
    prefix: str = PROMPT_PREFIX,
) -> list[PromptDiff]:
    """对比本地模板与云端（按 sv{version} 标签寻址）：

    - missing：云端无该版本（push 将新建）
    - same：内容 sha256 一致（push 将跳过）
    - changed：同版本标签内容已变（push 将创建新 Langfuse 版本）
    """
    diffs = []
    for name in registry.names():
        for version in registry.versions(name):
            spec = registry.get(name, version)
            digest = template_sha256(spec.template)
            remote = _fetch_quiet(client, f"{prefix}/{name}", label=label_for(version))
            if remote is None:
                state = "missing"
            elif (remote.config or {}).get("sha256") == digest:
                state = "same"
            else:
                state = "changed"
            diffs.append(PromptDiff(name, version, state))
    return diffs


def _fetch_quiet(client: LangfuseClientProtocol, name: str, *, label: str) -> Any:
    try:
        return client.get_prompt(name, label=label)
    except Exception:  # 版本标签不存在等：视为未推送（Fake 桩与 SDK 错误语义一致）
        return None
