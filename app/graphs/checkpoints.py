"""检查点读取兜底（2026-10-21 实证）：langgraph 顶层直连 invoke 的检查点落 ns=""，
根图嵌套调用（wrapper 节点内 invoke）落 ns=子图名。同一条业务数据可能由两种路径写入，
读取侧按候选 ns 依次尝试，不依赖 saver 的 ns 回退行为差异
（InMemorySaver 会回退、SqliteSaver 不保证）。
"""

from __future__ import annotations

from typing import Any

MOCK_INTERVIEW_NAMESPACES = ("mock_interview", "")


def read_checkpoint(checkpointer: Any, thread_id: str, namespaces: tuple[str, ...]) -> Any | None:
    """按候选 ns 依次读检查点；全部未命中返回 None。"""
    for namespace in namespaces:
        snapshot = checkpointer.get_tuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace}}
        )
        if snapshot is not None:
            return snapshot
    return None
