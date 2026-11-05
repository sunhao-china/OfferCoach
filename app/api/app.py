"""FastAPI 应用：按业务类型建会话（页面分离），消息同步返回完整回复。

鉴权：/api/auth/*（注册/登录）与 /healthz、静态页公开；其余端点要求
`Authorization: Bearer <token>`，会话归属一律取 token 身份（客户端 user_id 已废弃）。

端点：
- POST /api/auth/register|login|logout、GET /api/auth/me
- POST /api/sessions          创建会话（review 需 target_session_id）
- POST /api/sessions/{sid}/messages   发消息，返回 AI 回复
- GET  /api/sessions/{sid}/messages   会话历史（checkpointer 读出）
- GET  /api/sessions/{sid}            会话元数据
- DELETE /api/sessions/{sid}          删除会话（行 + 检查点线程；被复盘引用时 409）
- GET  /api/history                   历史回顾记录（chat 除外，含完成态与报告预览）
- GET  /healthz                       健康检查
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth_service import (
    AccountLockedError,
    CaptchaRequiredError,
    DuplicateUsernameError,
)
from app.api.deps import Runtime
from app.api.executor import (
    delete_checkpoint_threads,
    invoke_business,
    read_history,
    stream_business,
)
from app.api.history import build_history_records
from app.api.parsers import SUPPORTED_EXTENSIONS, extract_text
from app.api.schemas import (
    AuthOut,
    CaptchaOut,
    DrillAnswerIn,
    DrillAnswerOut,
    DrillPlanIn,
    DrillPlanOut,
    DrillProgressOut,
    DrillReclassifyIn,
    DrillReclassifyOut,
    DrillTodayOut,
    HistoryListOut,
    HistoryOut,
    LoginIn,
    LogoutOut,
    MessageIn,
    MessageOut,
    RegisterIn,
    SessionCreate,
    SessionDeleteOut,
    SessionListOut,
    SessionOut,
    UploadOut,
)
from app.drill.service import DrillNotFoundError

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
RATE_LIMIT_DETAIL = "请求过于频繁，请稍后再试"
# 公开端点 IP 级规则（其余端点只有 global 每 IP 限）
PUBLIC_IP_RULES = {
    "/api/auth/register": "register",
    "/api/auth/login": "login",
    "/api/auth/captcha": "captcha",
}


def _session_out(row) -> SessionOut:
    return SessionOut(
        session_id=row.id,
        business=row.business,
        user_id=row.user_id,
        target_session_id=row.target_session_id,
        difficulty=row.difficulty,
        position=row.position,
        created_at=row.created_at,
    )


def create_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="OfferCoach", version="0.1.0")

    def _limit(key: str, rule_name: str) -> HTTPException | None:
        """超限返回 429 异常，否则 None；runtime.limiter 为 None 时全部放行。"""
        if runtime.limiter is None or runtime.limiter.allow(key, rule_name):
            return None
        retry = runtime.limiter.retry_after(key, rule_name)
        return HTTPException(
            status_code=429,
            detail=RATE_LIMIT_DETAIL,
            headers={"Retry-After": str(max(1, int(retry)))},
        )

    if runtime.limiter is not None:

        @app.middleware("http")
        async def ip_rate_limit(request: Request, call_next):
            """IP 级限流：global 全端点 + 公开认证端点专属规则（先于鉴权，防刷）。

            中间件里不能 raise HTTPException（在 ExceptionMiddleware 之外会变 500），
            直接返回 429 JSONResponse。
            """
            ip = request.client.host if request.client else "unknown"
            ip_key = f"ip:{ip}"
            error = _limit(f"global:{ip_key}", "global")
            if error is None:
                rule_name = PUBLIC_IP_RULES.get(request.url.path)
                if rule_name is not None:
                    error = _limit(f"{rule_name}:{ip_key}", rule_name)
            if error is not None:
                return JSONResponse(
                    status_code=429,
                    content={"detail": error.detail},
                    headers=error.headers,
                )
            return await call_next(request)

    def _bearer_token(authorization: str | None) -> str:
        if authorization and authorization.startswith("Bearer "):
            return authorization[len("Bearer ") :]
        return ""

    def get_current_user(authorization: str | None = Header(default=None)) -> str:
        """解析 Bearer token → 用户名；缺失/无效统一 401（注入到各业务端点）。"""
        username = runtime.auth.authenticate(_bearer_token(authorization))
        if username is None:
            raise HTTPException(status_code=401, detail="未登录或登录已过期")
        return username

    def _user_limit(rule_name: str):
        """每用户限流依赖（复用 get_current_user——FastAPI 依赖缓存保证单次认证）。"""

        def check(username: str = Depends(get_current_user)) -> None:
            error = _limit(f"{rule_name}:user:{username}", rule_name)
            if error is not None:
                raise error

        return check

    def _owned(row, username: str):
        """会话归属校验；不存在/非本人统一 404（不泄露存在性）。"""
        if row is None or row.user_id != username:
            raise HTTPException(status_code=404, detail="会话不存在")
        return row

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # ---------- 认证端点（公开） ----------

    @app.get("/api/auth/captcha", response_model=CaptchaOut)
    def captcha() -> CaptchaOut:
        captcha_id, image = runtime.captcha.issue()
        return CaptchaOut(captcha_id=captcha_id, image=image)

    @app.post("/api/auth/register", response_model=AuthOut, status_code=201)
    def register(body: RegisterIn) -> AuthOut:
        if not runtime.captcha.verify(body.captcha_id, body.captcha_code):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")
        try:
            token, username = runtime.auth.register(body.username, body.password)
        except DuplicateUsernameError:
            raise HTTPException(status_code=409, detail="用户名已被注册") from None
        return AuthOut(token=token, username=username)

    @app.post("/api/auth/login", response_model=AuthOut)
    def login(body: LoginIn) -> AuthOut:
        try:
            result = runtime.auth.login(
                body.username, body.password, body.captcha_id, body.captcha_code
            )
        except AccountLockedError:
            raise HTTPException(
                status_code=401,
                detail="24 小时内错误次数过多，账号已临时锁定，请稍后再试",
            ) from None
        except CaptchaRequiredError:
            raise HTTPException(
                status_code=401, detail="验证码错误或已过期，请刷新后重试"
            ) from None
        if result is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token, username = result
        return AuthOut(token=token, username=username)

    @app.post("/api/auth/logout", response_model=LogoutOut)
    def logout(authorization: str | None = Header(default=None)) -> LogoutOut:
        token = _bearer_token(authorization)
        if runtime.auth.authenticate(token) is None:
            raise HTTPException(status_code=401, detail="未登录或登录已过期")
        runtime.auth.logout(token)
        return LogoutOut(ok=True)

    @app.get("/api/auth/me")
    def me(username: str = Depends(get_current_user)) -> dict[str, str]:
        return {"username": username}

    # ---------- 业务端点（需登录） ----------

    @app.post("/api/sessions", response_model=SessionOut)
    def create_session(
        body: SessionCreate,
        username: str = Depends(get_current_user),
        _: None = Depends(_user_limit("session_create")),
    ) -> SessionOut:
        if body.business == "review" and not body.target_session_id:
            raise HTTPException(status_code=422, detail="review 会话必须提供 target_session_id")
        target = runtime.sessions.get(body.target_session_id) if body.target_session_id else None
        if body.target_session_id and (target is None or target.user_id != username):
            raise HTTPException(status_code=404, detail="target_session_id 不存在")
        row = runtime.sessions.create(
            str(uuid.uuid4()),
            username,
            body.business,
            body.target_session_id,
            difficulty=body.difficulty,
            position=body.position,
        )
        return _session_out(row)

    @app.get("/api/sessions", response_model=SessionListOut)
    def list_sessions(username: str = Depends(get_current_user)) -> SessionListOut:
        rows = runtime.sessions.list(username)
        return SessionListOut(sessions=[_session_out(row) for row in rows])

    @app.get("/api/sessions/{session_id}", response_model=SessionOut)
    def get_session(session_id: str, username: str = Depends(get_current_user)) -> SessionOut:
        return _session_out(_owned(runtime.sessions.get(session_id), username))

    @app.delete("/api/sessions/{session_id}", response_model=SessionDeleteOut)
    def delete_session(
        session_id: str, username: str = Depends(get_current_user)
    ) -> SessionDeleteOut:
        row = _owned(runtime.sessions.get(session_id), username)
        if runtime.sessions.referenced_by(session_id):
            raise HTTPException(
                status_code=409, detail="该会话被复盘会话引用，请先删除对应的复盘会话"
            )
        runtime.sessions.delete(session_id)
        delete_checkpoint_threads(runtime.checkpointer, row)
        return SessionDeleteOut(deleted=True)

    @app.post("/api/sessions/{session_id}/messages", response_model=None)
    def send_message(
        session_id: str,
        body: MessageIn,
        username: str = Depends(get_current_user),
        _: None = Depends(_user_limit("messages")),
    ) -> MessageOut | StreamingResponse:
        row = _owned(runtime.sessions.get(session_id), username)
        runtime.sessions.touch(session_id)  # T1 兜底总结的静默判定基准
        if body.stream:
            # token 级流式（终稿节点 custom 事件）；生成器惰性执行于线程池
            return StreamingResponse(
                stream_business(
                    runtime.root, runtime.subgraphs, row, body.content, jd_text=body.jd_text
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        reply = invoke_business(
            runtime.root, runtime.subgraphs, row, body.content, jd_text=body.jd_text
        )
        return MessageOut(session_id=session_id, reply=reply)

    @app.post("/api/sessions/{session_id}/files", response_model=UploadOut)
    async def upload_file(
        session_id: str,
        file: UploadFile,
        username: str = Depends(get_current_user),
        _: None = Depends(_user_limit("upload")),
    ) -> UploadOut:
        """简历文件上传：提取文本返回（不落盘），前端走既有 resume 管道。"""
        _owned(runtime.sessions.get(session_id), username)
        filename = file.filename or ""
        if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
            raise HTTPException(status_code=400, detail="仅支持 PDF / docx 文件")
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件超过 5MB 上限")
        try:
            text = extract_text(filename, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return UploadOut(session_id=session_id, filename=filename, text=text)

    @app.get("/api/history", response_model=HistoryListOut)
    def list_history(username: str = Depends(get_current_user)) -> HistoryListOut:
        rows = runtime.sessions.list(username)
        return HistoryListOut(records=build_history_records(runtime.checkpointer, rows))

    @app.get("/api/sessions/{session_id}/messages", response_model=HistoryOut)
    def get_messages(session_id: str, username: str = Depends(get_current_user)) -> HistoryOut:
        row = _owned(runtime.sessions.get(session_id), username)
        return HistoryOut(session_id=session_id, messages=read_history(runtime.checkpointer, row))

    # ---------- 练习计划（页面直连服务层，不走 langgraph） ----------

    def _drill():
        if runtime.drill is None:
            raise HTTPException(status_code=503, detail="练习服务未装配")
        return runtime.drill

    @app.get("/api/drill/plan")
    def get_drill_plan(
        username: str = Depends(get_current_user),
    ) -> DrillPlanOut | None:
        plan = _drill().get_plan(username)
        return DrillPlanOut(**plan) if plan else None

    @app.put("/api/drill/plan", response_model=DrillPlanOut)
    def put_drill_plan(
        body: DrillPlanIn, username: str = Depends(get_current_user)
    ) -> DrillPlanOut:
        try:
            plan = _drill().save_plan(
                username,
                position=body.position,
                difficulty=body.difficulty,
                daily_count=body.daily_count,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return DrillPlanOut(**plan)

    @app.get("/api/drill/today", response_model=DrillTodayOut)
    def get_drill_today(username: str = Depends(get_current_user)) -> DrillTodayOut:
        try:
            return _drill().today_view(username)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/drill/answer", response_model=DrillAnswerOut)
    def post_drill_answer(
        body: DrillAnswerIn,
        username: str = Depends(get_current_user),
        _: None = Depends(_user_limit("drill")),
    ) -> DrillAnswerOut:
        try:
            return _drill().answer(username, body.question_id, body.answer)
        except DrillNotFoundError:
            raise HTTPException(status_code=404, detail="题目不存在") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except RuntimeError:
            raise HTTPException(status_code=502, detail="判分服务暂不可用，请稍后重试") from None

    @app.post("/api/drill/reclassify", response_model=DrillReclassifyOut)
    def post_drill_reclassify(
        body: DrillReclassifyIn,
        username: str = Depends(get_current_user),
        _: None = Depends(_user_limit("reclassify")),
    ) -> DrillReclassifyOut:
        try:
            return _drill().reclassify(username, body.question_id)
        except DrillNotFoundError:
            raise HTTPException(status_code=404, detail="题目不存在") from None
        except RuntimeError:
            raise HTTPException(status_code=502, detail="分类识别暂不可用，请稍后重试") from None

    @app.get("/api/drill/progress", response_model=DrillProgressOut)
    def get_drill_progress(
        username: str = Depends(get_current_user),
    ) -> DrillProgressOut:
        return _drill().progress(username)

    # 前端静态托管：/ 返回单页入口，/static/* 承载 js/css（无构建链）
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    return app
