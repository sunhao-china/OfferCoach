"""Langfuse 追踪开关与 handler 构建。

只在 LANGFUSE_SECRET_KEY 与 LANGFUSE_PUBLIC_KEY 都设置时启用；langfuse
惰性 import——无密钥环境（测试/纯本地）零依赖加载、零网络。handler 由
API 层每请求构建一个（一条 trace），经 LangChain config 的 callbacks 传入；
会话元数据用 config metadata 的 langfuse_* 键（v3 约定，见 executor.py）。
"""

from __future__ import annotations

import os
from typing import Any


def tracing_enabled() -> bool:
    """是否已配置 Langfuse 密钥。"""
    return bool(
        os.environ.get("LANGFUSE_SECRET_KEY") and os.environ.get("LANGFUSE_PUBLIC_KEY")
    )


def build_trace_handler() -> Any | None:
    """构建 LangChain CallbackHandler；未配置密钥返回 None（业务零感知）。"""
    if not tracing_enabled():
        return None
    from langfuse.langchain import CallbackHandler  # 惰性 import

    return CallbackHandler()
