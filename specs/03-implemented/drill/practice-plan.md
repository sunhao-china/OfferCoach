# 练习计划：每日题单 + 逐题判分 + 进度连续天数

## 目标

新增「练习」页签：用户设定练习计划（岗位 / 难度 / 每日题数），系统每天从题库随机抽一份题单（不重复），逐题作答后复用 A1 面试同款 score.v2 判分（strong 档，三维度 + 点评 + 优缺点），答完展示参考要点对照；进度页显示今日 x/N、累计答题数、连续练习天数（streak）。三期 1497 题多岗位题库首次作为主动练习资产，与 B1 问答/模拟面试形成「练—问—考」闭环。

设计原则（沿 [[架构简化偏好]]）：练习不是对话流，**不走 langgraph**——独立端点 + 服务层直连题库与 LLM；页面/业务入口分离，不碰 chat/interview/resume/review 任何路径。

## 输入输出契约

输入：
- QuestionStore.random_pick(position/category/difficulty/qtype/exclude_ids, ORDER BY RANDOM())——抽 N 题 = 循环 N 次累加 exclude_ids；position=None 全岗位、difficulty=None 不限难度
- mock_interview/score.v2（$qa_blocks 批量、results[] 三维 correctness/depth/expression + comment/strengths/weaknesses）——练习判分单题 = 单块 qa_blocks，不新增提示词
- sqlite 会话库 data/offercoach_sessions.db（SessionStore 已建 auth 三表）；llm_strong；前端 5 页签单页壳（index.html nav tabs + page sections + app.js）

输出：
- app/db 新增三表（会话库，create_all 顺带建）：
  - drill_plans：id PK、user_id（UNIQUE，一人一计划）、position（可空=全岗位，存归一化值）、difficulty（entry/medium/hard/mixed）、daily_count（1-20，缺省 5）、created_at/updated_at
  - drill_sets：id（uuid str PK）、user_id、plan_date（"YYYY-MM-DD"）、question_ids（JSON list）、status（active/abandoned/completed）、completed_at、created_at
  - drill_attempts：id PK、set_id、question_id、answer、correctness/depth/expression（int 1-5）、feedback、strengths/weaknesses（JSON）、answered_at；UNIQUE(set_id, question_id)——重复作答覆盖更新（幂等）
- app/drill/service.py：DrillService
  - plan get/save：save 校验 daily_count 1-20、difficulty 枚举；position 归一化（""/"全部"/"all" → None；其余走 store.normalize_position）
  - today_set：取今日题单，无则先把所有未完成旧单置 abandoned → 按计划抽 N 题（随机不重复；题库不足取现有全部并在响应带 shortage 标志）；同日复用；答完最后一题置 completed
  - answer：answer 长度 <20 抛 ValueError；score.v2 判分（llm_strong）→ 解析 results[0] → upsert attempt；判分解析失败不落库、向上抛（API 转 502）
  - progress：today {done,total,completed}、total_attempts、total_completed_sets、streak（completed_sets 的 completed_at 日期集合，从今天（今天已完成）或昨天（今天未完成）起往前连续计数）
- API（全 Bearer + 归属校验，跨用户统一 404 不泄露）：
  - GET /api/drill/plan → plan 或 null；PUT /api/drill/plan {position, difficulty, daily_count} → plan
  - GET /api/drill/today → {plan_date, total, done, completed, shortage, remaining:[{question_id, category, difficulty, question}]}（已答题不给题面，进度由 done/total 表达）
  - POST /api/drill/answer {question_id, answer} → {score:{correctness,depth,expression}, feedback, strengths, weaknesses, reference_points, completed, progress:{done,total}}（答后返回参考要点对照）
  - GET /api/drill/progress → {today, total_attempts, total_completed_sets, streak}
  - 限流：answer 端点新增每用户 20/min 规则（drill），与 messages 同机制
- Runtime 新增字段 drill: DrillService（生产 build_runtime 装配 llm_strong + QuestionStore + 会话引擎；测试可整体替换，缺省 None=端点 503？——不，缺省 None 时端点构建报错即可，测试注入 Fake）
- 前端：新 tab「练习」+ page-drill：计划表单（岗位输入复用 datalist 建议、难度 select 含「混合」、每日题数 1-20 步进）；今日题单逐题卡片（textarea 作答 → 判分 → 展示三维评分/点评/优缺点/参考要点对照 → 下一题）；顶部进度条（今日 x/N）+ streak/累计徽标

## 验收标准（AC）

1. DrillService 计划单测：保存/读取/覆盖；daily_count 越界与 difficulty 非法抛 ValueError；position 归一化（"Python"/"C++" 别名命中、"全部"/"" → None）；默认 daily_count=5
2. DrillService 题单单测：生成 N 题不重复且满足计划过滤；题库不足取现有全部并 shortage=True；同日再取复用同一单；跨日取题时未完成旧单置 abandoned、已完成单保留；答完最后一题置 completed
3. DrillService 判分/进度单测：StubLLM 返回 score.v2 形状 → 三维 + comment 解析正确落库；answer <20 抛 ValueError；判分 JSON 非法 → 抛异常且不落库；重复作答覆盖更新；streak 连续/断档/今天未完成从昨天起算/无完成=0
4. API 测试：未登录 401；跨用户取对方题单/答题 404 且文案一致；全流程 PUT plan → GET today 生成 N 题 → POST answer ×N → completed → progress（streak/total 正确）；重复答题幂等
5. 全量回归：既有 434 测试 + 18 快照全绿（新增并入）；ruff/mypy 干净；前端视觉走查（本地起服务，练习页计划表单/答题/评分/进度流程可用）
6. 服务器部署 + 冒烟：真实账号建计划 → 取今日题单 → 答 2 题真实判分（云端 trace 可见）→ progress/streak 正确；跨账号隔离；既有五页（问答/面试/简历/复盘/回顾）冒烟不回归

## 实施计划

1. 规格 → 01-planned（本文件），开工流转 02
2. app/db 三表 + app/drill/service.py + 单测（AC1-3）
3. API 端点（schemas + app.py 注册 + Runtime.drill 装配 + 限流规则）+ HTTP 测试（AC4）
4. 前端练习页（index.html nav/page + app.js + style.css）
5. ruff/mypy + 全量回归 + 视觉走查（AC5）
6. deploy + 服务器冒烟（AC6）
7. 收尾：流转 03 + README + 记忆同步 + 中文报告

## Definition of Done

AC1-6 全部通过；三表只落在 sqlite 会话库（MySQL 零改动）；冒烟证据写入实现备注；README 与记忆同步；红线合规。

## 实现备注（2026-10-25）

**架构落点**：app/db 三表（DrillPlan/DrillSet/DrillAttempt，UNIQUE(set_id, question_id) 幂等 upsert）+ app/drill/service.py（DrillService，不走 langgraph，判分复用 mock_interview/score.v2 单块 qa_blocks + llm_strong）；4 端点注册在 app.py（Runtime.drill 装配，缺省 None=测试注入）；限流规则 "drill" 20/min/用户（与 messages 同机制）；前端「练习」页签（index.html page-drill + app.js 练习模块 + style.css）。

**测试（AC1-5）**：DrillService 单测 21（计划 5 / 题单 6 / 判分与进度 8，假时钟注入跨天/streak）；API 测试 9（401 / 校验 422 / 全流程 / 幂等 / 跨用户 404 统一文案 / 过短 400 / 判分失败 502 不落库 / 限流 429）；全量回归 **464 passed + 18 快照**（434 基线 + 30 新增），ruff/mypy 全干净。本地走查 **13/13**（真实注册 → 口语别名计划 → 2 题真实判分 → progress/streak → 跨账号 → 过短 400 → 未登录 401）。

**服务器部署（AC6）**：deploy.py 全量部署（题库 8 目录幂等导入 0 新增——MySQL 1497 不变、零改动）；drill 三表由 SessionStore create_all 随重启自动建在 sqlite 会话库。服务器冒烟 **12/12**：练习全流程 8 项（真实判分三维 1-5 + 点评 + 参考要点；进度 streak≥1；跨账号 404） + 既有回归 3 项（B1 问答、面试抽题原文、回顾页记录） + 首页含练习页签。**云端 trace 可见**：Langfuse observations v2 API 查得 GENERATION ChatDeepSeek、userId=冒烟账号、sessionId=`drill:q_python_...`（判分 LLM 调用挂 CallbackHandler，metadata 与 executor 同套 langfuse_* 约定）。

**判分严格性（冒烟实证）**：泛泛而谈的「答题框架」回答被真实 score.v2 打 1 分并点名漏掉的评分要点——判分真实锚定题库 reference_points，练习闭环有效。

**遗留**：无。练习计划无日更定时器——题单按需生成（首次访问当天生成，跨天自动作废旧单），无新调度负担。
