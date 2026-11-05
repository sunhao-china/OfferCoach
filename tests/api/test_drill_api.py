"""L3: 练习计划端点 — 鉴权 / 计划 / 今日题单 / 判分 / 进度 / 跨用户隔离（AC4）。

依据 specs/02-in-progress/drill/practice-plan.md 的 AC4。drill 服务装配真实
DrillService（会话库 + 题库同 make_client 的 sqlite 文件），LLM 用桩。
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage
from sqlalchemy import create_engine

from app.api.rate_limit import RateLimiter
from app.drill.service import DrillService
from app.question_bank.store import QuestionStore
from tests.api.test_web_api import auth_headers, make_client
from tests.conftest import StubLLM
from tests.support.graphs import ScriptedChatModel
from tests.support.question_bank import make_record

LONG_ANSWER = (
    "线程池的核心参数有 corePoolSize、maximumPoolSize、keepAliveTime 与 workQueue，拒绝策略有四种"
)


def _score_json(correctness=4, comment="整体不错", strengths=None, weaknesses=None) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "question_id": "q_any",
                    "correctness": correctness,
                    "depth": 4,
                    "expression": 4,
                    "comment": comment,
                    "strengths": strengths or ["结构清晰"],
                    "weaknesses": weaknesses or ["深度不足"],
                }
            ]
        },
        ensure_ascii=False,
    )


def make_drill_client(
    tmp_path, registry, *, responses=(), classify_responses=(), limiter=None, records=None
):
    """make_client + 真实 DrillService 装配（题库与图共用同一 sqlite 文件）。

    responses=判分 LLM 桩（strong）、classify_responses=纠正分类 LLM 桩（cheap）。
    """
    model = ScriptedChatModel(messages=iter([AIMessage(content=text) for text in responses]))
    classify_model = ScriptedChatModel(
        messages=iter([AIMessage(content=text) for text in classify_responses])
    )
    kwargs = {} if records is None else {"records": records}
    client = make_client(
        tmp_path,
        registry,
        llm_classify=StubLLM([]),
        llm_strong=model,
        limiter=limiter,
        **kwargs,
    )
    store = QuestionStore(create_engine(f"sqlite:///{(tmp_path / 'store.db').as_posix()}"))
    client._runtime.drill = DrillService(
        store=store,
        engine=client._runtime.sessions._engine,
        registry=registry,
        llm=model,
        llm_classify=classify_model,
    )
    return client


def _headers(client, username="user1"):
    return auth_headers(client, username)


def _put_plan(client, **overrides):
    body = {"position": "全部", "difficulty": "mixed", "daily_count": 3}
    body.update(overrides)
    return client.put("/api/drill/plan", json=body, headers=_headers(client))


def test_drill_requires_auth(tmp_path, registry):
    """AC4 未登录一律 401。"""
    client = make_drill_client(tmp_path, registry)
    assert client.get("/api/drill/plan").status_code == 401
    assert client.put("/api/drill/plan", json={"difficulty": "medium"}).status_code == 401
    assert client.get("/api/drill/today").status_code == 401
    assert (
        client.post("/api/drill/answer", json={"question_id": "q", "answer": "x"}).status_code
        == 401
    )
    assert (
        client.post("/api/drill/reclassify", json={"question_id": "q"}).status_code == 401
    )
    assert client.get("/api/drill/progress").status_code == 401


def test_drill_plan_put_get_and_validation(tmp_path, registry):
    """AC4 保存/读取/覆盖 + pydantic 校验（难度枚举、题量 1-20）。"""
    client = make_drill_client(tmp_path, registry)
    assert _put_plan(client, difficulty="easy").status_code == 422
    assert _put_plan(client, daily_count=21).status_code == 422
    assert _put_plan(client, daily_count=0).status_code == 422

    response = _put_plan(client, position="Python", daily_count=5)
    assert response.status_code == 200
    assert response.json() == {
        "position": "python_backend",
        "difficulty": "mixed",
        "daily_count": 5,
    }

    got = client.get("/api/drill/plan", headers=_headers(client))
    assert got.status_code == 200 and got.json() == response.json()

    # 覆盖更新 + 「全部」归一化
    response = _put_plan(client, position="全部", difficulty="hard", daily_count=2)
    assert response.json() == {"position": None, "difficulty": "hard", "daily_count": 2}

    # 他人看不到我的计划
    assert client.get("/api/drill/plan", headers=_headers(client, "user2")).json() is None


def test_drill_today_requires_plan(tmp_path, registry):
    """AC4 无计划取今日题单 → 400 明确文案。"""
    client = make_drill_client(tmp_path, registry)
    response = client.get("/api/drill/today", headers=_headers(client))
    assert response.status_code == 400
    assert "练习计划" in response.json()["detail"]


def test_drill_full_flow(tmp_path, registry):
    """AC4 全流程：PUT plan → GET today → answer×N → completed → progress。"""
    client = make_drill_client(
        tmp_path, registry, responses=[_score_json(5, "答得全"), _score_json(2, "缺要点")]
    )
    assert _put_plan(client, daily_count=2).status_code == 200

    today = client.get("/api/drill/today", headers=_headers(client)).json()
    assert today["total"] == 2 and today["done"] == 0
    assert today["completed"] is False and today["shortage"] is False
    assert len(today["remaining"]) == 2
    first = today["remaining"][0]
    assert set(first) == {"question_id", "position", "category", "difficulty", "question"}

    answer = client.post(
        "/api/drill/answer",
        json={"question_id": first["question_id"], "answer": LONG_ANSWER},
        headers=_headers(client),
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["score"] == {"correctness": 5, "depth": 4, "expression": 4}
    assert body["feedback"] == "答得全"
    assert body["strengths"] == ["结构清晰"]
    assert body["reference_points"] == ["要点一", "要点二", "要点三"]
    assert body["completed"] is False
    assert body["progress"] == {"done": 1, "total": 2}

    today = client.get("/api/drill/today", headers=_headers(client)).json()
    assert today["done"] == 1 and len(today["remaining"]) == 1
    assert first["question_id"] not in {row["question_id"] for row in today["remaining"]}

    second = today["remaining"][0]
    answer = client.post(
        "/api/drill/answer",
        json={"question_id": second["question_id"], "answer": LONG_ANSWER},
        headers=_headers(client),
    )
    assert answer.json()["completed"] is True
    assert answer.json()["progress"] == {"done": 2, "total": 2}

    today = client.get("/api/drill/today", headers=_headers(client)).json()
    assert today["completed"] is True and today["remaining"] == []

    progress = client.get("/api/drill/progress", headers=_headers(client)).json()
    assert progress["today"]["completed"] is True
    assert progress["total_attempts"] == 2
    assert progress["total_completed_sets"] == 1
    assert progress["streak"] == 1
    assert progress["plan"]["daily_count"] == 2


def test_drill_answer_idempotent(tmp_path, registry):
    """AC4 重复作答覆盖更新：attempt 不增行、进度不重复计数（题单未完成前重答）。"""
    client = make_drill_client(tmp_path, registry, responses=[_score_json(3), _score_json(5)])
    _put_plan(client, daily_count=2)
    qid = client.get("/api/drill/today", headers=_headers(client)).json()["remaining"][0][
        "question_id"
    ]
    first = client.post(
        "/api/drill/answer",
        json={"question_id": qid, "answer": LONG_ANSWER},
        headers=_headers(client),
    ).json()
    assert first["score"]["correctness"] == 3
    second = client.post(
        "/api/drill/answer",
        json={"question_id": qid, "answer": LONG_ANSWER + "，还有 CallerRunsPolicy 兜底"},
        headers=_headers(client),
    ).json()
    assert second["score"]["correctness"] == 5
    progress = client.get("/api/drill/progress", headers=_headers(client)).json()
    assert progress["total_attempts"] == 1
    assert progress["today"]["done"] == 1


def test_drill_answer_cross_user_404_unified(tmp_path, registry):
    """AC4 跨用户取对方题单题目 404 且与未知题同文案（不泄露存在性）。"""
    client = make_drill_client(tmp_path, registry, responses=[_score_json()])
    _put_plan(client, daily_count=1)
    qid = client.get("/api/drill/today", headers=_headers(client)).json()["remaining"][0][
        "question_id"
    ]

    foreign = client.post(
        "/api/drill/answer",
        json={"question_id": qid, "answer": LONG_ANSWER},
        headers=_headers(client, "user2"),
    )
    unknown = client.post(
        "/api/drill/answer",
        json={"question_id": "q_not_exist", "answer": LONG_ANSWER},
        headers=_headers(client),
    )
    assert foreign.status_code == 404 and unknown.status_code == 404
    assert foreign.json()["detail"] == unknown.json()["detail"] == "题目不存在"


def test_drill_answer_too_short_400(tmp_path, registry):
    """AC4 回答不足 20 字 → 400。"""
    client = make_drill_client(tmp_path, registry, responses=[_score_json()])
    _put_plan(client, daily_count=1)
    qid = client.get("/api/drill/today", headers=_headers(client)).json()["remaining"][0][
        "question_id"
    ]
    response = client.post(
        "/api/drill/answer",
        json={"question_id": qid, "answer": "太短"},
        headers=_headers(client),
    )
    assert response.status_code == 400


def test_drill_answer_parse_failure_502_no_write(tmp_path, registry):
    """AC4 判分解析失败 → 502 且不落库。"""
    client = make_drill_client(tmp_path, registry, responses=["这不是 JSON"])
    _put_plan(client, daily_count=1)
    qid = client.get("/api/drill/today", headers=_headers(client)).json()["remaining"][0][
        "question_id"
    ]
    response = client.post(
        "/api/drill/answer",
        json={"question_id": qid, "answer": LONG_ANSWER},
        headers=_headers(client),
    )
    assert response.status_code == 502
    progress = client.get("/api/drill/progress", headers=_headers(client)).json()
    assert progress["total_attempts"] == 0


def test_drill_answer_rate_limited_per_user(tmp_path, registry):
    """AC4 drill 限流：每用户 20/min，第 21 次 429（与 messages 同机制）。"""
    from tests.support.question_bank import make_record

    responses = [_score_json()] * 20
    client = make_drill_client(
        tmp_path,
        registry,
        responses=responses,
        limiter=RateLimiter(),
        records=[
            make_record("python_basics", seq, position="python_backend") for seq in range(1, 26)
        ],
    )
    _put_plan(client, daily_count=20)
    qids = [
        row["question_id"]
        for row in client.get("/api/drill/today", headers=_headers(client)).json()["remaining"]
    ]
    for i in range(20):
        response = client.post(
            "/api/drill/answer",
            json={"question_id": qids[i], "answer": LONG_ANSWER},
            headers=_headers(client),
        )
        assert response.status_code == 200, f"第 {i + 1} 次应放行"
    blocked = client.post(
        "/api/drill/answer",
        json={"question_id": qids[0], "answer": LONG_ANSWER},
        headers=_headers(client),
    )
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") is not None


# ---- 错题纠正（本规格 AC3） ----


def _reclassify_json(**overrides) -> str:
    item = {"position": "java_backend", "category": "spring"}
    item.update(overrides)
    return json.dumps(item, ensure_ascii=False)


def _reclassify_records():
    return [make_record("python_basics", seq, position="python_backend") for seq in range(1, 3)] + [
        make_record("spring", 1)  # 默认 java_backend：纠正落点岗位
    ]


def test_drill_reclassify_corrects_then_unchanged(tmp_path, registry):
    """AC3 happy path：纠正落库 + 返回 previous；再识别同结果 changed=false。"""
    client = make_drill_client(
        tmp_path,
        registry,
        classify_responses=[_reclassify_json(), _reclassify_json()],
        records=_reclassify_records(),
    )
    _put_plan(client, position="python_backend", daily_count=1)
    today = client.get("/api/drill/today", headers=_headers(client)).json()
    qid = today["remaining"][0]["question_id"]
    assert today["remaining"][0]["position"] == "python_backend"

    response = client.post(
        "/api/drill/reclassify", json={"question_id": qid}, headers=_headers(client)
    )
    assert response.status_code == 200
    assert response.json() == {
        "question_id": qid,
        "position": "java_backend",
        "category": "spring",
        "changed": True,
        "previous": {"position": "python_backend", "category": "python_basics"},
    }
    # 纠正后再识别：结果一致 → changed=false，previous 为纠正后的分类
    again = client.post(
        "/api/drill/reclassify", json={"question_id": qid}, headers=_headers(client)
    ).json()
    assert again["changed"] is False
    assert again["previous"] == {"position": "java_backend", "category": "spring"}


def test_drill_reclassify_cross_user_404_unified(tmp_path, registry):
    """AC3 跨用户/不在题单 404 且与 answer 同文案（不泄露存在性）。"""
    client = make_drill_client(
        tmp_path, registry, classify_responses=[_reclassify_json()], records=_reclassify_records()
    )
    _put_plan(client, daily_count=1)
    qid = client.get("/api/drill/today", headers=_headers(client)).json()["remaining"][0][
        "question_id"
    ]

    foreign = client.post(
        "/api/drill/reclassify", json={"question_id": qid}, headers=_headers(client, "user2")
    )
    unknown = client.post(
        "/api/drill/reclassify", json={"question_id": "q_not_exist"}, headers=_headers(client)
    )
    assert foreign.status_code == 404 and unknown.status_code == 404
    assert foreign.json()["detail"] == unknown.json()["detail"] == "题目不存在"


def test_drill_reclassify_parse_failure_502_no_write(tmp_path, registry):
    """AC3 识别解析失败 → 502 且题库分类不变。"""
    client = make_drill_client(
        tmp_path,
        registry,
        classify_responses=["这不是 JSON"],
        records=_reclassify_records(),
    )
    _put_plan(client, position="python_backend", daily_count=1)
    qid = client.get("/api/drill/today", headers=_headers(client)).json()["remaining"][0][
        "question_id"
    ]
    response = client.post(
        "/api/drill/reclassify", json={"question_id": qid}, headers=_headers(client)
    )
    assert response.status_code == 502
    assert "分类识别" in response.json()["detail"]
    today = client.get("/api/drill/today", headers=_headers(client)).json()
    assert today["remaining"][0]["position"] == "python_backend"  # 未写库


def test_drill_reclassify_rate_limited_per_user(tmp_path, registry):
    """AC3 reclassify 限流：每用户 10/min，第 11 次 429（不触发 LLM 调用）。"""
    client = make_drill_client(
        tmp_path,
        registry,
        classify_responses=[_reclassify_json()] * 10,
        limiter=RateLimiter(),
        records=_reclassify_records(),
    )
    _put_plan(client, position="python_backend", daily_count=1)
    qid = client.get("/api/drill/today", headers=_headers(client)).json()["remaining"][0][
        "question_id"
    ]
    for i in range(10):
        response = client.post(
            "/api/drill/reclassify", json={"question_id": qid}, headers=_headers(client)
        )
        assert response.status_code == 200, f"第 {i + 1} 次应放行"
    blocked = client.post(
        "/api/drill/reclassify", json={"question_id": qid}, headers=_headers(client)
    )
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") is not None
