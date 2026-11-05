"""共享测试工具：LLM 桩与常用 fixture。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.hooks.bus import HookBus
from app.prompts.registry import PromptRegistry

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "prompts" / "templates"


class StubLLM:
    """按预设序列返回响应的 LLM 桩；记录全部调用供断言。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[str]] = []

    def invoke(self, messages: list[str], **kwargs):
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("StubLLM 无可用响应（调用次数超过预设序列）")
        return SimpleNamespace(content=self._responses.pop(0))

    def bind_tools(self, tools, **kwargs):
        """ReAct 构建时调用；组合层总是构建 ReAct 子图，桩返回自身即可。"""
        return self

    def stream(self, messages: list[str], **kwargs):
        """流式：单块产出（与 invoke 同源），供 clarify 等流式节点复用。"""
        return [SimpleNamespace(content=self.invoke(messages, **kwargs).content)]


@pytest.fixture
def registry() -> PromptRegistry:
    """使用生产提示词资产目录（真实模板是系统的一部分，被快照锁定）。"""
    return PromptRegistry(TEMPLATES_DIR)


@pytest.fixture
def make_llm():
    def _make(responses: list[str]) -> StubLLM:
        return StubLLM(responses)

    return _make


@pytest.fixture
def bus() -> HookBus:
    return HookBus()
