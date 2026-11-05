"""规则路由：0 token 快路径。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.router.routes import resolve_target
from app.schemas.route import Intent, InterviewSlots, RouteDecision

_YEARS_RE = re.compile(r"(\d+)\s*年")
_LATIN_WORD_RE = re.compile(r"^[a-z]+$")


@dataclass(frozen=True)
class Rule:
    """一条规则：命中任一 pattern 即视为命中该意图。"""

    intent: Intent
    patterns: tuple[str, ...]
    label: str


RULES: tuple[Rule, ...] = (
    Rule(
        Intent.MOCK_INTERVIEW,
        ("模拟面试", "mock interview", "面试练习", "来一场面试", "陪练面试"),
        "模拟面试",
    ),
    Rule(Intent.RESUME_ANALYSIS, ("简历", "resume", "cv"), "简历分析"),
    Rule(Intent.INTERVIEW_REVIEW, ("复盘", "回顾面试", "上次面试"), "面试复盘"),
    Rule(Intent.CHITCHAT, ("你好", "您好", "hello", "hi", "在吗", "在么", "谢谢", "再见"), "闲聊"),
)


@dataclass(frozen=True)
class RuleMatch:
    """规则匹配结果：唯一命中（rule 非空）或冲突（rule 为空，conflicts 记录冲突规则）。"""

    rule: Rule | None
    conflicts: tuple[Rule, ...] = ()

    @property
    def ambiguous(self) -> bool:
        return self.rule is None


def _pattern_matches(pattern: str, folded: str) -> bool:
    """拉丁文单词按词边界匹配（"hi" 不得命中 "this"），中文按子串匹配。"""
    if _LATIN_WORD_RE.match(pattern):
        return re.search(rf"\b{re.escape(pattern)}\b", folded) is not None
    return pattern in folded


def match_rules(message: str) -> RuleMatch | None:
    """返回唯一命中 / 冲突（ambiguous）/ 未命中（None）。"""
    folded = message.casefold()
    hits = [rule for rule in RULES if any(_pattern_matches(p, folded) for p in rule.patterns)]
    if not hits:
        return None
    if len(hits) > 1:
        return RuleMatch(rule=None, conflicts=tuple(hits))
    return RuleMatch(rule=hits[0])


def extract_years(message: str) -> int | None:
    """规则路径的槽位提取：仅年限（如"3年经验" → 3），其余槽位由 LLM 负责。"""
    m = _YEARS_RE.search(message)
    return int(m.group(1)) if m else None


def build_rule_decision(message: str, rule: Rule) -> RouteDecision:
    """规则命中时的决策：无 LLM 调用，confidence=1.0。"""
    return RouteDecision(
        intent=rule.intent,
        confidence=1.0,
        slots=InterviewSlots(years=extract_years(message)),
        source="rule",
        target_graph=resolve_target(rule.intent),
    )
