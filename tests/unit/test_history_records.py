"""L1: build_history_records 记录派生 — completed 判定 / preview 截断 / 排序与过滤。

依据 specs/02-in-progress/web/history-review-page.md 的 AC1。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from app.api.history import PREVIEW_CHARS, build_history_records


def _snapshot(messages: list, phase: str | None = None) -> SimpleNamespace:
    state: dict = {"messages": messages}
    if phase is not None:
        state["phase"] = phase
    return SimpleNamespace(checkpoint={"channel_values": state})


def _row(business: str, id: str | None = None, **extra) -> SimpleNamespace:
    fields = {
        "id": id or f"{business}-1",
        "business": business,
        "created_at": datetime(2026, 10, 25, 12, 0),
        "difficulty": None,
        "position": None,
        "target_session_id": None,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def _checkpointer(snapshots: dict[tuple[str, str], SimpleNamespace]):
    class Fake:
        def get_tuple(self, config):
            key = (
                config["configurable"]["thread_id"],
                config["configurable"]["checkpoint_ns"],
            )
            return snapshots.get(key)

    return Fake()


def test_interview_done_completed_with_preview():
    """AC1 interview phase=done → completed + preview 取最后一条 AI 消息。"""
    snapshot = _snapshot(
        [HumanMessage(content="开始"), AIMessage(content="第一题"), AIMessage(content="总结报告")],
        phase="done",
    )
    checkpointer = _checkpointer({("s1:mock_interview", "mock_interview"): snapshot})
    records = build_history_records(checkpointer, [_row("interview", id="s1")])
    assert len(records) == 1
    assert records[0].completed is True
    assert records[0].preview == "总结报告"


def test_interview_answering_not_completed_no_preview():
    """AC1 phase=answering（只开场）→ completed=False、preview 空。"""
    snapshot = _snapshot(
        [HumanMessage(content="开始"), AIMessage(content="第一题")], phase="answering"
    )
    checkpointer = _checkpointer({("s1:mock_interview", "mock_interview"): snapshot})
    records = build_history_records(checkpointer, [_row("interview", id="s1")])
    assert records[0].completed is False
    assert records[0].preview == ""


def test_interview_snapshot_ns_fallback_empty():
    """AC1 直连路径落 ns=""（双 ns 兜底）；无快照 → 不崩、未完成。"""
    snapshot = _snapshot([AIMessage(content="报告")], phase="done")
    checkpointer = _checkpointer({("s1:mock_interview", ""): snapshot})
    records = build_history_records(checkpointer, [_row("interview", id="s1")])
    assert records[0].completed is True

    empty = build_history_records(_checkpointer({}), [_row("interview", id="s2")])
    assert empty[0].completed is False
    assert empty[0].preview == ""


def test_resume_review_completed_by_ai_message():
    """AC1 resume/review：有 AI 消息即 completed；无消息未完成。"""
    checkpointer = _checkpointer(
        {
            ("r1:resume_analysis", ""): _snapshot(
                [HumanMessage(content="简历"), AIMessage(content="分析报告")]
            ),
            ("t1:interview_review", ""): _snapshot([HumanMessage(content="复盘")]),
        }
    )
    rows = [_row("resume", id="r1"), _row("review", id="t1", target_session_id="x")]
    records = build_history_records(checkpointer, rows)
    assert records[0].completed is True
    assert records[0].preview == "分析报告"
    assert records[1].completed is False
    assert records[1].preview == ""


def test_preview_truncates_to_120_chars():
    """AC1 preview 截断 120 字 + 省略号。"""
    long_report = "报" * 150
    snapshot = _snapshot([AIMessage(content=long_report)], phase="done")
    checkpointer = _checkpointer({("s1:mock_interview", ""): snapshot})
    records = build_history_records(checkpointer, [_row("interview", id="s1")])
    assert records[0].preview == "报" * PREVIEW_CHARS + "…"


def test_preview_skips_empty_ai_messages():
    """AC1 最后一条 AI 消息为空时回退到上一条（ReAct 空块防护）。"""
    snapshot = _snapshot([AIMessage(content=""), AIMessage(content="真实报告")], phase="done")
    checkpointer = _checkpointer({("s1:mock_interview", ""): snapshot})
    records = build_history_records(checkpointer, [_row("interview", id="s1")])
    assert records[0].preview == "真实报告"


def test_chat_rows_skipped_and_order_preserved():
    """AC1 chat 不进回顾页；输出保持输入倒序。"""
    rows = [
        _row("interview", id="s1", created_at=datetime(2026, 10, 25, 13, 0)),
        _row("chat", id="c1"),
        _row("interview", id="s2", created_at=datetime(2026, 10, 25, 12, 0)),
    ]
    checkpointer = _checkpointer(
        {
            ("s1:mock_interview", ""): _snapshot([AIMessage(content="A")], phase="done"),
            ("s2:mock_interview", ""): _snapshot([AIMessage(content="B")], phase="done"),
        }
    )
    records = build_history_records(checkpointer, rows)
    assert [record.session_id for record in records] == ["s1", "s2"]


def test_record_metadata_roundtrip():
    """AC1 difficulty/position/target_session_id 透传到记录。"""
    row = _row(
        "interview",
        id="s1",
        difficulty="hard",
        position="C++ 后端",
        target_session_id=None,
    )
    checkpointer = _checkpointer(
        {("s1:mock_interview", ""): _snapshot([AIMessage(content="报告")], phase="done")}
    )
    records = build_history_records(checkpointer, [row])
    assert records[0].difficulty == "hard"
    assert records[0].position == "C++ 后端"
    assert records[0].created_at == row.created_at
