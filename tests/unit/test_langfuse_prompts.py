"""Langfuse 提示词推送单测（AC1/AC2）：占位符转换 + 推送/跳过/标签（零网络）。"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.observability.langfuse_prompts import (
    PROMPT_PREFIX,
    diff_prompts,
    from_langfuse_template,
    push_all_prompts,
    template_sha256,
    to_langfuse_template,
)
from app.prompts.registry import PromptRegistry
from tests.support.langfuse import FakeLangfuse


def _write_prompt(templates_dir: Path, name: str, version: int, template: str) -> None:
    path = templates_dir / f"{name}.v{version}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    spec = {"name": name, "version": version, "description": "测试", "template": template}
    path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")


# ---------- AC1：占位符转换 ----------


def test_to_from_roundtrip():
    original = "你是 $role，请处理用户消息：$user_message。今天是 $date。"
    assert from_langfuse_template(to_langfuse_template(original)) == original


def test_to_langfuse_template_multiple_chinese_vars():
    template = "你好 $name，岗位是 $position，历史：$history_summary。"
    converted = to_langfuse_template(template)
    assert converted == "你好 {{name}}，岗位是 {{position}}，历史：{{history_summary}}。"


def test_to_langfuse_template_ignores_non_vars():
    # 非变量 $（后跟数字/标点）保持原样
    assert to_langfuse_template("价格 $100，描述 ${x}") == "价格 $100，描述 ${x}"


def test_from_langfuse_template_converts_mustache():
    assert from_langfuse_template("{{a}} 和 {{b}}") == "$a 和 $b"


# ---------- AC2：推送 ----------


def test_push_all_names_and_versions(tmp_path):
    _write_prompt(tmp_path, "router/intent", 1, "分类：$user_message")
    _write_prompt(tmp_path, "router/intent", 2, "分类 v2：$user_message")
    _write_prompt(tmp_path, "mock_interview/judge", 1, "判题：$question")
    client = FakeLangfuse()
    report = push_all_prompts(client, PromptRegistry(tmp_path), templates_dir=tmp_path)
    assert report.created == 3 and report.skipped == 0
    pushed_names = {c["name"] for c in client.prompt_creates}
    assert pushed_names == {
        f"{PROMPT_PREFIX}/router/intent",
        f"{PROMPT_PREFIX}/mock_interview/judge",
    }
    for created in client.prompt_creates:
        assert created["config"]["source_version"] in (1, 2)
        assert created["config"]["sha256"]
        assert "{{user_message}}" in created["prompt"] or "{{question}}" in created["prompt"]


def test_push_skips_unchanged(tmp_path):
    _write_prompt(tmp_path, "router/intent", 1, "分类：$user_message")
    client = FakeLangfuse()
    registry = PromptRegistry(tmp_path)
    first = push_all_prompts(client, registry, templates_dir=tmp_path)
    second = push_all_prompts(client, registry, templates_dir=tmp_path)
    assert first.created == 1
    assert second.created == 0 and second.skipped == 1
    assert len(client.prompt_creates) == 1  # 零写入


def test_push_changed_content_creates_new_version(tmp_path):
    _write_prompt(tmp_path, "router/intent", 1, "分类 v1：$user_message")
    _write_prompt(tmp_path, "router/intent", 2, "分类 v2：$user_message")
    client = FakeLangfuse()
    push_all_prompts(client, PromptRegistry(tmp_path), templates_dir=tmp_path)
    # 本地 v2 内容变更 → 重推该版本：云端新建第 3 个版本
    _write_prompt(tmp_path, "router/intent", 2, "分类 v2 改：$user_message")
    report = push_all_prompts(client, PromptRegistry(tmp_path), templates_dir=tmp_path)
    assert report.created == 1 and report.skipped == 1
    created = client.prompt_creates[-1]
    assert created["config"]["source_version"] == 2
    assert created["config"]["sha256"] == template_sha256("分类 v2 改：$user_message")
    # 云端该提示词累计 3 个版本，新版本标签 sv2 + latest
    versions = [v for v in client.prompts[f"{PROMPT_PREFIX}/router/intent"] if isinstance(v, int)]
    assert versions == [1, 2, 3]
    assert created["labels"] == ["sv2", "latest"]


def test_latest_label_only_on_max_version(tmp_path):
    _write_prompt(tmp_path, "a", 1, "v1：$x")
    _write_prompt(tmp_path, "a", 2, "v2：$x")
    client = FakeLangfuse()
    push_all_prompts(client, PromptRegistry(tmp_path), templates_dir=tmp_path)
    by_source = {c["config"]["source_version"]: c["labels"] for c in client.prompt_creates}
    assert by_source[1] == ["sv1"]
    assert by_source[2] == ["sv2", "latest"]


def test_config_records_sha_and_path(tmp_path):
    _write_prompt(tmp_path, "resume/analyze", 2, "分析：$resume")
    client = FakeLangfuse()
    push_all_prompts(client, PromptRegistry(tmp_path), templates_dir=tmp_path)
    config = client.prompt_creates[0]["config"]
    assert config["sha256"] == template_sha256("分析：$resume")
    assert config["template_path"].endswith("resume/analyze.v2.yaml")


def test_diff_prompts_states(tmp_path):
    _write_prompt(tmp_path, "a", 1, "v1：$x")
    client = FakeLangfuse()
    registry = PromptRegistry(tmp_path)
    assert [d.state for d in diff_prompts(client, registry)] == ["missing"]
    push_all_prompts(client, registry, templates_dir=tmp_path)
    assert [d.state for d in diff_prompts(client, registry)] == ["same"]
    # 本地同版本内容变更（云端 sv1 仍是旧 sha256）→ changed
    _write_prompt(tmp_path, "a", 1, "v1 改：$x")
    assert [d.state for d in diff_prompts(client, PromptRegistry(tmp_path))] == ["changed"]
