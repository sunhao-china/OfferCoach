# 计划：简单登录（注册/登录/登出 + 全 API 鉴权 + 前端登录层）

## 目标

当前 API 无鉴权（MVP），会话归属完全信任客户端自报的 user_id——任何拿到 URL 的人都能
读别人的会话。本步加**最简可行**的登录：用户名+密码注册/登录，签发随机 token，全部
业务 API 要求 `Authorization: Bearer <token>`，会话归属一律取 token 身份（客户端
user_id 字段废弃），前端加登录层。边界（架构简化偏好）：**不引入** cookie/session、
JWT、密码找回、验证码、限流、token 过期刷新——本地 MVP 工具，服务器恢复后再加固。

## 输入输出契约

### 模块一：认证服务（`app/api/auth_service.py` 新增）

- 表（挂 app.db 的 Base，与 chat_sessions 同库 data/offercoach_sessions.db，
  SessionStore 的 create_all 顺带建表）：
  - `auth_users`：id（pk）、username（unique）、password_hash、created_at；
  - `auth_tokens`：token_hash（pk，sha256 hex）、user_id、created_at；
- 密码散列：stdlib `hashlib.pbkdf2_hmac`（sha256，100_000 迭代，16 字节随机盐），
  存 `pbkdf2_sha256$100000$<salt_hex>$<hash_hex>`；校验用 `hmac.compare_digest`；
- token：`secrets.token_urlsafe(32)` 返回客户端，库内存 sha256(token)；无过期，登出即删；
- 服务方法：`register(username, password) -> (token, username)`（重名抛专属异常）、
  `login(username, password) -> token | None`（不存在与密码错同返 None，防枚举）、
  `authenticate(token) -> username | None`、`logout(token) -> None`。

### 模块二：API 端点与保护（`app/api/app.py`、`schemas.py`、`deps.py`）

- 公开端点（不鉴权）：
  - `POST /api/auth/register` {username, password} → 201 {token, username}；
    重名 409；username 3-20 字符 / password ≥6 字符 → 422；
  - `POST /api/auth/login` → 200 {token, username}；失败 401（统一文案「用户名或密码错误」）；
  - `/healthz`、静态页、`/` 保持公开；
- 受保护端点（除上面外全部 /api/*）：
  - 依赖 `get_current_user`：解析 `Authorization: Bearer <token>` →
    `runtime.auth.authenticate`；缺失/无效 → 401 {detail: "未登录或登录已过期"}；
  - `POST /api/auth/logout`（需有效 token）→ {ok: true}；`GET /api/auth/me` → {username}；
- 身份绑定与隔离：
  - create_session 的 user_id 一律取 token 身份；`SessionCreate.user_id` 字段删除
    （pydantic 忽略多余字段，旧前端兼容）；
  - `GET /api/sessions` 删除 user_id 查询参数，只返回当前用户会话；
  - get/delete/messages/files 对非本人会话 → 404（与不存在同响应，不泄露存在性）；
  - review 的 target_session_id 非本人 → 404（同文案）。

### 模块三：前端登录层（`app/web/index.html`、`app.js`、`style.css`）

- 登录层：未登录时全屏遮罩卡片（用户名/密码输入 + 登录/注册两个按钮 + 切换说明）；
  header 右侧显示当前用户名 + 登出按钮；
- token 存 localStorage `oc_token`、用户名 `oc_username`；删除 `oc_user_id` guest 逻辑；
- `api()` 与 SSE `fetch` 统一带 Authorization；任何 401 → 清 token、回登录层；
- 登录/注册成功 → 隐藏登录层，加载历史与复盘目标列表；登出 → 调 logout（失败忽略）、
  清 token 与本地会话记忆、回登录层。

## 验收标准（AC）

1. **AC1（认证契约）**：注册/登录/登出/me 按契约；重名 409、非法输入 422、
   错密码与不存在用户同 401；logout 后旧 token 失效。
2. **AC2（保护面）**：无 token / 坏 token 访问任一业务端点 → 401；/healthz、
   静态页、/api/auth/* 不受影响。
3. **AC3（身份绑定）**：带 A token 建会话，user_id 恒为 A（请求体无法覆盖）；
   会话列表无需且不接受 user_id 参数。
4. **AC4（会话隔离）**：B 看不到 A 的会话；B 访问 A 的会话（消息/删除/上传）→ 404；
   B 创建 review 指向 A 的面试会话 → 404。
5. **AC5（前端）**：未登录显示登录层、登录后进入主界面；401 自动回登录层；
   登出清 token；历史会话按账号隔离（服务端保证）。
6. **AC6（回归）**：全量测试（256 + 17 快照）适配后全绿；ruff/mypy 干净。
7. **AC7（真实冒烟）**：HTTP 冒烟：注册 → 登录 → 建会话 → 发消息 → 第二账号
   列表为空/访问 404 → logout → 旧 token 401。

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 计划落 01-planned（本规格） | specs/01-planned/web/auth-login.md | — |
| ② 认证服务 + 双表 | app/db.py（AuthUser/AuthToken）+ app/api/auth_service.py | 单元测试 |
| ③ API 端点 + 依赖保护 + 身份绑定 | app.py / schemas.py / deps.py | AC1-AC4 |
| ④ 前端登录层 + 401 处理 | index.html / app.js / style.css | AC5（test_frontend 适配） |
| ⑤ 既有 API 测试适配（登录 fixture） | tests/api/* | AC6 |
| ⑥ 真实冒烟 + 收尾 | — | AC7 |

## Definition of Done

- AC1-AC5 有测试且通过；全量回归（256 + 17 快照基准，新增后计数）全绿；ruff/mypy 干净。
- AC7 真实冒烟完成（双账号隔离验证），摘录进实现备注。
- 本规格流转 03-implemented，01/02/03 README 与记忆同步。

---

# 实现备注（2026-10-22）

## 落地情况

按计划五步落地（② 认证服务、③ API 保护、④ 前端登录层、⑤ 测试适配、⑥ 真实冒烟）。
全部 266 个测试 + 17 快照全绿，ruff 干净、mypy（app 全量 51 文件）零告警，
AC7 双账号 HTTP 冒烟 17/17。

与计划的两处偏差（均为记录层面，非行为）：
- 模型文件是 `app/db/__init__.py`（包），计划里写的是 app/db.py；
- `AuthService.login` 实际签名返回 `(token, username) | None`（计划写 `token | None`，
  实现时顺手带上用户名，避免 API 层二次查询）。

## 关键实现

- **双表 + 单向存储**：`auth_users`（pbkdf2_sha256$100000$盐$摘要，`hmac.compare_digest`
  校验）与 `auth_tokens`（pk 即 sha256(token)）。客户端持有 `secrets.token_urlsafe(32)`，
  库内只有散列——拿库反推不出 token，登出即删行。
- **防枚举**：登录失败（错密码/用户不存在）统一 401「用户名或密码错误」；跨账号会话
  访问统一 404「会话不存在」（与不存在同响应，不泄露存在性）。
- **身份单一来源**：`SessionCreate.user_id` 字段删除（pydantic 忽略多余字段，旧请求
  兼容），`list_sessions` 的 user_id 查询参数废弃忽略；所有业务端点
  `Depends(get_current_user)` 注入身份。
- **前端**：localStorage `oc_token`/`oc_username`；未登录全屏登录层；`api()` 与 SSE
  流统一带 Authorization，任何 401 → 清 token 回登录层；登出清本地会话记忆。
- 无 cookie/JWT/过期刷新/限流——本地 MVP，服务器恢复后再加固（见边界）。

## 测试

新增 10 个（266 = 256 + 10）：
- tests/unit/test_auth_service.py × 5：散列往返/随机盐/坏格式、注册登录登出生命周期、
  重名抛异常、库内只存 token 散列；
- tests/api/test_auth.py × 5：AC1 契约（409/422/401 同文案/登出失效）、AC2 保护面、
  AC3 身份绑定、AC4 双账号隔离（含跨账号复盘 target 404、上传 404）；
- 既有 API 测试适配：auth_headers fixture（默认账号 user1/user2——"u1" 只有 2 字符
  低于用户名下限 3，初跑 422 后改名）、test_stream / test_resume_upload /
  test_session_list_page 全部直接调用补 Authorization。

## 真实冒烟（AC7，uvicorn + 真实 deepseek）

```
PASS  无 token 列表 → 401  未登录或登录已过期
PASS  注册 A → 201 + token  smoke_a_201526
PASS  重名注册 → 409  用户名已被注册
PASS  登录失败 401 且文案一致（防枚举）  用户名或密码错误
PASS  me 返回当前身份  smoke_a_201526
PASS  建 chat 会话（身份取 token，忽略 user_id 字段）
PASS  A 发消息 → SSE 流式回复 + done 帧  67 帧, 回复 124 字
PASS  B 的会话列表为空
PASS  B 读 A 会话 → 404  会话不存在
PASS  B 删 A 会话 → 404
PASS  B 复盘 A 会话 → 404
PASS  A 读历史正常  ['user', 'assistant']
PASS  登出 → ok
PASS  登出后 me → 401
PASS  登出后旧 token 列表 → 401
PASS  重新登录后删除会话
PASS  healthz 与首页公开
== 结果：17/17 通过 ==
```

## 遗留与后续

- token 无过期、无限流、无密码找回（本地 MVP 边界，服务器恢复后随加固一起做）；
- 前端登录层已就绪：浏览器打开 http://127.0.0.1:8000 注册账号即可使用。
