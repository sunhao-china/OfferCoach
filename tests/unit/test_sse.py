"""L1: SSE 工具 — 终稿分块与事件帧格式。

依据 specs/02-in-progress/api/sse-streaming.md 的 AC1。
"""

from __future__ import annotations

import json

from app.api.sse import CHUNK_SIZE, chunk_text, sse_data


def test_chunk_text_empty():
    assert chunk_text("") == []


def test_chunk_text_shorter_than_size():
    assert chunk_text("短回复") == ["短回复"]


def test_chunk_text_exact_multiple():
    text = "字" * (CHUNK_SIZE * 2)
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert all(len(chunk) == CHUNK_SIZE for chunk in chunks)
    assert "".join(chunks) == text


def test_chunk_text_cjk_never_splits_inside_char():
    text = "面" * (CHUNK_SIZE * 2 + 7)
    chunks = chunk_text(text)
    assert "".join(chunks) == text
    assert all(0 < len(chunk) <= CHUNK_SIZE for chunk in chunks)
    assert len(chunks[-1]) == 7


def test_sse_data_frame_format():
    frame = sse_data({"content": "你好"})
    assert frame == 'data: {"content": "你好"}\n\n'
    assert json.loads(frame.removeprefix("data: ").strip()) == {"content": "你好"}
