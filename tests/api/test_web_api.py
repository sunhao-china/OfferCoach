"""L3: Web API 层 — 会话创建 / 直连不经路由 / chat 路由 / 历史 / 会话隔离 / 跨页复盘。

依据 specs/02-in-progress/api/web-api.md 的 AC1-AC6。
"""

from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import create_engine

from app.api.app import create_app
from app.api.auth_service import AuthService
from app.api.captcha import CaptchaStore
from app.api.deps import Runtime
from app.api.session_store import SessionStore
from app.composition import build_app_graph, build_subgraphs
from tests.conftest import StubLLM
from tests.support.graphs import ScriptedChatModel, judge_json, score_json
from tests.support.question_bank import make_record, make_store

BANK_RECORDS = [
    make_record("java_basics", 1, difficulty="entry", question="谈谈面向对象的三大特性"),
    make_record(
        "concurrent",
        1,
        difficulty="entry",
        qtype="knowledge",
        question="谈谈线程池的核心参数与拒绝策略",
    ),
    make_record("jvm", 1, difficulty="medium", question="JVM 运行时数据区有哪些"),
]

VALID = '{"intent": "knowledge_qa", "confidence": 0.9, "slots": {}}'
FINAL_ANSWER = "线程池的核心参数包括 corePoolSize、maximumPoolSize 等。"

# mock_interview 脚本（与 test_interview_review 夹具同序列，实证可用）：
# 开场 / judge move_on / score(1) / judge move_on / score(1) / 报告
INTERVIEW_SCRIPT = [
    "开场。",
    judge_json("move_on"),
    score_json(1),
    judge_json("move_on"),
    score_json(1),
    "报告。",
]

REVIEWS_JSON = json.dumps(
    {
        "reviews": [
            {
                "question_id": "fake_1",
                "comment": "知识点完整但缺少结构。",
                "strengths": ["概念准确"],
                "weaknesses": ["表达松散"],
            },
            {
                "question_id": "fake_2",
                "comment": "参数记得全，追问深度不足。",
                "strengths": ["覆盖面广"],
                "weaknesses": ["缺少调优经验"],
            },
        ]
    },
    ensure_ascii=False,
)

EXTRACT_JSON = json.dumps(
    {
        "skills": ["Java", "MySQL"],
        "years": "2 年",
        "education": "本科",
        "experience": ["某公司后端开发"],
        "projects": ["订单系统"],
    },
    ensure_ascii=False,
)
ANALYZE_JSON = json.dumps(
    {
        "overall": 4,
        "strengths": ["技术栈匹配"],
        "weaknesses": ["缺少量化结果"],
        "suggestions": ["用 STAR 结构改写项目描述"],
    },
    ensure_ascii=False,
)


def _tool_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_question_bank",
                "args": {"keywords": "线程池"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )


def make_client(
    tmp_path,
    registry,
    *,
    llm_classify,
    llm_strong,
    records=BANK_RECORDS,
    bus=None,
    limiter=None,
    auth=None,
    memory=None,
):
    """构建真实组合层 + sqlite 检查点 + 会话表 + 认证服务的测试客户端（LLM 用桩）。

    limiter 缺省 None（限流关闭，既有测试零行为）；auth 缺省按常规构造（限流
    测试注入计数子类验证依赖缓存）；memory 缺省 None（三层记忆关闭，零行为），
    runtime 挂在 client._runtime 上供记忆测试装配 MemoryService。
    """
    checkpointer = SqliteSaver(
        sqlite3.connect(str(tmp_path / "cp.db"), check_same_thread=False)
    )
    checkpointer.setup()
    store = make_store(tmp_path, records)
    subgraphs = build_subgraphs(
        store=store,
        registry=registry,
        llm_classify=llm_classify,
        llm_strong=llm_strong,
        checkpointer=checkpointer,
        bus=bus,
        memory=memory,
        max_questions=2,
        score_every=1,
    )
    root = build_app_graph(
        store=store,
        registry=registry,
        llm_classify=llm_classify,
        llm_strong=llm_strong,
        checkpointer=checkpointer,
        bus=bus,
        memory=memory,
        subgraphs=subgraphs,
    )
    sessions_engine = create_engine(f"sqlite:///{(tmp_path / 'sessions.db').as_posix()}")
    sessions = SessionStore(sessions_engine)  # create_all 顺带建 auth 三表
    captcha = CaptchaStore()
    if auth is None:
        auth = AuthService(sessions_engine, captcha=captcha)
    runtime = Runtime(
        root=root,
        subgraphs=subgraphs,
        checkpointer=checkpointer,
        sessions=sessions,
        auth=auth,
        captcha=captcha,
        limiter=limiter,
        memory=memory,
    )
    client = TestClient(create_app(runtime))
    client._runtime = runtime  # 记忆测试需取 checkpointer/sessions 装配服务
    return client


def _captcha(client) -> dict[str, str]:
    """取一张验证码并自动解出答案（SVG 文本可读，测试直读 <text> 字符）。"""
    body = client.get("/api/auth/captcha").json()
    code = "".join(
        part.split(">", 1)[1].split("<", 1)[0]
        for part in body["image"].split("<text ")[1:]  # 跳过 rect/噪线前缀段
    )
    assert len(code) == 4
    return {"captcha_id": body["captcha_id"], "captcha_code": code}


def auth_headers(client, username="user1") -> dict[str, str]:
    """注册（重复注册忽略 409）+ 登录 username，返回 Authorization 头。

    按 client 实例缓存（PBKDF2 600k 迭代较贵，同 client 复用同一 token）。
    """
    cache = getattr(client, "_auth_headers", None)
    if cache is None:
        cache = client._auth_headers = {}
    cached = cache.get(username)
    if cached is not None:
        return cached
    client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass1234", **_captcha(client)},
    )
    response = client.post("/api/auth/login", json={"username": username, "password": "pass1234"})
    assert response.status_code == 200
    cached = cache[username] = {"Authorization": f"Bearer {response.json()['token']}"}
    return cached


def _create(client, business, **extra) -> str:
    response = client.post(
        "/api/sessions", json={"business": business, **extra}, headers=auth_headers(client)
    )
    assert response.status_code == 200
    return response.json()["session_id"]


def _send(client, session_id, content):
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": content},
        headers=auth_headers(client),
    )
    assert response.status_code == 200
    return response.json()["reply"]


def _model(*texts: str) -> ScriptedChatModel:
    """llm_strong 桩：必须可被 create_react_agent 当作 Runnable（构建时校验）。"""
    return ScriptedChatModel(messages=iter([AIMessage(content=text) for text in texts]))


def test_healthz(tmp_path, registry):
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_sessions_all_businesses(tmp_path, registry):
    """AC1 四种 business 均可创建（需登录）；review 缺 target → 422，target 不存在 → 404。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    headers = auth_headers(client)
    for business in ("chat", "interview", "resume"):
        response = client.post("/api/sessions", json={"business": business}, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["business"] == business
        assert body["user_id"] == "user1"  # 身份取自 token，请求体无法指定
        assert body["session_id"]
        assert client.get(f"/api/sessions/{body['session_id']}", headers=headers).status_code == 200
    missing = client.post("/api/sessions", json={"business": "review"}, headers=headers)
    assert missing.status_code == 422
    unknown = client.post(
        "/api/sessions",
        json={"business": "review", "target_session_id": "nope"},
        headers=headers,
    )
    assert unknown.status_code == 404


def test_interview_session_direct_no_router(tmp_path, registry):
    """AC2 interview 会话直连 mock_interview：回复来自面试子图，分类器零调用。"""
    classify = StubLLM([])
    model = _model(*INTERVIEW_SCRIPT)
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    session_id = _create(client, "interview")
    first = _send(client, session_id, "开始")
    assert "第 1 题" in first  # 直连默认难度 medium，抽到题库中等题
    _send(client, session_id, "答1")
    _send(client, session_id, "答2")
    assert classify.calls == []  # 直连路径不经过路由（分类器从未被调用）


def test_resume_session_direct(tmp_path, registry):
    """AC2 resume 会话直连 resume_analysis 一次性管道：返回完整报告。"""
    classify = StubLLM([])
    llm = _model(EXTRACT_JSON, ANALYZE_JSON, "报告：加强高并发描述。")
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=llm)
    session_id = _create(client, "resume")
    reply = _send(client, session_id, "李四，2 年 Java 后端，做过订单系统")
    assert "报告" in reply
    assert classify.calls == []


def test_chat_session_uses_router(tmp_path, registry):
    """AC3 chat 会话走根图路由：分类器一次调用 + ReAct 工具调用后作答。"""
    classify = StubLLM([VALID])
    model = ScriptedChatModel(messages=iter([_tool_call(), AIMessage(content=FINAL_ANSWER)]))
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    session_id = _create(client, "chat")
    reply = _send(client, session_id, "讲讲线程池")
    assert reply == FINAL_ANSWER
    assert len(classify.calls) == 1
    # 历史不含 ReAct 中间步（工具调用 AIMessage），只保留用户可见的问答
    history = client.get(
        f"/api/sessions/{session_id}/messages", headers=auth_headers(client)
    ).json()["messages"]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[1]["content"] == FINAL_ANSWER


def test_history_reads_back_interview_messages(tmp_path, registry):
    """AC4 历史接口从 checkpointer 读回会话双方消息。"""
    classify = StubLLM([])
    model = _model(*INTERVIEW_SCRIPT)
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    session_id = _create(client, "interview")
    _send(client, session_id, "开始")
    history = client.get(
        f"/api/sessions/{session_id}/messages", headers=auth_headers(client)
    ).json()["messages"]
    assert [item["role"] for item in history][:2] == ["user", "assistant"]
    assert history[0]["content"] == "开始"
    assert history[1]["content"] == "开场。"


def test_chat_sessions_isolated(tmp_path, registry):
    """AC5 两个 chat 会话消息互不串（checkpointer 按 thread_id 隔离）。"""
    classify = StubLLM([VALID, VALID])
    script = iter([_tool_call(), AIMessage(content="答A"), _tool_call(), AIMessage(content="答B")])
    model = ScriptedChatModel(messages=script)
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    first = _create(client, "chat")
    second = _create(client, "chat")
    assert _send(client, first, "线程池") == "答A"
    assert _send(client, second, "线程池") == "答B"
    first_history = client.get(
        f"/api/sessions/{first}/messages", headers=auth_headers(client)
    ).json()["messages"]
    first_texts = [item["content"] for item in first_history]
    assert "答A" in first_texts and "答B" not in first_texts


def test_review_targets_interview_session(tmp_path, registry, bus):
    """AC6 跨页复盘：interview 会话跑到 done 后，review 会话（target=该会话）产出报告。

    覆盖双 ns 兜底读取：API 直连的面试检查点落 ns=""，复盘 load 依次尝试
    ns="mock_interview" 与 ns=""。
    """
    classify = StubLLM([])
    # 直连面试默认难度 medium，题库仅 1 道中等题：ask(开场)+judge+score+ask(耗尽) 恰好
    # 消费脚本前 4 条，随后直接结束（第三轮 judge 不再调用）；第 5 条起是复盘的
    # per_question / summary 输出
    model = _model(*INTERVIEW_SCRIPT[:4], REVIEWS_JSON, "复盘报告：改进计划……")
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model, bus=bus)
    interview_id = _create(client, "interview")
    _send(client, interview_id, "开始")
    _send(client, interview_id, "答1")
    _send(client, interview_id, "答2")
    review_id = _create(client, "review", target_session_id=interview_id)
    reply = _send(client, review_id, "帮我复盘一下")
    assert "复盘报告" in reply
    events = bus.events["interview_review.complete"]
    assert len(events) == 1
    assert events[0].payload["session_id"] == interview_id
    assert classify.calls == []


def test_interview_session_options_roundtrip(tmp_path, registry):
    """interview 会话可显式指定难度/岗位：SessionOut 与 GET 回显；非法难度 422。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    headers = auth_headers(client)
    response = client.post(
        "/api/sessions",
        json={"business": "interview", "difficulty": "entry", "position": "Java 后端"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["difficulty"] == "entry"
    assert body["position"] == "Java 后端"
    fetched = client.get(f"/api/sessions/{body['session_id']}", headers=headers).json()
    assert fetched["difficulty"] == "entry"
    assert fetched["position"] == "Java 后端"
    bad = client.post(
        "/api/sessions", json={"business": "interview", "difficulty": "impossible"},
        headers=headers,
    )
    assert bad.status_code == 422


def test_interview_session_entry_difficulty_label(tmp_path, registry):
    """entry 难度会话直连生效：首答带「入门」标签，分类器零调用。"""
    classify = StubLLM([])
    model = _model("开场。")  # opening 恰消费 1 条，首答来自 ask（不调 LLM）
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=model)
    session_id = _create(client, "interview", difficulty="entry")
    first = _send(client, session_id, "开始")
    assert "第 1 题（入门）" in first
    assert classify.calls == []


def test_list_sessions_per_identity_and_order(tmp_path, registry):
    """会话列表：只返回当前登录用户会话（倒序），不接受 user_id 参数；新用户列表为空。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    chat_id = _create(client, "chat")
    interview_id = _create(client, "interview")
    resume_id = _create(client, "resume")

    headers = auth_headers(client)
    mine = client.get("/api/sessions", headers=headers).json()["sessions"]
    assert [s["session_id"] for s in mine] == [resume_id, interview_id, chat_id]
    assert all(s["user_id"] == "user1" for s in mine)

    # user_id 查询参数已废弃：忽略，仍只返回当前用户会话
    ignored = client.get("/api/sessions", params={"user_id": "user2"}, headers=headers)
    assert [s["session_id"] for s in ignored.json()["sessions"]] == [
        resume_id, interview_id, chat_id
    ]

    # 另一个账号看不到 user1 的会话
    other_headers = auth_headers(client, "user2")
    assert client.get("/api/sessions", headers=other_headers).json()["sessions"] == []
