"""SSE 流式输出工具：终稿文本分块推送（真实 token 流式后置，见规格实现备注）。

服务端不加人为延迟——渲染节奏由前端逐块间隔控制，测试保持快速。
"""

from __future__ import annotations

import json

CHUNK_SIZE = 30


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """按字符切块（str 切片天然按字符，无字节截断）；空串返回空列表。"""
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def sse_data(payload: dict) -> str:
    """一行 SSE data 帧：`data: {json}\\n\\n`（中文不转义）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
