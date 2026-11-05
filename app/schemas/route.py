"""路由决策相关数据模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Intent(StrEnum):
    """意图枚举（v1）。"""

    MOCK_INTERVIEW = "mock_interview"
    RESUME_ANALYSIS = "resume_analysis"
    INTERVIEW_REVIEW = "interview_review"
    KNOWLEDGE_QA = "knowledge_qa"
    CHITCHAT = "chitchat"
    CLARIFY = "clarify"


class InterviewSlots(BaseModel):
    """从消息中抽取的面试槽位；提取不到为 None。"""

    position: str | None = None
    years: int | None = None
    round: str | None = None
    difficulty: str | None = None


class IntentResult(BaseModel):
    """LLM 分类器的原始输出（JSON 解析 + 校验后）。"""

    intent: Intent
    confidence: float = Field(ge=0, le=1, description="0~1 置信度")
    slots: InterviewSlots = Field(default_factory=InterviewSlots)


RouteSource = Literal["rule", "llm", "fallback"]


class RouteDecision(BaseModel):
    """路由最终决策：供根图选择子图。"""

    intent: Intent
    confidence: float = Field(ge=0, le=1)
    slots: InterviewSlots = Field(default_factory=InterviewSlots)
    source: RouteSource
    target_graph: str
