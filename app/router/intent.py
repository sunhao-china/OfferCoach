"""LLM 意图分类器：JSON 结构化输出 + 有限重试 + 降级。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.hooks.bus import EVENT_ERROR
from app.prompts.registry import PromptRegistry
from app.schemas.route import IntentResult

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_CORRECTION = (
    "\n\n你的上一个回复不是合法 JSON，请重新输出。只输出一个 JSON 对象，不要任何其他内容。"
)


class IntentParseError(ValueError):
    """LLM 响应无法解析为 IntentResult。"""


def parse_intent_json(content: Any) -> IntentResult:
    """从 LLM 响应中提取并校验 JSON。容忍代码围栏与前后杂文。"""
    text = str(content or "").strip()
    if not text:
        raise IntentParseError("响应为空")
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise IntentParseError("响应中无 JSON 对象")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise IntentParseError(f"JSON 解析失败: {exc}") from exc
    return IntentResult.model_validate(data)


class IntentClassifier:
    """渲染版本化提示词 → 调用 LLM → 解析校验；重试耗尽返回 None（由 dispatcher 降级）。"""

    PROMPT_NAME = "router/intent"

    def __init__(
        self,
        llm: Any,
        registry: PromptRegistry,
        *,
        max_retries: int = 1,
        bus: Any = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._max_retries = max_retries
        self._bus = bus

    def classify(
        self, user_message: str, history_summary: str = "", config=None
    ) -> IntentResult | None:
        prompt = self._registry.render(
            self.PROMPT_NAME,
            user_message=user_message,
            history_summary=history_summary or "（无）",
        )
        for attempt in range(self._max_retries + 1):
            messages = [prompt] if attempt == 0 else [prompt, _CORRECTION]
            response = self._llm.invoke(messages, config=config)
            try:
                return parse_intent_json(response.content)
            except ValueError as exc:  # IntentParseError / pydantic ValidationError
                last_error: ValueError = exc
        if self._bus is not None:
            self._bus.fire(
                EVENT_ERROR,
                payload=last_error,
                metadata={"component": "IntentClassifier", "prompt": self.PROMPT_NAME},
            )
        return None
