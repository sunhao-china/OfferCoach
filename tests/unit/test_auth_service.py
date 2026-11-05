"""L4: AuthService 单元 — 密码散列 + 注册/登录/登出生命周期 + token 单向存储。

依据 specs/02-in-progress/web/auth-login.md 的 AC1（服务侧实现约束）。
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.api.auth_service import (
    LOCK_WINDOW_SECONDS,
    TOKEN_TTL,
    AccountLockedError,
    AuthService,
    CaptchaRequiredError,
    DuplicateUsernameError,
    hash_password,
    verify_password,
)
from app.api.captcha import CaptchaStore
from app.db import AuthToken, Base
from tests.unit.test_captcha import _code_from_svg


class FakeClock:
    """假时钟：单测推进 24 小时窗口。"""

    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_service(tmp_path, captcha=None, clock=time.time) -> AuthService:
    engine = create_engine(f"sqlite:///{(tmp_path / 'sessions.db').as_posix()}")
    Base.metadata.create_all(engine)
    return AuthService(engine, captcha=captcha, clock=clock)


def _engine_of(service: AuthService):
    return service._engine  # 测试直读私有 engine 检查落库内容


def test_hash_roundtrip_and_salts_differ():
    stored = hash_password("secret1")
    assert stored.startswith("pbkdf2_sha256$600000$")  # OWASP 建议迭代数
    assert verify_password("secret1", stored)
    assert not verify_password("wrong", stored)
    other = hash_password("secret1")
    assert other != stored  # 随机盐：同密码两次散列不同
    assert verify_password("secret1", other)


def test_legacy_100k_hash_still_verifies():
    """加固后老散列无需迁移：verify 从存储串读取迭代数。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", b"oldpass1", salt, 100_000)
    stored = f"pbkdf2_sha256$100000${salt.hex()}${digest.hex()}"
    assert verify_password("oldpass1", stored)
    assert not verify_password("wrongpw", stored)


def test_verify_bad_formats_return_false():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "md5$100000$aa$bb")
    assert not verify_password("x", "pbkdf2_sha256$notanumber$aa$bb")
    assert not verify_password("x", "pbkdf2_sha256$100000$zz$cc")  # 盐非法 hex
    assert not verify_password("x", "pbkdf2_sha256$100000$aa$cc")  # 摘要长度错


def test_register_login_logout_lifecycle(tmp_path):
    service = make_service(tmp_path)
    token, username = service.register("alice", "secret1")
    assert username == "alice"
    assert service.authenticate(token) == "alice"
    assert service.authenticate("garbage") is None
    assert service.authenticate("") is None

    assert service.login("alice", "wrongpass") is None
    assert service.login("nobody", "secret1") is None  # 用户不存在与密码错同返 None

    token2, _ = service.login("alice", "secret1")
    assert service.authenticate(token2) == "alice"  # 登录签发新 token
    assert service.authenticate(token) == "alice"  # 旧 token 仍有效（多端共存）

    service.logout(token)
    assert service.authenticate(token) is None
    assert service.authenticate(token2) == "alice"  # 只注销当前 token
    service.logout(token)  # 幂等


def test_register_duplicate_raises(tmp_path):
    service = make_service(tmp_path)
    service.register("alice", "secret1")
    with pytest.raises(DuplicateUsernameError):
        service.register("alice", "another1")


def test_db_stores_token_hash_not_raw(tmp_path):
    service = make_service(tmp_path)
    token, _ = service.register("alice", "secret1")
    with Session(_engine_of(service)) as session:
        stored = session.scalar(select(AuthToken.token_hash))
        user = session.scalar(select(AuthToken).limit(1))
    assert stored == hashlib.sha256(token.encode()).hexdigest()
    assert token not in stored  # 库内只有 sha256，拿库也反推不出 token
    assert "secret1" not in stored
    assert user.token_hash  # 行结构完整（user_id 外键由 create_all 保障）


# ---------- token 7 天滑动过期（token-expiry-rate-limit 规格 AC1） ----------


def test_token_expires_after_seven_days_and_row_deleted(tmp_path):
    clock = FakeClock()
    service = make_service(tmp_path, clock=clock)
    token, _ = service.register("alice", "secret1")
    assert service.authenticate(token) == "alice"
    clock.advance(7 * 24 * 3600 + 1)
    assert service.authenticate(token) is None
    with Session(_engine_of(service)) as session:
        assert session.scalar(select(AuthToken)) is None  # 过期行已删


def test_token_sliding_renewal_on_each_use(tmp_path):
    clock = FakeClock()
    service = make_service(tmp_path, clock=clock)
    token, _ = service.register("alice", "secret1")
    clock.advance(3 * 24 * 3600)
    assert service.authenticate(token) == "alice"  # 第 3 天使用 → 续期到第 10 天
    clock.advance(4 * 24 * 3600 + 1)  # 距上次 4 天 1 秒 < 7 天
    assert service.authenticate(token) == "alice"  # 仍有效，再续期
    clock.advance(7 * 24 * 3600 + 1)  # 距上次 7 天 1 秒
    assert service.authenticate(token) is None


def test_login_issued_token_also_carries_expiry(tmp_path):
    clock = FakeClock()
    service = make_service(tmp_path, clock=clock)
    service.register("alice", "secret1")
    token, _ = service.login("alice", "secret1")
    with Session(_engine_of(service)) as session:
        row = session.scalar(select(AuthToken))
    assert row.expires_at is not None
    clock.advance(7 * 24 * 3600 + 1)
    assert service.authenticate(token) is None


def test_null_expiry_treated_as_expired(tmp_path):
    """防御：迁移后不应再有 NULL，但出现时按过期处理（fail-closed）。"""
    service = make_service(tmp_path)
    token, _ = service.register("alice", "secret1")
    with Session(_engine_of(service)) as session:
        row = session.scalar(select(AuthToken))
        row.expires_at = None
        session.commit()
    assert service.authenticate(token) is None


def test_legacy_tokens_table_migrated_and_backfilled(tmp_path):
    """老库无 expires_at 列：初始化 ALTER 加列 + 回填 created_at + 7 天，token 仍有效。"""
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE auth_tokens ("
            "token_hash VARCHAR(64) PRIMARY KEY, user_id INTEGER, created_at DATETIME)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE auth_users (id INTEGER PRIMARY KEY, username VARCHAR(32) UNIQUE, "
            "password_hash VARCHAR(200), created_at DATETIME)"
        )
    token = "legacy-token-1"
    created = datetime.fromtimestamp(time.time() - 86400, tz=UTC).replace(tzinfo=None)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO auth_users (id, username, password_hash) VALUES (1, 'alice', 'x')"
        )
        conn.exec_driver_sql(
            "INSERT INTO auth_tokens (token_hash, user_id, created_at) VALUES (:h, 1, :c)",
            {"h": hashlib.sha256(token.encode()).hexdigest(), "c": created},
        )
    service = AuthService(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("auth_tokens")}
    assert "expires_at" in columns
    with Session(engine) as session:
        row = session.scalar(select(AuthToken))
    assert row.expires_at == created + TOKEN_TTL  # 回填 = created_at + 7 天
    assert service.authenticate(token) == "alice"  # 迁移后老 token 平滑有效


# ---------- 验证码触发（image-captcha 规格 AC3/AC4） ----------


def test_login_captcha_triggers_after_three_failures_and_resets(tmp_path):
    store = CaptchaStore()
    service = make_service(tmp_path, captcha=store)
    service.register("alice", "secret1")
    for _ in range(3):
        assert service.login("alice", "wrong") is None  # 前 3 次无验证码也放行到密码校验
    with pytest.raises(CaptchaRequiredError):
        service.login("alice", "wrong")  # 触发后缺验证码
    with pytest.raises(CaptchaRequiredError):
        captcha_id, _ = store.issue()
        service.login("alice", "wrong", captcha_id, "XXXX")  # 错验证码

    captcha_id, svg = store.issue()
    token, _ = service.login("alice", "secret1", captcha_id, _code_from_svg(svg))
    assert service.authenticate(token) == "alice"
    assert service.login_failures("alice") == 0  # 成功清零
    assert service.login("alice", "secret1") is not None  # 下次登录不再要验证码


def test_captcha_failure_does_not_count_and_wrong_password_still_counts(tmp_path):
    store = CaptchaStore()
    service = make_service(tmp_path, captcha=store)
    service.register("alice", "secret1")
    for _ in range(3):
        service.login("alice", "wrong")
    captcha_id, svg = store.issue()
    with pytest.raises(CaptchaRequiredError):
        service.login("alice", "secret1", captcha_id, "XXXX")  # 验证码错：不计数
    assert service.login_failures("alice") == 3

    captcha_id2, svg2 = store.issue()
    assert service.login("alice", "wrong", captcha_id2, _code_from_svg(svg2)) is None
    assert service.login_failures("alice") == 4  # 验证码对但密码错：仍 +1


def test_unknown_user_failures_also_trigger_captcha(tmp_path):
    """AC4 防探测：不存在的用户名同样累计失败并触发验证码。"""
    store = CaptchaStore()
    service = make_service(tmp_path, captcha=store)
    for _ in range(3):
        assert service.login("ghost", "whatever") is None
    with pytest.raises(CaptchaRequiredError):
        service.login("ghost", "whatever")


def test_register_clears_login_failures(tmp_path):
    store = CaptchaStore()
    service = make_service(tmp_path, captcha=store)
    for _ in range(3):
        service.login("newbie", "whatever")
    service.register("newbie", "secret1")
    assert service.login_failures("newbie") == 0  # 注册成功即清零


# ---------- 24h 窗口锁定（login-hardening 规格 AC1/AC2） ----------


def test_lock_after_five_failures_blocks_even_correct_password(tmp_path):
    store = CaptchaStore()
    service = make_service(tmp_path, captcha=store)
    service.register("alice", "secret1")
    for _ in range(3):
        assert service.login("alice", "wrong") is None  # 前 3 次免验证码
    for _ in range(2):  # 第 4、5 次需验证码
        captcha_id, svg = store.issue()
        assert service.login("alice", "wrong", captcha_id, _code_from_svg(svg)) is None
    # 第 6 次起锁定：验证码正确、密码正确都不放行
    captcha_id, svg = store.issue()
    with pytest.raises(AccountLockedError):
        service.login("alice", "secret1", captcha_id, _code_from_svg(svg))
    assert service.login_failures("alice") == 5


def test_lock_window_slides_after_24h(tmp_path):
    store = CaptchaStore()
    clock = FakeClock()
    service = make_service(tmp_path, captcha=store, clock=clock)
    service.register("alice", "secret1")
    for _ in range(3):
        service.login("alice", "wrong")
    for _ in range(2):
        captcha_id, svg = store.issue()
        service.login("alice", "wrong", captcha_id, _code_from_svg(svg))
    with pytest.raises(AccountLockedError):
        service.login("alice", "secret1")
    clock.advance(LOCK_WINDOW_SECONDS + 1)
    assert service.login_failures("alice") == 0  # 旧事件剪除
    assert service.login("alice", "secret1") is not None  # 可重新登录，验证码也不再需要


def test_success_before_lock_resets_window(tmp_path):
    store = CaptchaStore()
    service = make_service(tmp_path, captcha=store)
    service.register("alice", "secret1")
    for _ in range(3):
        service.login("alice", "wrong")
    captcha_id, svg = store.issue()
    service.login("alice", "wrong", captcha_id, _code_from_svg(svg))  # 第 4 次
    captcha_id2, svg2 = store.issue()
    # 成功清零（失败 ≥3 后连成功尝试也要验证码）
    assert service.login("alice", "secret1", captcha_id2, _code_from_svg(svg2)) is not None
    assert service.login_failures("alice") == 0
    for _ in range(3):
        service.login("alice", "wrong")  # 重新累计
    captcha_id3, svg3 = store.issue()
    # 第 4 次（<5 不锁）
    assert service.login("alice", "wrong", captcha_id3, _code_from_svg(svg3)) is None
    captcha_id4, svg4 = store.issue()
    assert service.login("alice", "secret1", captcha_id4, _code_from_svg(svg4)) is not None
