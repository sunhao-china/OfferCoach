"""图片验证码：stdlib 生成 SVG + 内存单次使用校验（本地 MVP 挡自动化，非强对抗）。

无新依赖：随机字符逐个画成旋转 <text>，加噪线；服务端只存 sha256(code)。
CaptchaStore 线程安全（uvicorn 线程池并发），校验即消耗（pop），过期 300 秒。
"""

from __future__ import annotations

import hashlib
import hmac
import random
import secrets
import threading
import time

CAPTCHA_LENGTH = 4
CAPTCHA_TTL_SECONDS = 300
# 排除易混字符 0/O、1/I/L
CAPTCHA_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_COLORS = ("#1f4e79", "#7a3b00", "#27572c", "#5b2a5e", "#b02a37")
_WIDTH, _HEIGHT = 120, 40


def _svg_char(char: str, index: int) -> str:
    rotation = random.randint(-25, 25)
    font_size = random.randint(24, 30)
    x = 12 + index * 26 + random.randint(-3, 3)
    y = random.randint(26, 34)
    color = random.choice(_COLORS)
    return (
        f'<text x="{x}" y="{y}" font-size="{font_size}" fill="{color}" '
        f'font-family="Arial, sans-serif" font-weight="bold" '
        f'transform="rotate({rotation} {x} {y})">{char}</text>'
    )


def _svg_noise() -> str:
    lines = []
    for _ in range(random.randint(3, 4)):
        color = random.choice(_COLORS)
        lines.append(
            f'<line x1="{random.randint(0, _WIDTH)}" y1="{random.randint(0, _HEIGHT)}" '
            f'x2="{random.randint(0, _WIDTH)}" y2="{random.randint(0, _HEIGHT)}" '
            f'stroke="{color}" stroke-width="1" opacity="0.5"/>'
        )
    return "".join(lines)


def make_svg(code: str) -> str:
    """按 code 逐字符绘制 120×40 SVG（含噪线）。"""
    chars = "".join(_svg_char(char, index) for index, char in enumerate(code))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{_HEIGHT}" '
        f'viewBox="0 0 {_WIDTH} {_HEIGHT}">'
        f'<rect width="100%" height="100%" fill="#f4f6f8"/>'
        f"{_svg_noise()}{chars}</svg>"
    )


class CaptchaStore:
    """内存验证码存储：issue 生成、verify 单次校验（未知/过期/不匹配均 False）。"""

    def __init__(self, ttl_seconds: int = CAPTCHA_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    def issue(self) -> tuple[str, str]:
        """返回 (captcha_id, svg)。"""
        code = "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(CAPTCHA_LENGTH))
        captcha_id = secrets.token_urlsafe(16)
        with self._lock:
            self._entries[captcha_id] = (self._digest(code.lower()), time.monotonic())
        return captcha_id, make_svg(code)

    def verify(self, captcha_id: str, code: str) -> bool:
        """校验并消耗（pop）；大小写不敏感，空白容忍。"""
        with self._lock:
            entry = self._entries.pop(captcha_id, None)
        if entry is None:
            return False
        digest, issued = entry
        if time.monotonic() - issued > self._ttl:
            return False
        return hmac.compare_digest(digest, self._digest(code.strip().lower()))
