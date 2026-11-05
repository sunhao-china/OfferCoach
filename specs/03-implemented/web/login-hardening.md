# 计划：登录安全加固（24 小时 5 次锁定 + 密码复杂度 + 迭代数升级）

## 目标

堵住暴力破解：验证码只挡自动化，OCR 可破后密码尝试次数无上限。本步加**硬上限**：
24 小时滑动窗口内密码错 5 次 → 账号临时锁定（与现有验证码叠加：第 4、5 次仍需验证码，
第 6 次起直接锁定文案，正确密码也不放行）；注册密码加复杂度要求；PBKDF2 迭代数升到
OWASP 建议值。边界（架构简化偏好）：不引入 IP 限流（无 IP 基建）、不做找回流程、
不换 Argon2（保持 stdlib 零依赖）；锁定按用户名（不存在用户名同样锁定，防探测）。

## 输入输出契约

### 模块一：失败事件表与锁定（`app/db/__init__.py`、`app/api/auth_service.py`）

- 表改造：`auth_login_failures` 从「计数行」改为「事件行」：id（pk）、username（index）、
  failed_at（DateTime）——滑动窗口需要逐条时间戳；**迁移**：AuthService 初始化时
  inspect 老表，发现旧 `failures` 列 → DROP 重建（失败记录是临时安全状态，重置可接受）；
- 常量：`MAX_FAILURES = 5`、`LOCK_WINDOW_SECONDS = 24 * 3600`；
- `AuthService(engine, captcha=None, clock=time.time)`：clock 注入（单测假时钟）；
  时间一律 `datetime.fromtimestamp(clock())`；
- `login` 流程（顺序）：① 剪除窗口外事件 + 计数 ≥5 → 抛 `AccountLockedError`
  （先于验证码检查；锁定期间密码根本不校验）；② 窗口内 ≥3 次 → 验证码（既有逻辑不变）；
  ③ 密码错 → 记事件（含不存在用户名）；④ 成功 → 清空该用户名全部事件；
- `register` 成功同样清空该用户名事件；验证码错不记事件（既有语义）；
- `login_failures(username)` 语义改为**窗口内**失败次数（内部先剪除）。

### 模块二：密码复杂度（`app/api/schemas.py`）

- 拆分模型：`LoginIn`（username 3-20 / password ≥6 / captcha 可选）——
  **登录不设复杂度**（老账号兼容，不能锁死老用户）；`RegisterIn`（username 3-20 /
  password 8-20 且**必须同时含字母和数字** / captcha_id、captcha_code 必填）；
- 复杂度校验用 field_validator，422 detail 文案「密码需 8-20 位且同时包含字母和数字」。

### 模块三：散列强度（`app/api/auth_service.py`）

- `PBKDF2_ITERATIONS` 100_000 → 600_000（OWASP PBKDF2-HMAC-SHA256 建议值）；
- 散列格式自带迭代数、verify 从存储串读取——**老散列（$100000$）无需迁移仍可验证**；
  新注册自然用新迭代数。

### 模块四：API（`app/api/app.py`）

- login 捕获 `AccountLockedError` → 401
  `"24 小时内错误次数过多，账号已临时锁定，请稍后再试"`（存在/不存在用户同文案）；
- register 用 `RegisterIn`、login 用 `LoginIn`（替代原 AuthIn/RegisterIn(AuthIn)）。

### 模块五：前端（`app/web/index.html`、`app.js`）

- 密码 placeholder 更新为「密码（8-20 位，含字母和数字）」；
- 422 detail 是数组（pydantic）——authAction 渲染取各条 msg 拼成一行（否则显示
  [object Object]）；锁定文案原样展示。

## 验收标准（AC）

1. **AC1（锁定契约）**：窗口内 5 次密码错 → 第 6 次起 401 锁定文案（验证码正确、
   密码正确都不放行）；成功登录清零；锁定前（≤4 次）正确密码可登录。
2. **AC2（窗口滑动）**：假时钟单测——5 次错后锁定；时钟 +24h 后旧事件剪除、
   可重新尝试；窗口内计数只统计 24h 内事件。
3. **AC3（密码策略）**：注册 8 位纯数字 / 纯字母 / 7 位混合 → 422；8-20 含字母数字
   → 201；**登录**对弱密码老账号照常放行（宽松模型）。
4. **AC4（迭代数）**：新散列前缀 `pbkdf2_sha256$600000$`；手工构造 `$100000$`
   老散列仍 verify 通过（兼容，无迁移）。
5. **AC5（防枚举与叠加）**：锁定文案对存在/不存在用户一致；验证码触发（≥3 次）
   与验证码错不计数语义不变；注册成功清空该用户失败事件。
6. **AC6（前端）**：placeholder 更新；422 数组渲染成可读文案。
7. **AC7（回归）**：全量（282 + 17 快照基准 + 新增）全绿；ruff/mypy 干净。
8. **AC8（真实冒烟）**：5 次错密码 → 锁定；锁定后正确密码也 401；弱密码注册 422 →
   强密码 201；dev 库老账号仍可登录。

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 计划落 01-planned（本规格） | specs/01-planned/web/login-hardening.md | — |
| ② 事件表 + 锁定 + clock 注入 | app/db/__init__.py + auth_service.py | 单测（假时钟）AC1/AC2 |
| ③ 密码复杂度 + 迭代数 | schemas.py + auth_service.py | AC3/AC4 |
| ④ API 接线 | app.py | AC5 |
| ⑤ 前端适配 | index.html / app.js | AC6 |
| ⑥ 既有测试适配（强密码/缓存）+ 回归 + 冒烟 + 收尾 | tests/* | AC7/AC8 |

## Definition of Done

- AC1-AC6 有测试且通过；全量回归（282 + 17 快照基准，新增后计数）全绿；ruff/mypy 干净。
- AC8 真实冒烟完成，摘录进实现备注。
- 本规格流转 03-implemented，01/02/03 README 与记忆同步。

---

# 实现备注（2026-10-22）

## 落地情况

按计划五步落地。全量 **289 测试 + 17 快照**全绿（282 基准 + 7 新增），ruff 干净、
mypy（app 52 文件）零告警，AC8 加固冒烟 12/12。套件 33s → 84s（600k 迭代的代价；
测试侧 auth_headers 按 client 实例缓存 token 摊薄了散列次数）。

## 关键实现

- **24h 滑动窗口锁定**（auth_service.py）：`auth_login_failures` 从计数行改为事件行
  （id/username/failed_at）——滑动窗口需要逐条时间戳；AuthService 初始化迁移：
  inspect 发现旧 `failures` 列 → DROP 重建（失败记录是临时安全状态，重置可接受，幂等）。
  `MAX_FAILURES = 5`、`LOCK_WINDOW_SECONDS = 86400`；`clock` 注入（单测假时钟推进窗口）。
  login 防线顺序：① 窗口计数 ≥5 → `AccountLockedError`（**先于验证码**，锁定期间密码
  根本不校验，正确密码也不放行）；② ≥3 → 验证码（既有逻辑）；③ 密码错记事件
  （不存在用户名同样记，防探测）；④ 成功清空事件。注册成功同样清空。
- **密码复杂度**（schemas.py）：模型拆分——`LoginIn` 宽松（≥6，老账号兼容，加固不能
  锁死既有用户）、`RegisterIn` 8-20 位且同时含字母数字（field_validator，422 文案
  「密码需 8-20 位且同时包含字母和数字」）。
- **散列强度**：`PBKDF2_ITERATIONS` 100k → 600k（OWASP PBKDF2-HMAC-SHA256 建议值）；
  散列串自带迭代数、verify 从存储串读取——**老散列 $100000$ 无需迁移仍可验证**
  （冒烟实测老账号登录成功）。
- **API**：login 捕获 `AccountLockedError` → 401「24 小时内错误次数过多，账号已临时
  锁定，请稍后再试」（存在/不存在用户同文案）。
- **前端**：密码 placeholder 更新；422 detail 是 pydantic 数组 → 取各条 msg 拼接渲染
  （否则显示 [object Object]）。

## 测试

新增 7 个（289 = 282 + 7）：
- tests/unit/test_auth_service.py × 4：老 $100000$ 散列仍 verify（兼容）、5 次错后锁定
  （验证码+密码都对也不放行）、假时钟 +24h 窗口滑动后解锁且免验证码、成功清零后重新
  累计不到 5 不锁；
- tests/api/test_auth.py × 3：5 次错 → 锁定（API 层全流程）、锁定文案存在/不存在用户
  一致、注册密码策略（纯数字/纯字母/7 位混合 422、合规 201、登录宽松）；
- 既有适配：注册测试密码全部升级到 8 位含字母数字（"x123456"/"other1" 等 7 位密码
  在新策略下 422，逐个修正）；auth_headers 按 client 实例缓存 token。

## 真实冒烟（AC8，uvicorn + dev 库）

```
PASS  注册弱密码 422：12345678
PASS  注册弱密码 422：abcdefgh
PASS  注册弱密码 422：abc1234
PASS  注册强密码 → 201
PASS  错密码第 1 次 → 401 密码文案
PASS  错密码第 2 次 → 401 密码文案
PASS  错密码第 3 次 → 401 密码文案
PASS  错密码第 4 次（带验证码）→ 401 密码文案
PASS  错密码第 5 次（带验证码）→ 401 密码文案
PASS  第 6 次：正确密码 + 正确验证码 → 401 锁定  24 小时内错误次数过多，账号已临时锁定，请稍后再试
PASS  不存在用户 5 次错 → 同样锁定文案
PASS  老账号（旧散列/旧策略）仍可登录  smoke_a_201526
== 结果：12/12 通过 ==
```

## 遗留与后续

- 锁定无「剩余次数/解锁时间」提示与管理员解锁通道（本地 MVP 不需要）；
- 验证码发放端点无速率限制（无 IP 基建，服务器恢复后加）；
- 老弱密码账号不强制改密（登录宽松的代价），服务器恢复后可加首次登录引导。
