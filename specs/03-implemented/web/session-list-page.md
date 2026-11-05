# 计划：会话列表页（多会话管理：列表 / 继续 / 删除）

## 目标

补上「会话丢了找不回」的最后一块前端缺口：新增第五页「会话」，列出当前用户全部会话
（四业务统一），支持**继续**（回到对应业务页恢复该会话）与**删除**（清理练习废会话）。
后端补一个 `DELETE /api/sessions/{sid}` 端点（行 + 检查点线程一并清理，被复盘引用时拒绝）。

背景（已核实）：`GET /api/sessions?user_id=` 列表端点、`GET /api/sessions/{sid}/messages`
历史端点均已就绪且倒序稳定；前端四页用 localStorage `oc_session_<business>` 记当前会话，
但没有任何入口让用户找回已创建的会话——面试练完关页即「丢」，D 复盘选目标也只能靠下拉盲选。
纯前端 + 少量 API 增补，不碰图/执行层/会话表结构。

## 输入输出契约

### 模块一：DELETE 端点（API 增补）

- `DELETE /api/sessions/{session_id}` → `SessionDeleteOut {deleted: bool}`。
  - 会话不存在 → 404。
  - 被 review 会话引用（`target_session_id == sid`）→ 409「先删除引用它的复盘会话」。
  - 成功：删除 chat_sessions 行 + **该会话的全部检查点线程**（`SqliteSaver.delete_thread`
    幂等；线程按 executor 既有派生规则，见下），返回 `{"deleted": true}`。
- 检查点线程推导（`executor._session_thread_ids(session)`）：
  - chat：`[sid]`（根图）+ `[f"{sid}:{name}" for name in 五个子图名]`（chat 内路由出的子会话
    R6 派生了 `{sid}:{name}` 线程，逐一线程删除覆盖全部命名空间）。
  - interview：`[f"{sid}:mock_interview"]`；resume：`[f"{sid}:resume_analysis"]`；
    review：`[f"{target}:interview_review"]`（只删复盘自身线程，不碰复盘对象）。
- 删除后该会话的 GET 元数据 / 历史 / 发消息均 404；列表不再出现。

### 模块二：前端第五页（app/web/）

- index.html：顶栏新增页签「会话」（`data-page="sessions"`）+ `page-sessions` 区
  （标题栏含「刷新」按钮 + `#sessions-list` 容器）。
- app.js 新增（纯 vanilla，与既有风格一致）：
  - 常量 `SESSION_LABELS`（chat 问答 / interview 模拟面试 / resume 简历分析 / review 面试复盘）
    与 `DIFFICULTY_LABELS`（entry 入门 / medium 中等 / hard 困难）。
  - `loadSessions()`：GET `/api/sessions?user_id=`，渲染行：业务徽标 + 创建时间 +
    备注（interview：难度/岗位；review：复盘对象前 8 位）+ 「继续」「删除」按钮；空态文案。
  - `resumeSession(session)`：`rememberSession(business, id)` → 清空对应消息区 →
    `switchPage(business)` → `loadHistory(business)`（跨页恢复历史，进行中面试经
    checkpointer 续上）。
  - `deleteSession(session)`：`confirm()` 后 DELETE；若删除的是该业务当前会话 →
    `forgetSession` + 清消息区；刷新列表；toast 结果（409 时展示 detail）。
  - 初始化与切到「会话」页签时自动 `loadSessions()`（新会话即时可见）。
- style.css：`.sessions-list` 行/徽标/按钮样式（约 20 行）。

### 模块三：不动的东西

四页既有行为、SSE 流式、会话创建/列表 GET、复盘下拉（沿用同一列表端点）、
checkpointer 写入路径全部不变。

## 验收标准（AC）

1. **AC1（列表数据）**：`GET /api/sessions?user_id=` 对四业务会话均返回渲染所需字段
   （business/created_at/difficulty/position/target_session_id），倒序稳定（既有行为回归）。
2. **AC2（DELETE 行+检查点）**：删除有消息的 interview 会话后：`GET /api/sessions/{sid}` 404、
   列表不含它；`cp.db` 的 checkpoints/writes 表无该会话线程残留（含 chat 路由派生的
   `{sid}:*` 线程）。
3. **AC3（DELETE 引用保护）**：删除被 review 引用的 interview 会话 → 409 且行与检查点俱在；
   先删 review 再删 interview → 均 200。
4. **AC4（DELETE 404）**：删除不存在的会话 → 404。
5. **AC5（前端契约）**：index.html 含「会话」页签与 `#sessions-list`；app.js 引用的一切
   `/api` 路径仍在路由表内（既有 AC3 机制复用）。
6. **AC6（回归）**：全量 210 测试 + 13 快照全绿；ruff/mypy 干净。
7. **AC7（真实冒烟）**：uvicorn + curl：建 interview 会话发一轮 → 列表含它 → 读回历史 →
   DELETE → 列表消失、GET 404；首页 200。

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 计划落 01-planned（本规格） | specs/01-planned/web/session-list-page.md | — |
| ② 存储层 delete/referenced | app/api/session_store.py | tests/api/test_session_store.py 增补 |
| ③ 检查点线程推导 + DELETE 端点 | app/api/executor.py、schemas.py、app.py | AC2-AC4（新 tests/api/test_session_list_page.py） |
| ④ 前端第五页 | app/web/index.html、app.js、style.css | AC5（tests/api/test_frontend.py 增补） |
| ⑤ 回归 + 冒烟 | — | AC6、AC7 |
| ⑥ 收尾 | 流转 03-implemented + 实现备注 + README/记忆同步 | — |

## Definition of Done

- AC1-AC5 全部有测试且通过；全量回归（210 测试 + 13 快照）全绿。
- ruff / mypy 干净。
- AC7 uvicorn 真实冒烟通过（记录结果）。
- 本规格流转 03-implemented，01/02/03 README 与记忆同步。

## 实现备注（2026-10-22）

**验收结果**：AC1-AC7 全绿。全量 **217 测试 + 13 快照**（新增 7：store 级 2、API 级 5）
通过；ruff/mypy 干净。

**关键实现点**：

- 检查点线程清理按 executor 既有派生规则收口：chat 删 `[sid] + {sid}:{name}×5`（根图内
  路由出的子会话 R6 派生线程），直连业务删 `{sid|target}:{子图名}`；review 只清复盘自身
  线程、不碰复盘对象（测试逐条断言 cp.db 两表无残留）。
- 删除保护：`referenced_by` 计数 > 0 → 409（「先删除对应的复盘会话」），保证复盘目标
  永不悬空——比级联删除更简单且行为显式。
- `SqliteSaver.delete_thread`（langgraph 1.2.16）幂等：线程不存在无操作，删行与删线程
  顺序无关紧要。
- 前端「继续」复用 localStorage `oc_session_<business>` 机制：写入会话 id → 切页 →
  `loadHistory` 恢复；进行中面试经 checkpointer 自然续上，零后端改动。

**真实冒烟记录**（uvicorn + curl，UTF-8 文件体——Git Bash 命令行中文仍是 GBK 会被 400，
坑 10 再次应验）：

- 首页含「会话」页签与 `#sessions-list`；
- 建 interview 会话（entry）发「开始」→ 真实 deepseek 开场白 + 第 1 题，历史
  [user, assistant, assistant]；
- 列表含该会话（business/difficulty 齐全）→ DELETE 200 `{"deleted": true}` →
  列表清空、GET 404。

**实现清单**：`session_store.delete/referenced_by`、`executor._session_thread_ids +
delete_checkpoint_threads`、`app.py DELETE` 端点 + `SessionDeleteOut`、前端第五页
（index.html 页签 + app.js 渲染/继续/删除 + style.css 列表样式）、test_frontend 契约增补。
未动：四页既有行为、GET 列表/历史端点、检查点写入路径、会话表结构。
