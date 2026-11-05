"""LLM 结构化输出解析工具（工作流节点共用）。

容忍 markdown 代码围栏；解析失败返回 None，由调用方兜底前进（不中断管道）。
"""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_text(text: str) -> dict | None:
    """解析 LLM 输出为 dict：容忍 markdown 代码围栏；失败返回 None。"""
    cleaned = text.strip()
    match = _FENCE_RE.search(cleaned)
    if match:
        cleaned = match.group(1).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
