"""路由表：意图 → 目标执行图标识。"""

from __future__ import annotations

from app.schemas.route import Intent

# v0.1 目标图为图标识字符串；根图落地时按此名解析为已编译子图
ROUTE_TABLE: dict[Intent, str] = {
    Intent.MOCK_INTERVIEW: "mock_interview",
    Intent.RESUME_ANALYSIS: "resume_analysis",
    Intent.INTERVIEW_REVIEW: "interview_review",
    Intent.KNOWLEDGE_QA: "react_agent",
    Intent.CHITCHAT: "react_agent",
    Intent.CLARIFY: "clarify",
}


class RouteTableError(KeyError):
    """意图不在路由表中。"""


def resolve_target(intent: Intent) -> str:
    try:
        return ROUTE_TABLE[intent]
    except KeyError:
        raise RouteTableError(f"意图 {intent!r} 不在路由表中") from None
