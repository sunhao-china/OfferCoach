"""L4: RateLimiter 单元 — 滑动窗口计数 / 超限拒绝 / 窗口滑动恢复 / 懒清理。

依据 specs/02-in-progress/web/token-expiry-rate-limit.md 的 AC2。
"""

from __future__ import annotations

from collections import deque

from app.api import rate_limit as rate_limit_module
from app.api.rate_limit import RateLimiter, RateRule


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_limiter(rules=None, clock=None) -> RateLimiter:
    return RateLimiter(rules=rules, clock=clock or FakeClock())


def test_allow_until_limit_then_deny_with_retry():
    clock = FakeClock()
    limiter = make_limiter({"r": RateRule(limit=3, window=60)}, clock=clock)
    for _ in range(3):
        assert limiter.allow("k", "r")
    assert not limiter.allow("k", "r")  # 第 4 次超限
    assert 0 < limiter.retry_after("k", "r") <= 60  # 最早时间戳滚出窗口即可再试


def test_window_slides_and_recovers():
    clock = FakeClock()
    limiter = make_limiter({"r": RateRule(limit=2, window=60)}, clock=clock)
    assert limiter.allow("k", "r")
    assert limiter.allow("k", "r")
    assert not limiter.allow("k", "r")
    clock.advance(61)
    assert limiter.allow("k", "r")  # 窗口滑动后恢复
    assert limiter.allow("k", "r")  # 新窗口第 2 次
    assert not limiter.allow("k", "r")  # 第 3 次超限


def test_retry_after_counts_down():
    clock = FakeClock()
    limiter = make_limiter({"r": RateRule(limit=1, window=60)}, clock=clock)
    assert limiter.allow("k", "r")
    assert not limiter.allow("k", "r")
    before = limiter.retry_after("k", "r")
    clock.advance(30)
    after = limiter.retry_after("k", "r")
    assert before - after == 30  # 随时钟推进递减
    clock.advance(31)
    assert limiter.allow("k", "r")  # 窗口滚出


def test_keys_are_independent():
    clock = FakeClock()
    limiter = make_limiter({"r": RateRule(limit=1, window=60)}, clock=clock)
    assert limiter.allow("a", "r")
    assert limiter.allow("b", "r")  # 不同键互不影响
    assert not limiter.allow("a", "r")


def test_unknown_rule_fails_open():
    limiter = make_limiter()
    assert limiter.allow("k", "no_such_rule")  # 配置错误不拦业务
    assert limiter.retry_after("k", "no_such_rule") == 0


def test_sweep_prunes_expired_keys(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(rate_limit_module, "MAX_KEYS", 2)
    limiter = make_limiter({"login": RateRule(limit=5, window=60)}, clock=clock)
    limiter._hits["login:a"] = deque([0.0])  # 早已过期
    limiter._hits["login:b"] = deque([0.0])
    assert limiter.allow("login:c", "login")  # 触发清扫（键数 3 > 上限 2）
    assert set(limiter._hits) == {"login:c"}  # 过期键被清掉


def test_sweep_keeps_active_keys(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(rate_limit_module, "MAX_KEYS", 2)
    limiter = make_limiter({"login": RateRule(limit=5, window=60)}, clock=clock)
    limiter._hits["login:a"] = deque([clock.now])
    assert limiter.allow("login:b", "login")
    assert set(limiter._hits) == {"login:a", "login:b"}  # 活跃键保留
