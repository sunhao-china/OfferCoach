"""L3: 前端静态托管与前后端契约一致性。

依据 specs/02-in-progress/web/frontend.md 的 AC1（静态托管）、AC3（app.js 引用的
每个 /api 路径都真实存在于 FastAPI 路由表）。

AC3 原理：从 app.js 源码提取字符串/模板字面量中的 /api 路径，把 ${...} 与
{param} 归一化为 PARAM、去掉查询串，再与后端路由归一化结果求差。
"""

from __future__ import annotations

import re

from app.api.app import WEB_DIR
from tests.api.test_web_api import _model, make_client
from tests.conftest import StubLLM


def _normalize(path: str) -> str:
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]*\}", "PARAM", path)
    path = re.sub(r"\{[a-zA-Z_]+\}", "PARAM", path)
    return path.rstrip("/")


def test_frontend_served(tmp_path, registry):
    """AC1 首页与静态资源可访问，页面标题与四页签齐全。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    home = client.get("/")
    assert home.status_code == 200
    assert "OfferCoach" in home.text
    for page in ("chat", "interview", "resume", "review", "history", "sessions"):
        assert f'data-page="{page}"' in home.text
    # 简历页：文件上传控件与 JD 对照输入框（resume-upload-jd 里程碑）
    assert 'id="resume-file"' in home.text
    assert 'id="resume-jd"' in home.text
    # 面试页：难度下拉与岗位输入（interview-options 里程碑）
    assert 'id="interview-difficulty"' in home.text
    assert 'id="interview-position"' in home.text
    # 三期：岗位输入 datalist 建议（多岗位题库）
    assert 'id="position-suggestions"' in home.text
    assert 'value="AI Agent"' in home.text
    assert 'value="项目经理"' in home.text
    # 会话列表页：列表容器与刷新按钮（session-list-page 里程碑）
    assert 'id="sessions-list"' in home.text
    assert 'id="sessions-refresh"' in home.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_frontend_api_paths_match_routes(tmp_path, registry):
    """AC3 app.js 引用的每个 /api 路径都存在于后端路由表。"""
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    used = {_normalize(path) for path in re.findall(r"[`\"'](/api/[^`\"']*)", js)}
    assert used, "app.js 中未提取到 /api 路径"
    api_routes = (route for route in client.app.routes if route.path.startswith("/api"))
    routes = {_normalize(route.path) for route in api_routes}
    missing = used - routes
    assert not missing, f"app.js 引用了后端不存在的端点: {missing}"
