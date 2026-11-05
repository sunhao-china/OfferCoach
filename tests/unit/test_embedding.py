"""L4: Embedder — HttpEmbedder 请求/解析/异常 + DeterministicEmbedder 确定性。

依据 specs/02-in-progress/architecture/bge-m3-embedding.md 的 AC1。
"""

from __future__ import annotations

import httpx
import pytest

from app.core.embedding import DeterministicEmbedder, HttpEmbedder


def test_deterministic_same_text_same_vector():
    embedder = DeterministicEmbedder()
    assert embedder.embed_texts(["Java 并发"]) == embedder.embed_texts(["Java 并发"])
    vectors = embedder.embed_texts(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(vector) == 32 for vector in vectors)
    assert vectors[0] != vectors[1]  # 不同文本不同向量
    assert all(0.0 <= value <= 1.0 for value in vectors[0])


def test_http_embedder_sends_texts_and_parses_vectors():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json_body(request)
        return httpx.Response(200, json={"vectors": [[0.1, 0.2], [0.3, 0.4]]})

    embedder = HttpEmbedder(base_url="http://embed:8090")
    embedder._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert embedder.embed_texts(["你好", "世界"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["json"] == {"texts": ["你好", "世界"]}


def test_http_embedder_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "模型未就绪"})

    embedder = HttpEmbedder()
    embedder._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        embedder.embed_texts(["x"])


def json_body(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content)
