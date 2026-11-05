# 计划：前端页面（四页会话，无构建静态托管）

> 状态：已实现（2026-10-21）。依据：用户决策（2026-10-21）——页面/业务入口分离；架构简化偏好（不引入构建链）；specs/03-implemented/api/web-api.md（四种业务会话的 API 契约）。

## 目标

给 Web API 层配上四页前端：模拟面试 / 简历分析 / 面试复盘 / 问答，每页对应一种 business 会话（页面即业务分离，与 API 层直连规则一致）。技术选型：**vanilla HTML/CSS/JS 单页应用，无 node、无构建**，由 FastAPI 静态托管（一个服务、一条命令跑起来）。配套补一个会话列表端点（复盘页选择目标面试会话用）。

## 输入/输出契约

### 模块一：静态资源 `app/web/`（新增）

- `index.html`：顶栏四页签（模拟面试/简历分析/面试复盘/问答）+ 每页容器（消息区 + 输入区 + 新建会话按钮）
- `app.js`：页面状态与 API 调用（约 200 行 vanilla）
  - 每页记住当前会话 id（localStorage key `oc_session_<business>`）；「新建会话」清空并重新创建
  - user_id：首次访问生成随机访客 id 存 localStorage，随 SessionCreate 发送
  - 发送消息 → POST messages，渲染双方消息；等待期间显示加载态；错误 toast
  - 切页/加载时 GET messages 恢复历史
  - 复盘页：GET /api/sessions?user_id= 列出面试会话进下拉框，选定后创建 review 会话（target_session_id）
  - 简历页：textarea 粘贴简历
- `style.css`：聊天气泡（user/assistant 分侧）、页签、加载态（约 100 行）
- 托管：`app/api/app.py` 挂载 `/static`（StaticFiles），`GET /` 返回 index.html（FileResponse）

### 模块二：会话列表端点（API 增补）

- `GET /api/sessions?user_id=<可选>` → `SessionListOut {sessions: [SessionOut]}`，按 created_at 倒序
- `SessionStore.list(user_id)` 增补；无 user_id 时返回全部（MVP）

### 模块三：页面行为契约

| 页面 | business | 会话行为 |
|---|---|---|
| 问答 | chat | 走根图路由（可顺带路由到其他业务） |
| 模拟面试 | interview | 直连 mock_interview；进行中会话跨页签/刷新保持（checkpointer 按会话隔离） |
| 简历分析 | resume | 直连一次性管道；每轮完整报告 |
| 面试复盘 | review | 先选目标面试会话 → 建 review 会话 → 直连复盘 |

## 验收标准

- AC1（静态托管）TestClient：GET / 返回 index.html；GET /static/app.js、/static/style.css 均 200
- AC2（列表端点）创建 3 个会话（含不同 user）后：全量列表含 3 条且倒序；user_id 过滤只返回该用户
- AC3（前端-API 契约一致）测试读取 app.js，提取全部 fetch 路径模板（`/api/...`），归一化 `${...}`/`{param}` 后逐一断言存在于 FastAPI 路由表
- AC4（回归）全量 161 + 11 快照不受影响；ruff/mypy（app 全量）干净
- AC5（真实冒烟）uvicorn 起服务：/ 与 /static 资源 200；真实 deepseek 经 API 走一轮问答页会话（curl 复现前端调用序列）

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 列表端点 | app/api/schemas.py、session_store.py、app.py | AC2（tests/api/test_web_api.py 增补） |
| ② 静态托管 | app/api/app.py（StaticFiles + / 路由） | AC1 |
| ③ 前端三件套 | app/web/index.html、app.js、style.css | AC3（tests/api/test_frontend.py） |
| ④ 回归 + 冒烟 | — | AC4、AC5 |
| ⑤ 收尾 | 规格流转 03-implemented、索引同步 | — |

## Definition of Done

- [x] AC1-AC3 全部有测试且通过；全量回归（161 + 11 快照）全绿
- [x] ruff / mypy（app 全量）干净
- [x] AC5 uvicorn 真实冒烟通过（记录结果）
- [x] 本规格流转 03-implemented，01/02/03 README 同步

## 实现备注（2026-10-21）

- 按计划五步落地，新增 3 个测试（164 = 161 + 3），11 快照不变；ruff/mypy 全清。
- **created_at 微秒精度**：SessionStore.create 改由 Python 侧写入 `datetime.now()`（不再依赖 SQLite server_default 的秒级 CURRENT_TIMESTAMP），保证同秒连建的会话倒序稳定（AC2 排序断言依赖）。
- **冒烟发现并修复——ReAct 中间步泄漏进历史**：chat 会话经根图路由后，checkpoint 里含工具调用 AIMessage（content 为空），read_history 原样返回会在前端渲染空白气泡。修复：`executor.read_history` 跳过带 `tool_calls` 的 AIMessage；`test_chat_session_uses_router` 增补历史形状断言（roles 恰为 user/assistant）。
- **AC5 真实冒烟记录**（scripts/run_server.py + curl，UTF-8 文件体）：
  - GET /、/static/app.js、/static/style.css 均 200；
  - 真实 deepseek 问答页两轮：HashMap 扩容机制 → 追问位运算迁移（回答正确引用上一轮上下文）；
  - 修复后历史接口返回恰为 [user, assistant, user, assistant]，零空内容。
- AC3 契约测试覆盖的路径：`/api/sessions`、`/api/sessions/{sid}/messages`（app.js 中 `${API_SESSIONS}?user_id=` 拼接复用前者路由，属同一端点）。
