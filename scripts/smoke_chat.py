"""本地冒烟：真实 deepseek + sqlite 题库构建组合层，交互对话。

前置：已导入题库（见下）、已设置 DEEPSEEK_API_KEY。
  python scripts/import_questions.py --dry-run  # 校验
  DATABASE_URL=sqlite:///data/offercoach_dev.db python scripts/import_questions.py
用法：
  python scripts/smoke_chat.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from app.composition import build_app_graph, invoke_chat  # noqa: E402
from app.core.llm_factory import TIER_CHEAP, TIER_STRONG, build_chat_model  # noqa: E402
from app.prompts.registry import PromptRegistry  # noqa: E402
from app.question_bank.store import QuestionStore  # noqa: E402

DEV_DB = PROJECT_ROOT / "data" / "offercoach_dev.db"


def main() -> int:
    engine = create_engine(f"sqlite:///{DEV_DB.as_posix()}")
    registry = PromptRegistry(PROJECT_ROOT / "app" / "prompts" / "templates")
    graph = build_app_graph(
        store=QuestionStore(engine),
        registry=registry,
        llm_classify=build_chat_model(TIER_CHEAP),
        llm_strong=build_chat_model(TIER_STRONG),
        checkpointer=InMemorySaver(),
    )
    print("OfferCoach 本地冒烟（输入 exit 退出；同一 session 多轮累积）")
    session_id = "smoke-1"
    while True:
        try:
            text = input("你 > ").strip()
        except EOFError:
            break  # 管道输入结束
        if text.lower() in {"exit", "quit"}:
            break
        if not text:
            continue
        try:
            reply = invoke_chat(graph, "smoke-user", session_id, text)
            print(f"AI > {reply}")
        except Exception as exc:  # 冒烟工具：打印异常继续
            print(f"[错误] {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
