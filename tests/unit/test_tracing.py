"""L4: Langfuse 追踪开关 — 无密钥零行为、有密钥构建 handler。

依据 specs/02-in-progress/observability/langfuse-tracing.md 的 AC1。
"""

from __future__ import annotations

from app.core.tracing import build_trace_handler, tracing_enabled


def test_disabled_without_keys(monkeypatch):
    for name in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert not tracing_enabled()
    assert build_trace_handler() is None


def test_enabled_with_both_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test-secret")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test-public")
    assert tracing_enabled()
    handler = build_trace_handler()
    assert handler is not None  # 构造不触发网络（客户端懒加载）


def test_enabled_requires_both_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test-secret")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    assert not tracing_enabled()
    assert build_trace_handler() is None
