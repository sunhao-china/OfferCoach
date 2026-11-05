"""题库检索工具：按关键词/分类/难度检索题库，返回 JSON 化题目列表。"""

from __future__ import annotations

import json
import re

from langchain_core.tools import BaseTool, tool

from app.question_bank.store import QuestionStore, normalize_position

TOOL_NAME = "search_question_bank"
EMPTY_RESULT_TEXT = "题库中未找到匹配的题目"
_RESULT_FIELDS = (
    "question_id",
    "category",
    "difficulty",
    "question",
    "reference_points",
    "followups",
)
# 整句兜底拆词时的停用词（模型把用户原句当 keywords 传入时过滤虚词）
_TOKEN_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "in",
    "on",
    "to",
    "for",
    "it",
    "its",
    "is",
    "are",
    "was",
    "were",
    "be",
    "do",
    "does",
    "did",
    "what",
    "why",
    "how",
    "which",
    "when",
    "where",
    "who",
    "的",
    "了",
    "是",
    "吗",
    "呢",
    "吧",
    "啊",
    "什么",
    "为什么",
    "还有",
    "存在",
    "请问",
    "如何",
    "怎么",
    "怎样",
    "哪些",
    "这个",
    "那个",
    "一个",
    "一下",
    "以及",
    "可以",
    "不是",
    "是否",
    "讲讲",
    "说说",
    "解释",
    "介绍",
    "是什么",
    "什么是",
    "有哪些",
    "的区别",
    "的作用",
    "的原因",
}
_TOKEN_SPLIT_RE = re.compile(r"[^\w+#.\-]+")
_MAX_TOKENS = 8


def _to_json_rows(questions) -> str:
    rows = [{field: getattr(q, field) for field in _RESULT_FIELDS} for q in questions]
    return json.dumps(rows, ensure_ascii=False)


def _retry_tokens(keywords: str) -> list[str] | None:
    """空结果兜底：把整句关键词拆成词条（去停用词/去重，上限 8 个）。

    仅当原始输入可拆出 ≥2 个片段时才重试（精炼单关键词不折腾）；返回 None
    表示不值得重试。词条间为 OR 关系，命中任一即可。
    """
    raw = [part for part in _TOKEN_SPLIT_RE.split(keywords) if part]
    if len(raw) < 2:
        return None
    tokens: list[str] = []
    for part in raw:
        if len(part) < 2 or part.lower() in _TOKEN_STOPWORDS or part in tokens:
            continue
        tokens.append(part)
        if len(tokens) >= _MAX_TOKENS:
            break
    return tokens or None


def build_question_bank_tool(store: QuestionStore) -> BaseTool:
    """构建 search_question_bank 工具；limit=5，空结果返回明确提示。"""

    @tool(TOOL_NAME)
    def search_question_bank(
        keywords: str,
        category: str | None = None,
        difficulty: str | None = None,
        position: str | None = None,
    ) -> str:
        """检索面试题库。keywords 为题目关键词（必填）；category 可选（如 jvm/mysql/
        python_basics）；difficulty 可选（entry/medium/hard）；position 可选（岗位，如
        python_backend，口语输入如「Python」「C++」亦可）；position 省略时全岗位检索。
        返回 JSON 数组。"""
        rows = store.search(
            keywords=keywords,
            category=category,
            difficulty=difficulty,
            position=normalize_position(position) if position else None,
            limit=5,
        )
        if not rows:
            # 兜底：模型可能把整句问题当 keywords 传入（LIKE 精确子串不命中）。
            # 拆词后只按词条重试（放弃 category/difficulty/position 约束换召回）。
            tokens = _retry_tokens(keywords)
            if tokens:
                # 显式 position=None：store.search 缺省会回退 java_backend
                rows = store.search(keywords=tokens, position=None, limit=5)
        if not rows:
            return EMPTY_RESULT_TEXT
        return _to_json_rows(rows)

    return search_question_bank
