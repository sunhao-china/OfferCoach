"""L3: GET /api/history 历史回顾端点 — 鉴权 / 业务过滤 / 完成态与预览。

依据 specs/02-in-progress/web/history-review-page.md 的 AC2。
"""

from __future__ import annotations

from tests.api.test_web_api import (
    ANALYZE_JSON,
    EXTRACT_JSON,
    INTERVIEW_SCRIPT,
    REVIEWS_JSON,
    _create,
    _model,
    _send,
    auth_headers,
    make_client,
)
from tests.conftest import StubLLM


def _records(client, username="user1"):
    return client.get("/api/history", headers=auth_headers(client, username)).json()["records"]


def test_history_requires_auth(tmp_path, registry):
    """AC2 未登录 401。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    assert client.get("/api/history").status_code == 401


def test_history_excludes_chat_and_empty_sessions(tmp_path, registry):
    """AC2 空会话与 chat 会话不进回顾页；记录形状完整。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    _create(client, "chat")
    _create(client, "interview", difficulty="entry", position="Java 后端")
    _create(client, "resume")

    records = _records(client)
    by_business = {record["business"] for record in records}
    assert by_business == {"interview", "resume"}
    interview = next(record for record in records if record["business"] == "interview")
    assert interview["difficulty"] == "entry"
    assert interview["position"] == "Java 后端"
    assert interview["completed"] is False
    assert interview["preview"] == ""
    assert all("created_at" in record for record in records)


def test_history_completed_interview_with_preview(tmp_path, registry):
    """AC2 完整面试（entry 2 题出报告）→ completed=True + preview 为报告开头。"""
    model = _model(*INTERVIEW_SCRIPT)
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=model)
    session_id = _create(client, "interview", difficulty="entry")
    _send(client, session_id, "开始")
    _send(client, session_id, "答1")
    _send(client, session_id, "答2")

    records = _records(client)
    assert len(records) == 1
    assert records[0]["session_id"] == session_id
    assert records[0]["completed"] is True
    assert records[0]["preview"] == "报告。"


def test_history_unfinished_interview_not_completed(tmp_path, registry):
    """AC2 只开场的面试 → completed=False、preview 空。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model("开场。"))
    session_id = _create(client, "interview")
    _send(client, session_id, "开始")

    records = _records(client)
    assert records[0]["completed"] is False
    assert records[0]["preview"] == ""


def test_history_resume_and_review_records(tmp_path, registry):
    """AC2 resume 一次管道即 completed；review 记录含 target_session_id 透传。"""
    model = _model(
        *INTERVIEW_SCRIPT[:4],
        REVIEWS_JSON,
        "复盘报告：改进计划……",
        EXTRACT_JSON,
        ANALYZE_JSON,
        "报告：加强高并发描述。",
    )
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=model)
    interview_id = _create(client, "interview")
    _send(client, interview_id, "开始")
    _send(client, interview_id, "答1")
    _send(client, interview_id, "答2")
    review_id = _create(client, "review", target_session_id=interview_id)
    _send(client, review_id, "帮我复盘一下")
    resume_id = _create(client, "resume")
    _send(client, resume_id, "李四，2 年 Java 后端，做过订单系统")

    records = _records(client)
    by_id = {record["session_id"]: record for record in records}
    # 默认 medium 题库仅 1 题：答 1 后耗尽直接出报告（phase=done）
    assert by_id[interview_id]["completed"] is True
    assert by_id[interview_id]["preview"]
    assert by_id[review_id]["completed"] is True
    assert by_id[review_id]["target_session_id"] == interview_id
    assert by_id[review_id]["preview"] == "复盘报告：改进计划……"
    assert by_id[resume_id]["completed"] is True
    assert by_id[resume_id]["preview"] == "报告：加强高并发描述。"


def test_history_scoped_to_user(tmp_path, registry):
    """AC2 只返回本人记录（user2 看不到 user1 的会话）。"""
    client = make_client(
        tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model("开场。", "开场。")
    )
    own_id = _create(client, "interview")
    _send(client, own_id, "开始")
    # user2 走同一 runtime 但用自己的 token 建会话/发消息
    headers2 = auth_headers(client, "user2")
    created = client.post("/api/sessions", json={"business": "interview"}, headers=headers2)
    assert created.status_code == 200
    user2_own = created.json()["session_id"]
    sent = client.post(
        f"/api/sessions/{user2_own}/messages", json={"content": "开始"}, headers=headers2
    )
    assert sent.status_code == 200

    records = _records(client, "user2")
    assert [record["session_id"] for record in records] == [user2_own]
    records_user1 = _records(client, "user1")
    assert [record["session_id"] for record in records_user1] == [own_id]
