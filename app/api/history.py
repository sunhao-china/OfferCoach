"""历史回顾记录派生：零新增存储——chat_sessions 元数据 + 检查点状态合成。

completed 判定不做内容嗅探：interview 看快照 state.phase（PHASE_DONE = 已出报告），
resume/review 看是否有 AI 消息（管道一次跑完，有消息即有报告）。
chat 问答会话不进回顾页（连续问答，由会话页管理）。
"""

from __future__ import annotations

from typing import Any

from app.api.executor import read_snapshot, snapshot_messages
from app.api.schemas import HistoryRecordOut
from app.graphs.workflows.mock_interview import PHASE_DONE

PREVIEW_CHARS = 120
HISTORY_BUSINESSES = ("interview", "resume", "review")


def _completed(session: Any, snapshot: Any) -> bool:
    if snapshot is None:
        return False
    if session.business == "interview":
        state = snapshot.checkpoint.get("channel_values", {})
        return state.get("phase") == PHASE_DONE
    return any(message.role == "assistant" for message in snapshot_messages(snapshot))


def _preview(snapshot: Any, completed: bool) -> str:
    if not completed:
        return ""
    for message in reversed(snapshot_messages(snapshot)):
        if message.role != "assistant":
            continue
        text = message.content.strip()
        if not text:
            continue
        if len(text) > PREVIEW_CHARS:
            return text[:PREVIEW_CHARS] + "…"
        return text
    return ""


def build_history_records(checkpointer: Any, rows: list[Any]) -> list[HistoryRecordOut]:
    """chat 之外的会话 → 回顾记录（rows 已按创建时间倒序，输出保持同序）。"""
    records: list[HistoryRecordOut] = []
    for row in rows:
        if row.business not in HISTORY_BUSINESSES:
            continue
        snapshot = read_snapshot(checkpointer, row)
        completed = _completed(row, snapshot)
        records.append(
            HistoryRecordOut(
                session_id=row.id,
                business=row.business,
                created_at=row.created_at,
                difficulty=row.difficulty,
                position=row.position,
                target_session_id=row.target_session_id,
                completed=completed,
                preview=_preview(snapshot, completed),
            )
        )
    return records
