"""JavaGuide md 提取：问题式标题 + 正文 → 问答题候选（确定性、纯函数、可单测）。

依据 specs/02-in-progress/question_bank/java-guide-import.md 模块二契约：
- 路径片段 → category 有序规则表，首个命中生效，README 跳过；
- 标题 `^(#{2,4})`，题面清理 emoji/链接/反引号后以 `？` 结尾、6~100 字；
- 正文到同级或更高级标题为止；更深的非问题小标题并入正文、更深的子问题先收口父题再独立成候选；
- fenced code block / 图片 / HTML 剔除，链接语法转纯文本；正文 100~2500 字；
- 文件按路径排序、题按文档序，输出确定性；候选内题面归一化去重。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.question_bank.validator import norm_text

CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("docs/java/basis/", "java_basics"),
    ("docs/java/collection/", "java_basics"),
    ("docs/java/concurrent/", "concurrent"),
    ("docs/java/jvm/", "jvm"),
    ("docs/database/mysql/", "mysql"),
    ("docs/database/redis/", "redis"),
    ("docs/system-design/framework/spring/", "spring"),
    ("docs/cs-basics/network/", "network_os"),
    ("docs/cs-basics/operating-system/", "network_os"),
    ("docs/distributed-system/", "distributed"),
    ("docs/high-availability/", "distributed"),
    ("docs/high-performance/", "distributed"),
)

HEADING_PATTERN = re.compile(r"^(#{2,4})\s+(.+)$")
FENCE_PATTERN = re.compile(r"^```")
LINK_PATTERN = re.compile(r"\[([^\]]*)\]\([^)]*\)")
IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
TAG_PATTERN = re.compile(r"<[^>]+>")
# 标题常见标记（高频题 ⭐️ 等）：提取时剔除，避免进题面
EMOJI_PATTERN = re.compile(r"[⭐️❓✅🔥❗️]")

QUESTION_MIN, QUESTION_MAX = 6, 100
ANSWER_MIN, ANSWER_MAX = 100, 2500


@dataclass(frozen=True)
class RawQuestion:
    category: str
    question: str
    answer: str


def _clean_heading(text: str) -> str:
    text = EMOJI_PATTERN.sub("", text)
    text = IMAGE_PATTERN.sub("", text)
    text = LINK_PATTERN.sub(r"\1", text)
    return text.replace("`", "").strip()


def _clean_body(text: str) -> str:
    text = IMAGE_PATTERN.sub("", text)
    text = LINK_PATTERN.sub(r"\1", text)
    return TAG_PATTERN.sub("", text)


def _category_for(path: str) -> str | None:
    for fragment, category in CATEGORY_RULES:
        if fragment in path:
            return category
    return None


def _is_question(heading: str) -> bool:
    return heading.endswith("？") and QUESTION_MIN <= len(heading) <= QUESTION_MAX


def extract_candidates(root: Path) -> list[RawQuestion]:
    """遍历 root 下全部 md（路径排序，跳过 README），提取问答候选（文档序）。"""
    candidates: list[RawQuestion] = []
    seen: set[str] = set()
    for path in sorted(root.rglob("*.md")):
        if path.name == "README.md":
            continue
        category = _category_for(path.as_posix())
        if category is None:
            continue
        candidates.extend(_extract_file(path, category, seen))
    return candidates


def _extract_file(path: Path, category: str, seen: set[str]) -> list[RawQuestion]:
    found: list[RawQuestion] = []
    current_question: str | None = None
    current_level = 0
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        """收口当前候选：清理正文、长度过滤、题面去重后入列。"""
        nonlocal current_question, body
        if current_question is None:
            return
        answer = _clean_body("\n".join(body)).strip()
        question = current_question
        current_question = None
        body = []
        if not ANSWER_MIN <= len(answer) <= ANSWER_MAX:
            return
        norm_q = norm_text(question)
        if norm_q in seen:
            return
        seen.add(norm_q)
        found.append(RawQuestion(category=category, question=question, answer=answer))

    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE_PATTERN.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_PATTERN.match(line)
        if match:
            level = len(match.group(1))
            text = _clean_heading(match.group(2))
            question_like = _is_question(text)
            if current_question is not None:
                if level <= current_level or question_like:
                    flush()  # 同级/更高级标题，或更深的子问题标题：先收口当前候选
                else:
                    body.append(text)  # 更深的非问题小标题：并入当前正文
            if question_like:
                current_question = text
                current_level = level
            continue
        if current_question is not None:
            body.append(line)
    flush()
    return found
