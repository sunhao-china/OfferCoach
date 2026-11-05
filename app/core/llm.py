"""最小 LLM 接口协议。

本层不依赖 LangChain：分类器等组件只要求 `llm.invoke(list[str])` 返回带 `.content` 属性的对象。
LangChain 的 BaseChatModel.invoke(list[str]) 会把字符串转成 HumanMessage，鸭子兼容本协议。
真实模型工厂（按环境变量构建 ChatOpenAI 等）在 Phase 2 于此模块接线，协议保持不变。
"""

from __future__ import annotations

from typing import Any, Protocol


class LLM(Protocol):
    """可注入的最小 LLM 接口。"""

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        """输入 list[str]，返回任意带 .content 属性的对象。"""
        ...
