"""bge-m3 embedding 服务（服务器本地，127.0.0.1:8090）。

POST /embed {"texts": [...]} → {"vectors": [[1024 维]...]}；模型进程内常驻加载一次。
按 systemd offercoach-embed.service 运行；模型路径/端口可环境变量覆盖。
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel, Field

MODEL_NAME = os.environ.get("EMBED_MODEL_NAME", "BAAI/bge-m3")
MODEL_CACHE = os.environ.get("EMBED_MODEL_CACHE", "/data/offercoach/models")
MAX_TEXTS = int(os.environ.get("EMBED_MAX_TEXTS", "32"))
MAX_TEXT_CHARS = int(os.environ.get("EMBED_MAX_TEXT_CHARS", "8000"))

app = FastAPI(title="OfferCoach Embedding", version="0.1.0")
_model = None


class EmbedIn(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS)


def _load_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # 惰性 import：启动后才需要

        # 权重走 pytorch_model.bin（torch 2.7.1 满足 CVE-2025-32434 的 torch.load 要求）；
        # local_files_only：运行期绝不触网（unit 已设 HF_HUB_OFFLINE=1）。
        _model = SentenceTransformer(MODEL_NAME, cache_folder=MODEL_CACHE, local_files_only=True)
    return _model


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/embed")
def embed(body: EmbedIn) -> dict[str, list[list[float]]]:
    from fastapi import HTTPException

    for text in body.texts:
        if len(text) > MAX_TEXT_CHARS:
            raise HTTPException(status_code=422, detail="文本超过长度上限")
    try:
        model = _load_model()
    except Exception as exc:  # 模型未就绪 → 503
        raise HTTPException(status_code=503, detail=f"模型未就绪：{exc}") from exc
    embeddings = model.encode(body.texts, normalize_embeddings=True)
    return {"vectors": [vector.tolist() for vector in embeddings]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("EMBED_HOST", "127.0.0.1"),
        port=int(os.environ.get("EMBED_PORT", "8090")),
    )
