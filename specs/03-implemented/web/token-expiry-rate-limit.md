# 服务器侧登录加固二：token 7 天滑动过期 + 接口限流（推迟清单 ① 剩余项）

## 目标

补上登录鉴权遗留的两块服务器侧加固：**token 无过期** → 7 天滑动过期（每次成功认证续期）；**无接口限流** → 内存滑动窗口限流（注册/登录/验证码按 IP，发消息/建会话/上传按用户）。零新依赖（纯 stdlib）。**找回密码本次范围外**：需要投递通道（邮箱/SMS），当前都没有——自建"重置 token 只回日志"反而制造安全隐患；管理员可经服务器 SQL 重置作为过渡。范围说明写进实现备注与报告，用户后续提供邮箱服务再补。

## 输入输出契约

输入：
- AuthService（clock 注入已具备，token 经 `_issue` 签发、`authenticate` 只查 hash 不过期）；AuthToken 表无 expires_at 列（本地 dev sqlite 与服务器 data/offercoach_sessions.db 都是老库，需迁移；测试库新建走模型）
- create_app(runtime)（无中间件）；Runtime dataclass（测试经 tests/api/test_web_api.py make_client 单点构造——新字段必须带缺省，否则 30+ 处全改）
- 部署环境：服务器单 uvicorn 进程（内存限流可行，重启清零可接受）；无反向代理（直接取 request.client.host）

输出：
- AuthToken 加 `expires_at`（nullable）列；AuthService 启动迁移（ALTER + 回填 created_at+7d）+ `_issue` 写 7d + authenticate 过期即删行返 None（401 文案不变，防枚举不变量）+ 滑动续期（每次成功认证刷新）
- app/api/rate_limit.py：RateLimiter（滑动窗口、内存 dict[deque]、clock 注入、allow/retry_after、懒清理）
- create_app：IP 级中间件（global 120/min、register 10/h、login 20/min、captcha 30/min）+ 业务端点 per-user 依赖（messages 20/min、session 创建 20/min、upload 10/min）；超限 429 + Retry-After 头
- Runtime 加 `limiter` 可缺省字段（None = 关闭，既有测试零行为）

## 验收标准（AC）

1. token 过期：过期 authenticate 返 None 且行被删；滑动续期（3 天时认证成功 → expires_at 推后）；老库迁移（无列的表初始化后列在、值为 created_at+7d）；401 文案与之前一致（防枚举不变量）；register/login 签发都带过期
2. 限流器：窗口内计数、超限拒绝、窗口滑动后恢复、retry_after 正确、假时钟驱动；键过期懒清理不涨内存
3. 端点限流（TestClient + 假时钟 limiter）：login 每 IP 20/min 超限 429 + Retry-After；captcha 超限 429；messages 每用户 20/min 超限 429 而**另一用户不受影响**；未登录业务请求仍 401（不因限流先炸）；get_current_user 每请求只执行一次（依赖缓存）
4. 全量回归：既有 Runtime 无 limiter 时 295 测试 + 17 快照全绿（零行为不变量）
5. 服务器部署：重跑 deploy.py（零新依赖，requirements 不动）；服务器验证——auth_tokens 有 expires_at 列、登录 flood 21 连发第 21 个 429、手工把 token 过期后 /api/auth/me 401 且行被删、注册+chat+面试业务冒烟通过
6. 红线合规：fin 资源零触碰；凭据零落盘（无新凭据）；零新依赖

## 实施计划

1. 规格 → specs/01-planned（本文件，开工流转 02-in-progress）
2. db.AuthToken 加 expires_at + auth_service 迁移/回填/过期/续期 + tests/unit/test_auth_service.py 补 AC1
3. app/api/rate_limit.py + tests/unit/test_rate_limit.py 补 AC2
4. create_app 中间件 + per-user 依赖 + Runtime.limiter 字段 + tests/api/test_rate_limit_api.py 补 AC3（make_client 加 limiter 参数）
5. ruff/mypy + 全量回归（AC4）
6. deploy.py 重跑 → 服务器验证（AC5）
7. 规格流转 03 + README + 记忆同步 + 中文报告

## Definition of Done

AC1-6 全部通过；冒烟与服务器验证证据写入实现备注；specs/03-implemented/README.md 与记忆文件同步；红线合规；找回密码的范围外说明同步进记忆与报告。

## 实现备注

**代码**：app/db AuthToken 加 `expires_at`（nullable，兼容老库）；auth_service 新增 `TOKEN_TTL = 7 天`、`_migrate_tokens_table()`（老库无列 → ALTER TABLE ADD COLUMN + 回填 created_at+7d，幂等；新库走 create_all 模型）、`_now_utc()`（naive UTC，与 server_default CURRENT_TIMESTAMP 同基准——`_now()` 是本地时区，用于锁定窗口的自洽对，过期比较必须用 UTC 对）；`_issue` 写 7 天过期；`authenticate` 过期/缺 expires_at → 删行返 None（fail-closed），有效则滑动续期（每次成功认证刷新 7 天）。401 文案不变（防枚举不变量）。新模块 app/api/rate_limit.py：RateLimiter 纯 stdlib 内存滑动窗口（dict[deque]，键内嵌规则名，访问时剪过期、键量超 MAX_KEYS=100k 整表清扫，未知规则 fail-open，clock 注入）。默认规则：global 120/min/IP、register 10/h/IP、login 20/min/IP、captcha 30/min/IP、messages 20/min/用户、session_create 20/min/用户、upload 10/min/用户。Runtime 加 `limiter: RateLimiter | None = None` 字段（缺省关闭，既有测试零行为）；build_runtime 装配默认规则。create_app：limiter 非 None 时挂 IP 级中间件（global + 公开认证端点专属规则，**中间件里不能 raise HTTPException**——在 ExceptionMiddleware 之外会变 500，必须直接返回 429 JSONResponse）；业务端点挂 `_user_limit(rule)` 依赖（复用 get_current_user，FastAPI 依赖缓存保证单请求单次认证）。

**测试（18 个新增，全量 313 + 17 快照）**：test_auth_service 补 5 个（7 天过期删行、滑动续期、登录签发也带过期、NULL 过期 fail-closed、老库迁移回填+老 token 平滑有效）；test_rate_limit 7 个（窗口计数/滑动恢复/retry_after 递减/键独立/fail-open/清扫删过期键保活跃键）；test_rate_limit_api 6 个（login 20 次 401 后 429+Retry-After、captcha 30 次后 429、register 10 次后 429、messages 每用户 20 后 429 且他人不受影响、未登录仍 401 不受限流影响、CountingAuth 子类证明每请求单次认证）。测试坑：register flood 用 "secret1" 被 422（密码复杂度 8-20 位）→ 换 "secret123"。

**服务器验证（AC5，2026-10-22）**：deploy.py 全量重跑（零新依赖）→ 服务重启 active。公网冒烟：业务主流程（chat 950 字 + interview 41 字，真实 LLM）✓；登录 flood 前 20 个 401、第 21 个 429 ✓；SSH 查库 auth_tokens 列含 expires_at（迁移成功），新 token 过期值 = 签发后 7 天 ✓；手工置过期后 /api/auth/me 401 且行被删（rows_left: 0）✓。附带发现：部署后有外部扫描器探测 GET /v1/models（77.90.185.118），404 挡住。

**范围外（诚实说明）**：找回密码未做——需要投递通道（邮箱/SMS），当前都没有；自建"重置 token 只回日志"是安全反模式。过渡方案：管理员可经服务器 SQL 重置密码。用户后续提供邮箱服务（SMTP 凭据）再补。另注：内存限流重启清零（单 worker 可接受；多 worker 需换 Redis 计数，Redis 上只用 `oc:` 前缀）。

