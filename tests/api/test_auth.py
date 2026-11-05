"""L4: 登录认证 — 注册/登录/登出契约 + 保护面 + 身份绑定 + 会话隔离。

依据 specs/02-in-progress/web/auth-login.md 的 AC1-AC4。
"""

from __future__ import annotations

from tests.api.test_web_api import _captcha, _model, auth_headers, make_client
from tests.conftest import StubLLM


def _register(client, username, password="secret12"):
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, **_captcha(client)},
    )
    assert response.status_code == 201
    return response.json()["token"]


def test_register_login_me_logout_contract(tmp_path, registry):
    """AC1：注册 201 + token；重名 409；非法输入 422；登录/登出/me。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    registered = client.post(
        "/api/auth/register", json={"username": "alice", "password": "secret12", **_captcha(client)}
    )
    assert registered.status_code == 201
    token = registered.json()["token"]
    assert registered.json()["username"] == "alice"
    assert len(token) > 20

    duplicate = client.post(
        "/api/auth/register", json={"username": "alice", "password": "x1234567", **_captcha(client)}
    )
    assert duplicate.status_code == 409
    short_name = client.post(
        "/api/auth/register", json={"username": "ab", "password": "secret1"}
    )
    assert short_name.status_code == 422
    short_password = client.post(
        "/api/auth/register", json={"username": "bob", "password": "123"}
    )
    assert short_password.status_code == 422

    wrong = client.post("/api/auth/login", json={"username": "alice", "password": "wrongpass"})
    nobody = client.post("/api/auth/login", json={"username": "nobody", "password": "secret1"})
    assert wrong.status_code == 401
    assert nobody.status_code == 401
    assert wrong.json()["detail"] == nobody.json()["detail"]  # 防枚举：同文案

    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=headers).json() == {"username": "alice"}
    logout = client.post("/api/auth/logout", headers=headers)
    assert logout.status_code == 200 and logout.json()["ok"] is True
    assert client.get("/api/auth/me", headers=headers).status_code == 401  # 旧 token 失效
    assert client.post("/api/auth/logout", headers=headers).status_code == 401


def test_protected_endpoints_require_token(tmp_path, registry):
    """AC2：业务端点无 token/坏 token → 401；/healthz 与静态页公开。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    calls = [
        ("post", "/api/sessions", {"json": {"business": "chat"}}),
        ("get", "/api/sessions", {}),
        ("get", "/api/sessions/whatever", {}),
        ("delete", "/api/sessions/whatever", {}),
        ("post", "/api/sessions/whatever/messages", {"json": {"content": "hi"}}),
        ("get", "/api/sessions/whatever/messages", {}),
        ("post", "/api/sessions/whatever/files", {}),
    ]
    for method, path, kwargs in calls:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, f"{method} {path}"
    bad = client.get("/api/sessions", headers={"Authorization": "Bearer not-a-token"})
    assert bad.status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200


def test_identity_binds_to_token_not_body(tmp_path, registry):
    """AC3：建会话 user_id 恒为 token 身份；请求体塞 user_id 被忽略（pydantic 丢字段）。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    headers = auth_headers(client, "alice")
    response = client.post(
        "/api/sessions",
        json={"business": "chat", "user_id": "evil-override"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == "alice"


def test_cross_user_isolation(tmp_path, registry):
    """AC4：B 看不到/访问不了 A 的会话（404 同文案）；跨账号 review target → 404。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    alice = auth_headers(client, "alice")
    bob = auth_headers(client, "bob")
    session_id = client.post(
        "/api/sessions", json={"business": "chat"}, headers=alice
    ).json()["session_id"]

    assert client.get(f"/api/sessions/{session_id}", headers=bob).status_code == 404
    assert client.get(f"/api/sessions/{session_id}/messages", headers=bob).status_code == 404
    assert client.delete(f"/api/sessions/{session_id}", headers=bob).status_code == 404
    assert (
        client.post(
            f"/api/sessions/{session_id}/messages", json={"content": "hi"}, headers=bob
        ).status_code
        == 404
    )
    review = client.post(
        "/api/sessions",
        json={"business": "review", "target_session_id": session_id},
        headers=bob,
    )
    assert review.status_code == 404  # 不能复盘别人的面试

    # A 本人正常访问
    assert client.get(f"/api/sessions/{session_id}", headers=alice).status_code == 200


def test_upload_requires_ownership(tmp_path, registry):
    """AC4：B 不能向 A 的 resume 会话上传文件（归属校验先于扩展名校验）。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    alice = auth_headers(client, "alice")
    bob = auth_headers(client, "bob")
    session_id = client.post(
        "/api/sessions", json={"business": "resume"}, headers=alice
    ).json()["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/files",
        files={"file": ("resume.txt", b"hello", "text/plain")},
        headers=bob,
    )
    assert response.status_code == 404


# ---------- 图片验证码（image-captcha 规格 AC1-AC4） ----------


def test_captcha_endpoint_contract(tmp_path, registry):
    """AC1：公开端点返回 id + SVG（4 字符）。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    body = client.get("/api/auth/captcha").json()
    assert body["captcha_id"]
    assert "<svg" in body["image"]
    assert body["image"].count("<text ") == 4


def test_register_requires_valid_captcha(tmp_path, registry):
    """AC2：缺字段 422、错验证码 400、正确 201；验证码先于重名 409 校验。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    missing = client.post(
        "/api/auth/register", json={"username": "carol", "password": "secret1"}
    )
    assert missing.status_code == 422

    captcha_id = client.get("/api/auth/captcha").json()["captcha_id"]
    wrong = client.post(
        "/api/auth/register",
        json={
            "username": "carol",
            "password": "secret12",
            "captcha_id": captcha_id,
            "captcha_code": "XXXX",
        },
    )
    assert wrong.status_code == 400
    assert "验证码" in wrong.json()["detail"]

    _register(client, "carol")

    # 重名 + 错验证码 → 400（验证码校验先于重名，不泄露"已存在"）
    bad_id = client.get("/api/auth/captcha").json()["captcha_id"]
    dup_bad_captcha = client.post(
        "/api/auth/register",
        json={
            "username": "carol",
            "password": "other123",
            "captcha_id": bad_id,
            "captcha_code": "XXXX",
        },
    )
    assert dup_bad_captcha.status_code == 400
    dup = client.post(
        "/api/auth/register", json={"username": "carol", "password": "other123", **_captcha(client)}
    )
    assert dup.status_code == 409


def test_login_captcha_trigger_flow(tmp_path, registry):
    """AC3：3 次密码错后强制验证码；成功清零；验证码错不计数、密码错仍计数。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    _register(client, "dave")
    for _ in range(3):
        response = client.post("/api/auth/login", json={"username": "dave", "password": "wrongpw"})
        assert response.status_code == 401
        assert response.json()["detail"] == "用户名或密码错误"

    # 第 4 次缺验证码 → 401 验证码文案
    response = client.post("/api/auth/login", json={"username": "dave", "password": "wrongpw"})
    assert response.status_code == 401 and "验证码" in response.json()["detail"]
    # 错验证码 → 401 验证码文案（不计数）
    response = client.post(
        "/api/auth/login",
        json={"username": "dave", "password": "wrongpw", "captcha_id": "x", "captcha_code": "XXXX"},
    )
    assert response.status_code == 401 and "验证码" in response.json()["detail"]
    # 对验证码 + 错密码 → 401 密码文案
    response = client.post(
        "/api/auth/login", json={"username": "dave", "password": "wrongpw", **_captcha(client)}
    )
    assert response.status_code == 401 and response.json()["detail"] == "用户名或密码错误"
    # 对验证码 + 对密码 → 200（成功清零）
    response = client.post(
        "/api/auth/login", json={"username": "dave", "password": "secret12", **_captcha(client)}
    )
    assert response.status_code == 200
    # 清零后无需验证码
    response = client.post("/api/auth/login", json={"username": "dave", "password": "secret12"})
    assert response.status_code == 200


def test_login_captcha_single_use(tmp_path, registry):
    """AC1/AC3：验证码一次一换——密码错消耗后复用同一验证码 → 验证码错误。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    _register(client, "erin")
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "erin", "password": "wrongpw"})
    captcha = _captcha(client)
    response = client.post(
        "/api/auth/login", json={"username": "erin", "password": "wrongpw", **captcha}
    )
    assert response.json()["detail"] == "用户名或密码错误"  # 验证码有效但已消耗
    response = client.post(
        "/api/auth/login", json={"username": "erin", "password": "secret12", **captcha}
    )
    assert response.status_code == 401 and "验证码" in response.json()["detail"]


def test_unknown_user_triggers_captcha(tmp_path, registry):
    """AC4：不存在的用户名同样累计失败并触发验证码（防探测）。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "ghost", "password": "whatever"})
    response = client.post("/api/auth/login", json={"username": "ghost", "password": "whatever"})
    assert response.status_code == 401 and "验证码" in response.json()["detail"]


# ---------- 登录锁定与密码策略（login-hardening 规格 AC1/AC3/AC5） ----------


def test_login_lock_after_five_failures(tmp_path, registry):
    """AC1：24h 窗口内 5 次密码错 → 锁定；验证码正确、密码正确也不放行。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    _register(client, "frank")
    for _ in range(3):
        response = client.post("/api/auth/login", json={"username": "frank", "password": "wrongpw"})
        assert response.json()["detail"] == "用户名或密码错误"
    for _ in range(2):  # 第 4、5 次需验证码（≥3 触发）
        response = client.post(
            "/api/auth/login", json={"username": "frank", "password": "wrongpw", **_captcha(client)}
        )
        assert response.json()["detail"] == "用户名或密码错误"
    # 第 6 次：验证码正确 + 密码正确 → 401 锁定文案
    response = client.post(
        "/api/auth/login", json={"username": "frank", "password": "secret12", **_captcha(client)}
    )
    assert response.status_code == 401
    assert "锁定" in response.json()["detail"]


def test_lock_message_same_for_unknown_user(tmp_path, registry):
    """AC5：不存在用户 5 次错后的锁定文案与存在用户一致（防探测）。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    _register(client, "grace")
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "grace", "password": "wrongpw"})
    for _ in range(2):
        client.post(
            "/api/auth/login", json={"username": "grace", "password": "wrongpw", **_captcha(client)}
        )
    locked_real = client.post(
        "/api/auth/login", json={"username": "grace", "password": "secret12", **_captcha(client)}
    ).json()["detail"]

    for _ in range(3):
        client.post("/api/auth/login", json={"username": "ghost", "password": "wrongpw"})
    for _ in range(2):
        client.post(
            "/api/auth/login", json={"username": "ghost", "password": "wrongpw", **_captcha(client)}
        )
    locked_ghost = client.post(
        "/api/auth/login", json={"username": "ghost", "password": "wrongpw", **_captcha(client)}
    ).json()["detail"]

    assert "锁定" in locked_real
    assert locked_real == locked_ghost


def test_register_password_policy(tmp_path, registry):
    """AC3：注册密码 8-20 位且含字母数字；登录不受复杂度限制（老账号兼容）。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    for weak in ("12345678", "abcdefgh", "abc1234"):
        response = client.post(
            "/api/auth/register", json={"username": "hank", "password": weak, **_captcha(client)}
        )
        assert response.status_code == 422, weak
    _register(client, "hank", password="abc12345")
    # 登录宽松：任意 ≥6 位密码都能提交（是否通过由密码本身决定）
    response = client.post("/api/auth/login", json={"username": "hank", "password": "abc12345"})
    assert response.status_code == 200
