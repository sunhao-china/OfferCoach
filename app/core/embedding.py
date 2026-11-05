"""Embedding 客户端与测试桩：三层记忆的向量化依赖。

生产走服务器本地 bge-m3 服务（localhost:8090，httpx 懒客户端，零 import 开销）；
测试用 DeterministicEmbedder（sha256 → 固定 32 维伪向量，零网络零模型）。
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # 类型检查可见；运行时不 import（懒加载保持零开销）
    import httpx

DIM_DETERMINISTIC = 32  # 测试桩固定维度（与真实 1024 维无关，测试自洽即可）


class Embedder(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class HttpEmbedder:
    """调用本地 bge-m3 服务；客户端懒加载（无请求不建连接）。

    timeout 参数化便于测试注入短超时。
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8090", timeout: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.Client | None = None  # 懒加载：首个请求才建连接

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        import httpx  # 惰性 import：无网络环境不加载

        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        response = self._client.post(f"{self._base_url}/embed", json={"texts": texts})
        response.raise_for_status()
        return response.json()["vectors"]


class DeterministicEmbedder:
    """确定性伪向量：文本 sha256 前 32 字节归一化。测试专用（真实语义召回用 HttpEmbedder）。"""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    @staticmethod
    def _embed(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [byte / 255.0 for byte in digest[:DIM_DETERMINISTIC]]
