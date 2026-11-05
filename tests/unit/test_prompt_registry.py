"""L1: 提示词注册表 — 加载 / 版本 / 渲染 / 错误 / 快照。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.prompts.registry import PromptNotFoundError, PromptRegistry, PromptRenderError


def _write(tmp_path: Path, name: str, version: int, template: str, description: str = "t") -> Path:
    path = tmp_path / f"{name}.v{version}.yaml"
    path.write_text(
        yaml.safe_dump(
            {"name": name, "version": version, "description": description, "template": template},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def test_render_defaults_to_latest_version(tmp_path: Path):
    _write(tmp_path, "greet", 1, "你好，$name")
    _write(tmp_path, "greet", 2, "欢迎回来，$name")
    registry = PromptRegistry(tmp_path)
    assert registry.render("greet", name="小明") == "欢迎回来，小明"


def test_render_explicit_version(tmp_path: Path):
    _write(tmp_path, "greet", 1, "你好，$name")
    _write(tmp_path, "greet", 2, "欢迎回来，$name")
    registry = PromptRegistry(tmp_path)
    assert registry.render("greet", version=1, name="小明") == "你好，小明"


def test_subdir_naming_derived_from_path(tmp_path: Path):
    (tmp_path / "router").mkdir()
    path = tmp_path / "router" / "intent.v1.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "router/intent",
                "version": 1,
                "description": "t",
                "template": "分类：$user_message",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    registry = PromptRegistry(tmp_path)
    assert registry.render("router/intent", user_message="你好") == "分类：你好"


def test_missing_variable_raises_with_name(tmp_path: Path):
    _write(tmp_path, "p", 1, "你好 $a 和 $b")
    registry = PromptRegistry(tmp_path)
    with pytest.raises(PromptRenderError) as exc:
        registry.render("p", a="1")
    assert "b" in str(exc.value)


def test_unknown_prompt_raises(tmp_path: Path):
    registry = PromptRegistry(tmp_path)
    with pytest.raises(PromptNotFoundError):
        registry.get("nope")


def test_unknown_version_raises_with_available_versions(tmp_path: Path):
    _write(tmp_path, "p", 1, "v1")
    _write(tmp_path, "p", 3, "v3")
    registry = PromptRegistry(tmp_path)
    with pytest.raises(PromptNotFoundError) as exc:
        registry.get("p", version=2)
    assert "[1, 3]" in str(exc.value)


def test_json_braces_survive_rendering(tmp_path: Path):
    _write(tmp_path, "p", 1, '示例：{"intent": "chitchat", "confidence": 0.95} 用户：$user_message')
    registry = PromptRegistry(tmp_path)
    rendered = registry.render("p", user_message="你好")
    assert rendered == '示例：{"intent": "chitchat", "confidence": 0.95} 用户：你好'


def test_non_template_files_ignored(tmp_path: Path):
    (tmp_path / "notes.md").write_text("说明文档", encoding="utf-8")
    (tmp_path / "data.yaml").write_text("key: value", encoding="utf-8")
    _write(tmp_path, "p", 1, "hi $x")
    registry = PromptRegistry(tmp_path)
    assert registry.render("p", x="1") == "hi 1"
    with pytest.raises(PromptNotFoundError):
        registry.get("data")


def test_filename_content_mismatch_raises(tmp_path: Path):
    path = tmp_path / "p.v1.yaml"
    path.write_text(
        yaml.safe_dump(
            {"name": "other", "version": 1, "description": "t", "template": "x"},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        PromptRegistry(tmp_path)


def test_intent_prompt_snapshot(snapshot, registry: PromptRegistry):
    """真实意图提示词渲染结果被快照锁定：模板变更会在此暴露 diff。"""
    rendered = registry.render("router/intent", user_message="帮我模拟面试", history_summary="")
    assert rendered == snapshot
