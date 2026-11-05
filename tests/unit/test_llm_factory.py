"""L1: 模型工厂 — 档位 / 型号覆盖 / 缓存 / 缺 key 报错。"""

from __future__ import annotations

import pytest
from langchain_deepseek import ChatDeepSeek

from app.core.llm_factory import TIER_CHEAP, TIER_STRONG, build_chat_model


@pytest.fixture(autouse=True)
def _key_and_cache(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    yield
    build_chat_model.cache_clear()


def test_valid_tier_returns_deepseek_model():
    model = build_chat_model(TIER_STRONG)
    assert isinstance(model, ChatDeepSeek)
    assert model.model_name == "deepseek-chat"


def test_invalid_tier_raises():
    with pytest.raises(ValueError, match="未知模型档位"):
        build_chat_model("super")


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        build_chat_model()


def test_env_overrides_model_name(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_STRONG_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("DEEPSEEK_CHEAP_MODEL", "deepseek-chat")
    assert build_chat_model(TIER_STRONG).model_name == "deepseek-reasoner"
    assert build_chat_model(TIER_CHEAP).model_name == "deepseek-chat"


def test_cache_returns_same_instance_per_tier_and_temperature():
    strong_a = build_chat_model(TIER_STRONG)
    strong_b = build_chat_model(TIER_STRONG)
    cold = build_chat_model(TIER_STRONG, temperature=0.0)
    cheap = build_chat_model(TIER_CHEAP)
    assert strong_a is strong_b
    assert strong_a is not cold
    assert strong_a is not cheap
