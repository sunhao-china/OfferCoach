# 历史回顾页（练习成果浏览）

## 目标

新增「回顾」页签：按时间倒序列出**已完成的练习**（模拟面试 / 简历分析 / 面试复盘），每条带业务徽标、时间、面试难度/岗位等元信息与报告预览；点击进入详情，以气泡形式完整回放该会话的问答与最终报告。**零新增存储**：记录由 chat_sessions 元数据 + checkpointer 既有状态派生（架构简化偏好）；chat 问答会话不进回顾页（它是连续问答，由「会话」页管理）。

## 输入输出契约

输入：
- chat_sessions 表：business / created_at / difficulty / position / target_session_id（已有，session_store 已提供 list/get）
- checkpointer：interview 会话顶层直连落 ns=""、线程 `{sid}:mock_interview`（read_checkpoint 双 ns 兜底已有）；**state 含 phase 字段**（mock_interview 的 PHASE_DONE = 已出报告，可靠完成信号，不做内容嗅探）；resume/review 快照内有无 AI 消息即完成信号
- 既有端点 GET /api/sessions（全量会话）与 GET /api/sessions/{sid}/messages（完整历史，详情复用）

输出：
- app/api/history.py：`build_history_records(checkpointer, rows)` → 每行一个记录（completed 判定 + preview = 最后一条 AI 消息截断 120 字）
- GET /api/history 端点（Bearer 鉴权 + 按用户过滤，复用 runtime.sessions.list）
- schemas：HistoryRecordOut {session_id, business, created_at, difficulty, position, target_session_id, completed, preview} + HistoryListOut
- 前端：新页签「回顾」+ 列表（业务筛选 chips + 刷新）+ 详情回放（复用 GET messages，气泡渲染，返回按钮）；index.html / app.js / style.css 增量修改

## 验收标准（AC）

1. build_history_records 单测：interview 用 fake 快照 state phase=done/answering 判 completed 对错；resume/review 有 AI 消息即 completed；preview 取最后一条 AI 消息且截断 120 字；无快照（空会话）不崩；排序时间倒序
2. GET /api/history API 测试：未登录 401；只返回本人非 chat 会话；记录形状完整（含 difficulty/position/target_session_id 透传）；completed 与 preview 正确（真实 checkpointer 夹具）
3. 前端：回顾页签可见；加载显示记录列表（徽标/时间/预览）；筛选 chips 按业务过滤；点「查看」回放该会话消息（user/assistant 气泡）；返回列表
4. 全量回归：既有 357 测试 + 17 快照全绿（零行为不变量；新增测试并入）
5. 服务器部署 + 冒烟：deploy 重跑；真实面试一轮（结束出报告）→ GET /api/history 该记录 completed=true 且 preview 为报告开头；未完成面试（只开场）completed=false；review/resume 记录可见

## 实施计划

1. 规格 → 01-planned（本文件，开工流转 02）
2. executor 抽 read_snapshot（read_history 复用）+ history.py + schemas + 端点（AC1/AC2）
3. 前端页签/列表/筛选/详情（AC3）
4. ruff/mypy + 全量回归（AC4）
5. deploy + 服务器冒烟（AC5）
6. 收尾：流转 03 + README + 记忆同步 + 中文报告

## Definition of Done

AC1-5 全部通过；冒烟证据写入实现备注；README 与记忆同步；零新增存储/依赖。

## 实现备注（2026-10-25）

**流程**：executor 抽 `read_snapshot`/`snapshot_messages`（read_history 复用）→ app/api/history.py 记录派生 → GET /api/history → 前端「回顾」页签（列表/筛选/详情回放）→ 全量回归 → 服务器部署 + 冒烟。

**关键决策**：
- **零新增存储**：记录由 chat_sessions 元数据 + checkpointer 既有状态派生；详情直接复用 GET /api/sessions/{sid}/messages 回放，无新表、无新列、无新依赖
- **completed 判定不做内容嗅探**：interview 看快照 state.phase == "done"（PHASE_DONE，报告节点的真实状态位）；resume/review 看是否有 AI 消息（管道一次跑完）；chat 会话不进回顾页（连续问答，归会话页管）

**服务器证据**（8.137.174.74，2026-10-25）：
- deploy 重跑绿（healthz 200，8002）；题库 seed 幂等
- AC5 冒烟 6/6：真实面试一轮（entry，答 1 题后结束口令出报告）→ /api/history 该记录 completed=true、preview 与报告开头一致、position 透传「Java 后端」；未完成面试 completed=false、preview 空；简历分析 completed=true；chat 会话不出现在记录中；首页含 data-page="history" 页签

**测试**：单测 9 个（completed/preview 截断/空消息回退/chat 过滤/排序/元数据透传/双 ns 兜底）+ API 5 个（401/业务过滤/完成面试/未完成/用户隔离）；全量回归 371 + 17 快照全绿（357 → 371，新增 14）。

**AC 对照**：AC1 ✓ 单测；AC2 ✓ API 测试；AC3 ✓ 前端页签/列表/筛选/详情（test_frontend 页签清单加 history）；AC4 ✓ 371 + 17；AC5 ✓ 服务器冒烟 6/6。

