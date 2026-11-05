"""模型工厂：deepseek 分档（strong / cheap），配置经环境变量注入。

- strong：提问 / 追问 / 评分 / 报告（质量敏感）
- cheap：分类 / 总结 / 澄清（成本敏感）
- 型号可经 DEEPSEEK_STRONG_MODEL / DEEPSEEK_CHEAP_MODEL 覆盖，默认均为 deepseek-chat
- 返回 LangChain ChatDeepSeek，同时鸭子兼容最小 LLM 协议（app/core/llm.py）
"""

from __future__ import annotations

import os
from functools import cache

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from pydantic import SecretStr

TIER_STRONG = "strong"
TIER_CHEAP = "cheap"
TIERS = (TIER_STRONG, TIER_CHEAP)

_DEFAULT_MODEL = "deepseek-chat"


@cache
def build_chat_model(tier: str = TIER_STRONG, *, temperature: float | None = None) -> BaseChatModel:
    """按档位构建 deepseek 聊天模型；构造不发网络请求。"""
    if tier not in TIERS:
        raise ValueError(f"未知模型档位: {tier}（合法档位: {', '.join(TIERS)}）")
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY 环境变量，无法构建 LLM")
    model = os.environ.get(f"DEEPSEEK_{tier.upper()}_MODEL", _DEFAULT_MODEL)
    return ChatDeepSeek(model=model, api_key=SecretStr(api_key), temperature=temperature)
