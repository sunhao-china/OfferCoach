"""L4: 图片验证码单元 — SVG 生成 + 单次使用/过期/大小写不敏感校验。

依据 specs/02-in-progress/web/image-captcha.md 的 AC1（验证码本身）。
"""

from __future__ import annotations

import time

from app.api.captcha import (
    CAPTCHA_ALPHABET,
    CAPTCHA_LENGTH,
    CaptchaStore,
    make_svg,
)


def _code_from_svg(svg: str) -> str:
    return "".join(
        part.split(">", 1)[1].split("<", 1)[0] for part in svg.split("<text ")[1:]
    )


def test_make_svg_renders_four_text_chars():
    svg = make_svg("AB3D")
    assert svg.startswith("<svg")
    assert svg.count("<text ") == 4
    assert _code_from_svg(svg) == "AB3D"


def test_issue_generates_valid_codes_only():
    store = CaptchaStore()
    for _ in range(50):
        _, svg = store.issue()
        code = _code_from_svg(svg)
        assert len(code) == CAPTCHA_LENGTH
        assert all(char in CAPTCHA_ALPHABET for char in code)  # 无 0/O/1/I/L


def test_verify_case_insensitive_and_whitespace_tolerant():
    store = CaptchaStore()
    captcha_id, svg = store.issue()
    code = _code_from_svg(svg)
    assert store.verify(captcha_id, code.lower())
    assert not store.verify(captcha_id, code)  # 单次使用：已消耗

    captcha_id2, svg2 = store.issue()
    assert store.verify(captcha_id2, f"  {_code_from_svg(svg2)} ")


def test_verify_wrong_code_consumes_entry():
    store = CaptchaStore()
    captcha_id, svg = store.issue()
    assert not store.verify(captcha_id, "XXXX")
    assert not store.verify(captcha_id, _code_from_svg(svg))  # 错一次即失效，须换新


def test_verify_unknown_id_false():
    store = CaptchaStore()
    assert not store.verify("no-such-id", "ABCD")


def test_verify_expired_false():
    store = CaptchaStore(ttl_seconds=0.05)
    captcha_id, svg = store.issue()
    time.sleep(0.1)
    assert not store.verify(captcha_id, _code_from_svg(svg))


def test_issues_are_independent():
    store = CaptchaStore()
    id1, _ = store.issue()
    id2, _ = store.issue()
    assert id1 != id2
