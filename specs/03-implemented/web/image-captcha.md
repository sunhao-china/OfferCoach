# 计划：图片验证码（注册恒要求 + 登录失败 3 次触发）

## 目标

为认证端点加图片验证码，堵住批量注册与密码爆破两个口子。**最简方案（架构简化偏好）**：
stdlib 生成 SVG 图片（零新依赖）、内存单次使用 5 分钟过期、注册恒要求、
登录连续失败 3 次后强制（成功即清零）。防对抗级别定位：本地 MVP 挡自动化脚本，
非强对抗（服务器恢复后随加固升级）。仍不引入限流（本步验证码即轻量限流替代）。

## 输入输出契约

### 模块一：验证码生成/校验（`app/api/captcha.py` 新增）

- 生成：4 位随机字符，字符集排除易混（0/O/1/I/L）；`captcha_id = secrets.token_urlsafe(16)`；
  产出 120×40 SVG 字符串（每字符随机旋转 ±25°、字号 24-30、y 抖动、3-4 条噪线）；
- 存储：内存 dict `{id: (sha256(code_lower), issued_monotonic)}` + threading.Lock；
  `CaptchaStore(ttl_seconds=300)`：`issue() -> (captcha_id, svg)`、
  `verify(captcha_id, code) -> bool`（pop 即单次使用；未知/过期/不匹配均 False；
  比较 `hmac.compare_digest`，输入 strip + lower）；
- 校验失败即消耗——每次尝试都要换新验证码（前端每次失败后自动刷新）。

### 模块二：失败计数与登录触发（`app/db/__init__.py`、`app/api/auth_service.py`）

- 新表 `auth_login_failures`：username（pk）、failures（int）、updated_at（挂 app.db Base，
  与 auth_users 同库，SessionStore create_all 顺带建表）；
- `AuthService(engine, captcha: CaptchaStore | None = None)`（默认 None 行为不变）；
- `login(username, password, captcha_id=None, captcha_code=None)`：
  1. `failures(username) >= 3` 时校验验证码：缺失/无效抛 `CaptchaRequiredError`（不计数）；
  2. 用户不存在或密码错 → `record_login_failure(username)`（**不存在用户也计数**，
     防存在性探测）→ 返回 None；
  3. 成功 → `clear_login_failures(username)` → 签发 token；
- `register` 成功后同样清零该用户名失败计数；阈值 `CAPTCHA_THRESHOLD = 3`。

### 模块三：API 端点（`app/api/app.py`、`schemas.py`、`deps.py`）

- `GET /api/auth/captcha`（公开）→ 200 `{captcha_id, image}`（image 为 SVG 字符串）；
- `POST /api/auth/register` 改用 `RegisterIn(AuthIn)`：`captcha_id`/`captcha_code` 必填
  （缺失 422）；验证码无效 → 400 `"验证码错误或已过期"`（先于重名 409 校验）；
- `POST /api/auth/login` 用 `AuthIn`（captcha 字段可选）：触发时缺失/无效 →
  401 `"验证码错误或已过期，请刷新后重试"`；密码错仍 401 `"用户名或密码错误"`（文案不变）；
- `Runtime` 增 `captcha: CaptchaStore`，`build_runtime` 组装后传入 AuthService。

### 模块四：前端登录层（`app/web/index.html`、`app.js`、`style.css`）

- 登录卡片加验证码行：`<img id="auth-captcha-img">`（点击刷新）+ 输入框；
- `showAuth()` 与每次失败后 `refreshCaptcha()`：GET /api/auth/captcha → img.src 用
  data URL（`data:image/svg+xml;utf8,` + encodeURIComponent）；
- `authAction` 请求体带 `captcha_id`/`captcha_code`；400/401 文案含「验证码」→
  清输入 + 刷新验证码；登录与注册共用同一验证码行。

## 验收标准（AC）

1. **AC1（验证码契约）**：GET /api/auth/captcha 返回 id + SVG（含 `<svg`）；4 位、
   无易混字符；大小写不敏感；单次使用（复用 → 无效）；过期 → 无效。
2. **AC2（注册）**：无验证码字段 → 422；验证码错误/过期 → 400；正确 → 201；
   重名在验证码通过后才 409。
3. **AC3（登录触发）**：失败 <3 次不强制（不带验证码可登录）；连续 3 次密码错后
   第 4 次无验证码 → 401 验证码文案、带正确验证码但密码错 → 401 密码文案且计数 +1；
   登录成功清零（下次登录不再要验证码）；验证码错误不增加失败计数。
4. **AC4（防枚举维持）**：密码错 401 文案不变；不存在用户同样累计失败并触发验证码。
5. **AC5（前端）**：登录卡片显示验证码图片、点击可刷新；注册带验证码成功；
   连续登录失败后卡在验证码错误提示与刷新（服务端行为由 AC3 保障，前端为接线）。
6. **AC6（回归）**：全量测试（266 + 17 快照基准 + 新增）全绿；ruff/mypy 干净。
7. **AC7（真实冒烟）**：HTTP 冒烟：错误验证码注册 → 400 → 刷新后正确注册 201；
   登录 3 次错密码 → 第 4 次无验证码 401 验证码文案 → 带验证码 + 正确密码 200；
   再登录无需验证码；旧验证码复用失败。

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 计划落 01-planned（本规格） | specs/01-planned/web/image-captcha.md | — |
| ② 验证码生成/校验 | app/api/captcha.py | tests/unit/test_captcha.py |
| ③ 失败计数 + 登录触发 | app/db/__init__.py + auth_service.py | 单测补 test_auth_service.py |
| ④ API 端点 + schemas/deps | app.py / schemas.py / deps.py | AC1-AC4（tests/api/test_auth.py 补） |
| ⑤ 前端登录层验证码 | index.html / app.js / style.css | AC5（test_frontend 适配） |
| ⑥ 真实冒烟 + 收尾 | — | AC7 |

## Definition of Done

- AC1-AC5 有测试且通过；全量回归（266 + 17 快照基准，新增后计数）全绿；ruff/mypy 干净。
- AC7 真实冒烟完成，摘录进实现备注。
- 本规格流转 03-implemented，01/02/03 README 与记忆同步。

---

# 实现备注（2026-10-22）

## 落地情况

按计划五步落地。全量 **282 测试 + 17 快照**全绿（266 基准 + 16 新增），ruff 干净、
mypy（app 52 文件）零告警，AC7 验证码冒烟 13/13。

## 关键实现

- **零新依赖 SVG 验证码**（app/api/captcha.py）：4 位字符集排除易混 0/O/1/I/L；
  逐字符随机旋转 ±25°、字号 24-30、y 抖动 + 3-4 条噪线，画成 120×40 SVG 返回；
  服务端内存只存 sha256(code)（`hmac.compare_digest` 校验，strip+lower），
  `CaptchaStore(ttl_seconds=300)` 线程安全，**verify 即 pop**——验证码一次一换，
  错一次也要换新（前端 400/401 文案含「验证码」时自动刷新）。
- **失败计数触发**：新表 `auth_login_failures`（username pk + failures + updated_at，
  SessionStore create_all 顺带建）；`CAPTCHA_THRESHOLD = 3`；
  `AuthService(engine, captcha=None)`——**缺省 None 行为不变**（既有测试零改动）。
  密码错 +1（不存在用户名也计数，防探测）；验证码错不计数；登录成功/注册成功清零；
  `CaptchaRequiredError` → API 层 401「验证码错误或已过期，请刷新后重试」。
- **端点**：`GET /api/auth/captcha` 公开；register 用 `RegisterIn(AuthIn)`（captcha 字段
  必填 → 缺 422），验证码校验**先于重名 409**（重名+错验证码 → 400，不泄露"已存在"）；
  登录密码错文案「用户名或密码错误」不变。
- **前端**：登录卡片验证码行（img 点击刷新 + 输入框），SVG 转 data URL 展示；
  authAction 请求体带 captcha_id/captcha_code；失败文案含「验证码」→ 清输入 + 刷新。

## 测试

新增 16 个（282 = 266 + 16）：
- tests/unit/test_captcha.py × 7：SVG 结构/字符集合法（50 次抽样无易混字符）/
  大小写与空白容忍/单次使用/未知 id/过期（ttl=0.05 注入）/独立发放；
- tests/unit/test_auth_service.py × 4：3 次失败触发+验证码错不计数+密码错仍计数+
  成功清零、未知用户同样触发（防探测）、注册清零；
- tests/api/test_auth.py × 5：captcha 端点契约、注册 422/400/201/验证码先于 409、
  登录触发全流程、验证码单次使用、未知用户触发；
- 既有测试适配：`_captcha(client)` 夹具（GET 验证码 + 直读 SVG `<text>` 解出答案），
  auth_headers 注册时自动带验证码；test_auth.py 直接注册调用补验证码。

## 真实冒烟（AC7，uvicorn + dev 库）

```
PASS  GET /api/auth/captcha → id + SVG（4 字符）  QZWC
PASS  注册缺验证码 → 422
PASS  注册错验证码 → 400  验证码错误或已过期
PASS  注册正确验证码 → 201 + token
PASS  重名 + 错验证码 → 400（不泄露已存在）  验证码错误或已过期
PASS  登录错密码第 1 次 → 401 密码文案
PASS  登录错密码第 2 次 → 401 密码文案
PASS  登录错密码第 3 次 → 401 密码文案
PASS  第 4 次无验证码 → 401 验证码文案  验证码错误或已过期，请刷新后重试
PASS  带验证码 + 正确密码 → 200
PASS  成功清零后登录不再要验证码
PASS  旧验证码复用 → 401 验证码文案
PASS  换新验证码 → 200
== 结果：13/13 通过 ==
```

## 遗留与后续

- SVG 验证码挡自动化脚本级别，非强对抗（OCR 可解）——本地 MVP 定位，服务器恢复后
  随加固升级（如接入图形验证码服务或行为校验）；
- 登录失败计数行对不存在用户名不清理（防探测的代价），量大时可加定期清扫。
