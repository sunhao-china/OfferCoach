"""L3: 会话列表页后端支撑 — 列表渲染字段（AC1）+ DELETE 端点（行 + 检查点线程清理）。

依据 specs/02-in-progress/web/session-list-page.md 的 AC1-AC4。
"""

from __future__ import annotations

import sqlite3

from langchain_core.messages import AIMessage

from tests.api.test_web_api import (
    FINAL_ANSWER,
    VALID,
    _create,
    _model,
    _send,
    _tool_call,
    auth_headers,
    make_client,
)
from tests.conftest import StubLLM
from tests.support.graphs import ScriptedChatModel


def _threads(tmp_path, session_id: str) -> set[str]:
    """cp.db 中该会话前缀的全部检查点线程 id（checkpoints + writes 两表并集）。"""
    conn = sqlite3.connect(str(tmp_path / "cp.db"))
    try:
        threads: set[str] = set()
        for table in ("checkpoints", "writes"):
            rows = conn.execute(
                f"SELECT DISTINCT thread_id FROM {table} WHERE thread_id LIKE ?",
                (f"{session_id}%",),
            )
            threads.update(row[0] for row in rows)
        return threads
    finally:
        conn.close()


def test_list_provides_page_render_fields(tmp_path, registry):
    """AC1 列表端点返回会话页渲染所需字段：业务/时间/难度/岗位/复盘对象。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    chat_id = _create(client, "chat")
    interview_id = _create(client, "interview", difficulty="entry", position="Java 后端")
    review_id = _create(client, "review", target_session_id=chat_id)
    resume_id = _create(client, "resume")

    sessions = client.get("/api/sessions", headers=auth_headers(client)).json()["sessions"]
    by_id = {s["session_id"]: s for s in sessions}
    assert set(by_id) == {chat_id, interview_id, resume_id, review_id}
    assert all(s["business"] in {"chat", "interview", "resume", "review"} for s in sessions)
    assert all(s["created_at"] for s in sessions)
    assert by_id[interview_id]["difficulty"] == "entry"
    assert by_id[interview_id]["position"] == "Java 后端"
    assert by_id[review_id]["target_session_id"] == chat_id


def test_delete_interview_session_removes_row_and_threads(tmp_path, registry):
    """AC2 删除有消息的 interview 会话：行 404、列表剔除、检查点线程清空。"""
    model = _model("开场。")
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=model)
    session_id = _create(client, "interview")
    _send(client, session_id, "开始")
    assert _threads(tmp_path, session_id) == {f"{session_id}:mock_interview"}

    response = client.delete(f"/api/sessions/{session_id}", headers=auth_headers(client))
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert (
        client.get(f"/api/sessions/{session_id}", headers=auth_headers(client)).status_code == 404
    )
    assert (
        client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "x"},
            headers=auth_headers(client),
        ).status_code
        == 404
    )
    listed = client.get("/api/sessions", headers=auth_headers(client)).json()["sessions"]
    assert all(s["session_id"] != session_id for s in listed)
    assert _threads(tmp_path, session_id) == set()


def test_delete_chat_session_removes_root_and_derived_threads(tmp_path, registry):
    """AC2 chat 经根图路由（react 工具轮）：根线程 + R6 派生 {sid}:react_agent 一并清理。"""
    classify = StubLLM([VALID])
    model = ScriptedChatModel(messages=iter([_tool_call(), AIMessage(content=FINAL_ANSWER)]))
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    session_id = _create(client, "chat")
    _send(client, session_id, "讲讲线程池")
    assert _threads(tmp_path, session_id) == {session_id, f"{session_id}:react_agent"}

    deleted = client.delete(f"/api/sessions/{session_id}", headers=auth_headers(client))
    assert deleted.status_code == 200
    assert _threads(tmp_path, session_id) == set()
    assert (
        client.get(f"/api/sessions/{session_id}", headers=auth_headers(client)).status_code == 404
    )


def test_delete_blocked_while_review_references(tmp_path, registry):
    """AC3 被 review 引用时 409（行与检查点俱在）；先删 review 再删 target 均成功。"""
    model = _model("开场。")
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=model)
    interview_id = _create(client, "interview")
    _send(client, interview_id, "开始")
    review_id = _create(client, "review", target_session_id=interview_id)

    blocked = client.delete(f"/api/sessions/{interview_id}", headers=auth_headers(client))
    assert blocked.status_code == 409
    assert (
        client.get(f"/api/sessions/{interview_id}", headers=auth_headers(client)).status_code == 200
    )
    assert _threads(tmp_path, interview_id) == {f"{interview_id}:mock_interview"}

    # review 删除只清复盘自身线程（f"{target}:interview_review"），不动复盘对象
    assert (
        client.delete(f"/api/sessions/{review_id}", headers=auth_headers(client)).status_code == 200
    )
    assert _threads(tmp_path, interview_id) == {f"{interview_id}:mock_interview"}

    assert (
        client.delete(f"/api/sessions/{interview_id}", headers=auth_headers(client)).status_code
        == 200
    )
    assert _threads(tmp_path, interview_id) == set()


def test_delete_unknown_session_404(tmp_path, registry):
    """AC4 删除不存在的会话 → 404。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    assert client.delete("/api/sessions/nope", headers=auth_headers(client)).status_code == 404
