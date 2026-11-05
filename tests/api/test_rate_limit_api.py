"""L3: 接口限流 — IP 级（login/captcha/register）+ 用户级（messages）429 与隔离。

依据 specs/02-in-progress/web/token-expiry-rate-limit.md 的 AC3。
"""

from __future__ import annotations

from sqlalchemy import create_engine

from app.api.auth_service import AuthService
from app.api.rate_limit import DEFAULT_RULES, RateLimiter
from app.api.session_store import SessionStore
from tests.api.test_web_api import VALID, _create, _model, auth_headers, make_client
from tests.conftest import StubLLM


class CountingAuth(AuthService):
    """计数 authenticate 调用次数的子类：验证依赖缓存（每请求只认证一次）。"""

    calls = 0

    def authenticate(self, token: str) -> str | None:
        type(self).calls += 1
        return super().authenticate(token)


def make_limited_client(tmp_path, registry, **kwargs):
    return make_client(
        tmp_path,
        registry,
        llm_classify=StubLLM([VALID] * 64),
        llm_strong=_model(*["回复。"] * 32),
        limiter=RateLimiter(rules=DEFAULT_RULES),
        **kwargs,
    )


def test_login_rate_limited_per_ip_after_20(tmp_path, registry):
    client = make_limited_client(tmp_path, registry)
    for _ in range(20):  # 密码全错也照计（中间触发锁定/验证码都不影响计数）
        response = client.post(
            "/api/auth/login", json={"username": "ghost", "password": "whatever"}
        )
        assert response.status_code == 401
    response = client.post(
        "/api/auth/login", json={"username": "ghost", "password": "whatever"}
    )
    assert response.status_code == 429  # 第 21 次超限
    assert int(response.headers["Retry-After"]) >= 1


def test_captcha_rate_limited_per_ip_after_30(tmp_path, registry):
    client = make_limited_client(tmp_path, registry)
    for _ in range(30):
        assert client.get("/api/auth/captcha").status_code == 200
    response = client.get("/api/auth/captcha")
    assert response.status_code == 429


def test_register_rate_limited_per_ip_after_10(tmp_path, registry):
    client = make_limited_client(tmp_path, registry)
    for index in range(10):  # 验证码乱填：前 10 次 400（计数照记），不真正建号
        response = client.post(
            "/api/auth/register",
            json={
                "username": f"flood_{index}",
                "password": "secret123",
                "captcha_id": "x",
                "captcha_code": "bad",
            },
        )
        assert response.status_code == 400
    response = client.post(
        "/api/auth/register",
        json={
            "username": "flood_last",
            "password": "secret123",
            "captcha_id": "x",
            "captcha_code": "bad",
        },
    )
    assert response.status_code == 429


def test_messages_rate_limited_per_user_others_unaffected(tmp_path, registry):
    client = make_limited_client(tmp_path, registry)
    headers = auth_headers(client, "user1")
    auth_headers(client, "user2")  # 预注册第二个用户
    session_id = _create(client, "chat")

    for _ in range(20):  # 每用户 20/min
        response = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "问题", "stream": False},
            headers=headers,
        )
        assert response.status_code == 200
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "问题", "stream": False},
        headers=headers,
    )
    assert response.status_code == 429  # user1 第 21 次超限

    other_headers = auth_headers(client, "user2")
    created = client.post(
        "/api/sessions", json={"business": "chat"}, headers=other_headers
    )
    assert created.status_code == 200
    response = client.post(
        f"/api/sessions/{created.json()['session_id']}/messages",
        json={"content": "问题", "stream": False},
        headers=other_headers,
    )
    assert response.status_code == 200  # user2 不受 user1 限流影响


def test_unauthenticated_message_still_401_with_limiter_on(tmp_path, registry):
    client = make_limited_client(tmp_path, registry)
    response = client.post(
        "/api/sessions/nonexistent/messages",
        json={"content": "问题"},
        headers={"Authorization": "Bearer garbage"},
    )
    assert response.status_code == 401  # 鉴权先于 per-user 限流，行为不变


def test_authenticate_runs_once_per_request(tmp_path, registry):
    """per-user 限流依赖复用 get_current_user：FastAPI 依赖缓存保证单次认证。"""
    sessions_engine = create_engine(f"sqlite:///{(tmp_path / 'sessions.db').as_posix()}")
    SessionStore(sessions_engine)  # create_all 建 auth 表
    auth = CountingAuth(sessions_engine)
    client = make_limited_client(tmp_path, registry, auth=auth)
    headers = auth_headers(client, "user1")
    session_id = _create(client, "chat")
    CountingAuth.calls = 0
    client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "问题", "stream": False},
        headers=headers,
    )
    assert CountingAuth.calls == 1  # 端点 + 限流依赖共用同一次认证
