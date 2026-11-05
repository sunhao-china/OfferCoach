"""运行时依赖装配（生产路径）：模型分档 / 题库 / 提示词 / 检查点 / 组合层接线。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import create_engine

from app.api.auth_service import AuthService
from app.api.captcha import CaptchaStore
from app.api.rate_limit import RateLimiter
from app.api.session_store import SessionStore
from app.composition import build_app_graph, build_subgraphs
from app.core.config import settings
from app.core.embedding import HttpEmbedder
from app.core.llm_factory import TIER_CHEAP, TIER_STRONG, build_chat_model
from app.drill.service import DrillService
from app.hooks.bus import HookBus
from app.memory.scheduler import MemoryScheduler
from app.memory.service import MemoryService
from app.memory.store import QdrantMemoryStore
from app.prompts.registry import PromptRegistry
from app.question_bank.store import QuestionStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSION_DB_PATH = PROJECT_ROOT / "data" / "offercoach_sessions.db"


@dataclass
class Runtime:
    """API 层运行依赖集合；测试可整体替换各字段。

    limiter 缺省 None = 限流关闭（测试零行为）；生产 build_runtime 装配默认规则。
    memory/scheduler 缺省 None = 三层记忆关闭（测试零行为）；生产 build_runtime 装配。
    """

    root: Any  # 根图（chat 会话）
    subgraphs: dict[str, Any]  # 直连子图（interview/resume/review）
    checkpointer: Any
    sessions: SessionStore
    auth: AuthService
    captcha: CaptchaStore
    limiter: RateLimiter | None = None
    memory: Any = None
    scheduler: Any = None
    drill: Any = None  # 练习计划服务（build_runtime 装配；测试可注入替换）

    def dispose(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown()
        conn = getattr(self.checkpointer, "conn", None)
        if conn is not None:
            conn.close()


def build_runtime(
    *, question_db_url: str | None = None, session_db_path: Path | None = None
) -> Runtime:
    """构建生产运行时；缺省用开发库 data/offercoach_dev.db + data/offercoach_sessions.db。"""
    dev_db = (PROJECT_ROOT / "data" / "offercoach_dev.db").as_posix()
    question_engine = create_engine(question_db_url or f"sqlite:///{dev_db}")
    store = QuestionStore(question_engine)
    registry = PromptRegistry(settings.prompts_dir)
    bus = HookBus()
    # langgraph 线程池跨线程写检查点：必须 check_same_thread=False（官方实现内部有锁）
    conn = sqlite3.connect(str(session_db_path or SESSION_DB_PATH), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()
    llm_strong = build_chat_model(TIER_STRONG)
    llm_classify = build_chat_model(TIER_CHEAP)
    session_engine = create_engine(f"sqlite:///{SESSION_DB_PATH.as_posix()}")
    sessions = SessionStore(session_engine)  # create_all 顺带建 auth 三表
    # 三层记忆：Qdrant(oc_) + embedding 服务；任何依赖不可用 → fail-open 记录不阻断
    memory = MemoryService(
        store=QdrantMemoryStore(),
        embedder=HttpEmbedder(),
        llm=llm_strong,
        registry=registry,
        checkpointer=checkpointer,
        sessions=sessions,
        bus=bus,
    )
    memory.ensure()
    memory.register()
    scheduler = MemoryScheduler(memory)
    scheduler.start()
    subgraphs = build_subgraphs(
        store=store,
        registry=registry,
        llm_classify=llm_classify,
        llm_strong=llm_strong,
        bus=bus,
        checkpointer=checkpointer,
        memory=memory,
    )
    root = build_app_graph(
        store=store,
        registry=registry,
        llm_classify=llm_classify,
        llm_strong=llm_strong,
        bus=bus,
        checkpointer=checkpointer,
        memory=memory,
        subgraphs=subgraphs,  # 与直连会话共享同一批子图实例
    )
    captcha = CaptchaStore()
    auth = AuthService(session_engine, captcha=captcha)
    drill = DrillService(
        store=store,
        engine=session_engine,
        registry=registry,
        llm=llm_strong,
        llm_classify=llm_classify,
    )
    return Runtime(
        root=root,
        subgraphs=subgraphs,
        checkpointer=checkpointer,
        sessions=sessions,
        auth=auth,
        captcha=captcha,
        limiter=RateLimiter(),
        memory=memory,
        scheduler=scheduler,
        drill=drill,
    )
