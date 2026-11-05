"""登录认证服务：注册/登录/登出/token 校验 + 24h 滑动窗口失败锁定。

最简方案（架构简化）：PBKDF2 散列密码（stdlib，无新依赖）、随机 token 客户端持有、
库内存 sha256(token)。token 7 天滑动过期：每次成功认证续期，过期即删行返 None
（401 文案不变，防枚举不变量）。
防线叠加：登录连续失败 CAPTCHA_THRESHOLD 次后强制图片验证码；窗口内密码错满
MAX_FAILURES 次（24 小时滑动窗口）→ 账号临时锁定，正确密码也不放行。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import AuthToken, AuthUser, Base, LoginFailure

PBKDF2_ITERATIONS = 600_000  # OWASP 建议的 PBKDF2-HMAC-SHA256 迭代数
USERNAME_MIN, USERNAME_MAX = 3, 20
PASSWORD_MIN = 6
CAPTCHA_THRESHOLD = 3
MAX_FAILURES = 5
LOCK_WINDOW_SECONDS = 24 * 3600
TOKEN_TTL = timedelta(days=7)  # 滑动过期：每次成功认证续期 7 天


class DuplicateUsernameError(Exception):
    """用户名已存在（API 层转 409）。"""


class CaptchaRequiredError(Exception):
    """登录已触发验证码但缺失/无效（API 层转 401 验证码文案）。"""


class AccountLockedError(Exception):
    """24 小时窗口内密码错满 MAX_FAILURES 次（API 层转 401 锁定文案）。"""


def hash_password(password: str) -> str:
    """PBKDF2 散列：`pbkdf2_sha256$<迭代>$<盐hex>$<摘要hex>`。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验散列；格式不对/算法不符一律 False。"""
    try:
        algorithm, iterations, salt_hex, hash_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


class AuthService:
    """engine 注入（与 SessionStore 同库同 engine，create_all 由其完成）。

    captcha 缺省 None：不启用验证码（既有行为不变）；传入 CaptchaStore 后
    登录连续失败达阈值强制验证码。clock 注入（缺省 time.time）：锁定窗口
    的"现在"取自 clock，单测用假时钟推进 24 小时。
    """

    def __init__(self, engine: Engine, captcha=None, clock=time.time) -> None:
        self._engine = engine
        self._captcha = captcha
        self._clock = clock
        self._migrate_failures_table()
        self._migrate_tokens_table()

    def _migrate_failures_table(self) -> None:
        """老版 auth_login_failures 是计数行（failures 列），与事件行不兼容：
        直接 DROP 重建（失败记录是临时安全状态，重置可接受；幂等）。"""
        tables = inspect(self._engine).get_table_names()
        if "auth_login_failures" not in tables:
            Base.metadata.create_all(self._engine)
            return
        columns = {
            column["name"]
            for column in inspect(self._engine).get_columns("auth_login_failures")
        }
        if "failures" in columns:
            with self._engine.begin() as conn:
                conn.exec_driver_sql("DROP TABLE auth_login_failures")
            Base.metadata.create_all(self._engine)

    def _migrate_tokens_table(self) -> None:
        """老库 auth_tokens 无 expires_at 列：ALTER 加列并回填 created_at + 7 天（幂等）。"""
        tables = inspect(self._engine).get_table_names()
        if "auth_tokens" not in tables:
            return
        columns = {
            column["name"] for column in inspect(self._engine).get_columns("auth_tokens")
        }
        if "expires_at" not in columns:
            with self._engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE auth_tokens ADD COLUMN expires_at DATETIME")
        with Session(self._engine) as session:
            legacy = session.scalars(select(AuthToken).where(AuthToken.expires_at.is_(None))).all()
            for row in legacy:
                row.expires_at = (row.created_at or self._now_utc()) + TOKEN_TTL
            session.commit()

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock())

    def _now_utc(self) -> datetime:
        """naive UTC「现在」：与 server_default 的 CURRENT_TIMESTAMP（UTC）同基准。"""
        return datetime.fromtimestamp(self._clock(), tz=UTC).replace(tzinfo=None)

    def _window_count(self, username: str) -> int:
        """剪除 24 小时窗口外事件后返回窗口内失败次数。"""
        with Session(self._engine) as session:
            cutoff = datetime.fromtimestamp(self._clock() - LOCK_WINDOW_SECONDS)
            session.execute(
                delete(LoginFailure).where(
                    LoginFailure.username == username, LoginFailure.failed_at < cutoff
                )
            )
            session.commit()
            count = session.scalar(
                select(func.count())
                .select_from(LoginFailure)
                .where(LoginFailure.username == username)
            )
            return count or 0

    def register(self, username: str, password: str) -> tuple[str, str]:
        """注册并签发 token；用户名重复抛 DuplicateUsernameError；清零失败事件。"""
        with Session(self._engine) as session:
            exists = session.scalar(select(AuthUser).where(AuthUser.username == username))
            if exists is not None:
                raise DuplicateUsernameError
            user = AuthUser(username=username, password_hash=hash_password(password))
            session.add(user)
            session.commit()
            session.refresh(user)
            self.clear_login_failures(username)
            return self._issue(session, user.id), username

    def login_failures(self, username: str) -> int:
        """窗口内失败次数（先剪除过期事件）。"""
        return self._window_count(username)

    def record_login_failure(self, username: str) -> None:
        """密码错记一条事件（用户不存在也记，防探测）；验证码错误不计。"""
        with Session(self._engine) as session:
            session.add(LoginFailure(username=username, failed_at=self._now()))
            session.commit()

    def clear_login_failures(self, username: str) -> None:
        with Session(self._engine) as session:
            session.execute(delete(LoginFailure).where(LoginFailure.username == username))
            session.commit()

    def login(
        self,
        username: str,
        password: str,
        captcha_id: str | None = None,
        captcha_code: str | None = None,
    ) -> tuple[str, str] | None:
        """登录成功签发新 token；用户不存在与密码错误同返 None（防枚举）。

        防线顺序：① 窗口内失败 ≥ MAX_FAILURES → 抛 AccountLockedError（先于验证码，
        锁定期间密码根本不校验）；② 失败 ≥ CAPTCHA_THRESHOLD → 验证码缺失/无效抛
        CaptchaRequiredError（不记事件）；③ 密码错记事件；④ 成功清空事件。
        """
        if self._window_count(username) >= MAX_FAILURES:
            raise AccountLockedError
        if self._captcha is not None and self._window_count(username) >= CAPTCHA_THRESHOLD:
            if not captcha_id or not self._captcha.verify(captcha_id, captcha_code or ""):
                raise CaptchaRequiredError
        with Session(self._engine) as session:
            user = session.scalar(select(AuthUser).where(AuthUser.username == username))
            if user is None or not verify_password(password, user.password_hash):
                self.record_login_failure(username)
                return None
            self.clear_login_failures(username)
            return self._issue(session, user.id), username

    def authenticate(self, token: str) -> str | None:
        """token 有效返回用户名（并滑动续期），否则 None。

        过期/缺 expires_at（防御）→ 删行返 None——401 文案与无效 token 一致，不泄露状态。
        """
        if not token:
            return None
        token_hash = self._token_hash(token)
        with Session(self._engine) as session:
            row = session.scalar(select(AuthToken).where(AuthToken.token_hash == token_hash))
            if row is None:
                return None
            if row.expires_at is None or row.expires_at <= self._now_utc():
                session.delete(row)
                session.commit()
                return None
            row.expires_at = self._now_utc() + TOKEN_TTL  # 滑动续期
            session.commit()
            return session.scalar(select(AuthUser.username).where(AuthUser.id == row.user_id))

    def logout(self, token: str) -> None:
        """删除 token（幂等）。"""
        with Session(self._engine) as session:
            session.execute(
                delete(AuthToken).where(AuthToken.token_hash == self._token_hash(token))
            )
            session.commit()

    def _issue(self, session: Session, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        session.add(
            AuthToken(
                token_hash=self._token_hash(token),
                user_id=user_id,
                expires_at=self._now_utc() + TOKEN_TTL,
            )
        )
        session.commit()
        return token

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
