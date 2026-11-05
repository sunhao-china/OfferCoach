"""内存滑动窗口限流器（纯 stdlib，零新依赖）。

单进程 MVP：服务器单 uvicorn worker，重启清零可接受（登录暴力破解主防线是
AuthService 的 24h 锁定，限流是防滥用/控 LLM 成本的叠加层）。clock 注入供测试
假时钟推进。键内嵌规则名（`login:ip:1.2.3.4`），懒清理：访问时剪过期时间戳，
键总量超 MAX_KEYS 时整表清扫（清空已全部过期的键）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

MAX_KEYS = 100_000


@dataclass(frozen=True)
class RateRule:
    limit: int  # 窗口内允许次数
    window: float  # 窗口秒数


# 默认限流规则（IP 级防滥用 + 用户级控 LLM 成本）
DEFAULT_RULES: dict[str, RateRule] = {
    "global": RateRule(limit=120, window=60),  # 全端点每 IP
    "register": RateRule(limit=10, window=3600),  # 注册每 IP（防批量注册）
    "login": RateRule(limit=20, window=60),  # 登录每 IP（配合锁定防线）
    "captcha": RateRule(limit=30, window=60),  # 验证码每 IP（防刷图）
    "messages": RateRule(limit=20, window=60),  # 发消息每用户（控 LLM 成本）
    "session_create": RateRule(limit=20, window=60),  # 建会话每用户
    "upload": RateRule(limit=10, window=60),  # 上传每用户
    "drill": RateRule(limit=20, window=60),  # 练习判分每用户（控 LLM 成本）
    "reclassify": RateRule(limit=10, window=60),  # 错题纠正每用户（控 LLM 成本）
}


class RateLimiter:
    """滑动窗口计数器；rule 未知一律放行（fail-open，配置错误不拦业务）。"""

    def __init__(self, rules: dict[str, RateRule] | None = None, clock=time.time) -> None:
        self._rules: dict[str, RateRule] = dict(DEFAULT_RULES if rules is None else rules)
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = 0.0

    def allow(self, key: str, rule_name: str) -> bool:
        rule = self._rules.get(rule_name)
        if rule is None:
            return True
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = deque()
        while hits and hits[0] <= now - rule.window:
            hits.popleft()
        if len(hits) >= rule.limit:
            return False
        hits.append(now)
        self._sweep(now)
        return True

    def retry_after(self, key: str, rule_name: str) -> float:
        """距可再请求的秒数（未超限返回 0）。"""
        rule = self._rules.get(rule_name)
        hits = self._hits.get(key)
        if rule is None or not hits:
            return 0.0
        return max(0.0, hits[0] + rule.window - self._clock())

    def _sweep(self, now: float) -> None:
        """键总量超上限时整表清理（约每分钟至多一次）：剪过期 + 删空键。"""
        if len(self._hits) <= MAX_KEYS or now - self._last_sweep < 60:
            return
        self._last_sweep = now
        for key in list(self._hits):
            rule_name = key.split(":", 1)[0]
            rule = self._rules.get(rule_name)
            if rule is None:
                del self._hits[key]
                continue
            hits = self._hits[key]
            while hits and hits[0] <= now - rule.window:
                hits.popleft()
            if not hits:
                del self._hits[key]
