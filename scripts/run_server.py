"""起服务：uvicorn 托管 FastAPI 应用（本地与服务器共用）。

前置：已导入题库（scripts/import_questions.py）、已设置 DEEPSEEK_API_KEY。
用法：
  python scripts/run_server.py          # 默认 127.0.0.1:8002 + 本地 sqlite 题库
环境变量（服务器部署用，缺省即本地行为）：
  DATABASE_URL         题库库连接串（缺省 data/offercoach_dev.db）
  OFFERCOACH_HOST      监听地址（缺省 127.0.0.1；服务器设 0.0.0.0）
  OFFERCOACH_PORT      端口（缺省 8002）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.api.deps import build_runtime  # noqa: E402

app = create_app(build_runtime(question_db_url=os.environ.get("DATABASE_URL")))

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("OFFERCOACH_HOST", "127.0.0.1"),
        port=int(os.environ.get("OFFERCOACH_PORT", "8002")),
    )
