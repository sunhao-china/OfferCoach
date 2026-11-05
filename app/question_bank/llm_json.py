"""LLM 输出 JSON 解析与形状校验：normalizer（二期）与生成器（三期）共用。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.question_bank.validator import DIFFICULTIES, TYPES

FIELDS = ("question", "reference_points", "followups", "difficulty", "type", "keywords")
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*$", re.MULTILINE)


def parse_llm_json(content: str) -> Any:
    """容错解析 LLM 输出：剥 Markdown 代码围栏后 json.loads，失败返回 None。"""
    text = _FENCE_RE.sub("", content).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def validate_llm_output(data: Any) -> list[str]:
    """单条题库记录形状校验（枚举/条数）；字段级终检由 validate_records 兜底。"""
    if not isinstance(data, dict):
        return ["LLM 输出不是 JSON 对象"]
    errors = [f"缺少字段 {f}" for f in FIELDS if f not in data]
    if errors:
        return errors
    if not isinstance(data["question"], str) or not data["question"].strip():
        errors.append("question 为空")
    for f, lo, hi in (("reference_points", 3, 5), ("followups", 2, 3)):
        if not isinstance(data[f], list) or not lo <= len(data[f]) <= hi:
            errors.append(f"{f} 应为 {lo}~{hi} 条")
    if not isinstance(data["keywords"], list) or len(data["keywords"]) < 3:
        errors.append("keywords 至少 3 个")
    if data["difficulty"] not in DIFFICULTIES:
        errors.append(f"difficulty 非法: {data['difficulty']}")
    if data["type"] not in TYPES:
        errors.append(f"type 非法: {data['type']}")
    return errors
