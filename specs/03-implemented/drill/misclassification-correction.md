# 错题纠正：练习页一键重新识别题目的岗位与类别

## 目标

新增练习页「错题纠正」能力：用户在每日题单中看到某道题被分错了岗位/类别（如 Spring 题被标成 python_web——2026-10-27 已手工修过 5 条此类数据），点击「分类有误？」按钮，系统用 LLM（cheap 档，分类任务）重新识别题面所属岗位（position）与类别（category），识别结果必须落在题库实际存在的岗位/类别枚举内，通过后直接纠正 question_bank 记录。

设计原则（沿 [[架构简化偏好]]）：
- 挂在现有 DrillService 上（练习不走 langgraph 的既有决策），不新增工作流；
- **question_id 保持不变**（md5 历史派生值，练习答题 drill_attempts 按 question_id 引用）——只改 position/category 两列，与手工修正脚本 scripts/fix_v3_misclassified.py 的决策一致，杜绝悬挂引用；
- 识别失败/越界不写库（fail-closed，与判分 502 同策略）；识别结果与现分类一致则返回 changed=false 不写库；
- 岗位/类别枚举**动态取自题库本身**（SELECT DISTINCT），新增类别无需改代码，也不硬编码一份会漂移的清单。

## 输入输出契约

输入：
- DrillService 既有成员：store（QuestionStore，生产 MySQL / 测试 sqlite）、engine（会话库）、registry、llm（strong，判分用）
- 新增提示词 `question_bank/reclassify.v1.yaml`（$positions / $category_map / $question 三变量；只输出 `{"position": "...", "category": "..."}` JSON）
- 新增 QuestionStore 方法：
  - `position_categories() -> dict[str, list[str]]`：SELECT DISTINCT position, category ORDER BY position, category，全库岗位→类别清单（提示词渲染 + 结果校验共用）
  - `reclassify(question_id, position, category) -> Question | None`：改写 position/category 两列并 commit（question_id 不变）；无此记录返回 None
- llm_classify（cheap 档，deps.py 已构建）注入 DrillService——分类走 cheap、判分仍走 strong，两档不混

输出：
- DrillService.reclassify(user_id, question_id) -> dict：
  - 前置校验：question_id 必须在该用户当前 active 题单内（与 answer 同口径）→ 否则 DrillNotFoundError（API 转 404 统一文案「题目不存在」）
  - 题库无此记录 → DrillNotFoundError；LLM 输出非 JSON / 缺字段 / position 或 category 不在库内枚举 → RuntimeError（API 转 502，不写库）
  - 返回 `{question_id, position, category, changed, previous: {position, category}}`
- API：POST /api/drill/reclassify {question_id} → DrillReclassifyOut；Bearer + 归属校验；新增限流规则 "reclassify" 10/min/用户（控 LLM 成本）
- today_view 的 remaining 每项增加 "position" 字段（题面卡片要展示岗位，纠正功能才有可见入口）；DrillQuestionOut 同步加 position: str
- 追踪：复用 _drill_trace_config，tag 参数化（判分 "drill"、纠正 "drill-reclassify"），sessionId 仍 `drill:{question_id}`
- 前端：drillCard 的 meta 行改为「难度 · 岗位 · 类别」+ 小按钮「分类有误？」；点击 → POST → changed 时更新 meta 文案并 toast「已纠正：Python · spring」、未变化 toast「AI 识别结果与当前分类一致」；答完题后卡片 meta 保留（renderDrillResult 只移除 textarea/actions），按钮在判分前后都可用；样式新增 .drill-reclassify（meta 行内幽灵小按钮）

## 验收标准（AC）

1. Store 单测：position_categories 全库去重升序；reclassify 改写两列、question_id 不变、不存在返回 None
2. DrillService.reclassify 单测：changed 路径落库且返回 previous；识别结果与现分类一致 changed=false 不写库；不在题单内/题库无记录抛 DrillNotFoundError；非 JSON、缺字段、position 或 category 越界（如库内无此岗位/类别组合）抛 RuntimeError 且不写库；分类走 llm_classify（llm 桩零响应时 reclassify 仍可用、判分桩零响应时 reclassify 直接失败——两档互不串用）
3. API 测试：未登录 401；happy path 200 形状（含 previous）；跨用户/未知题 404 且文案与 answer 一致；解析失败 502 不落库；reclassify 限流 10/min 第 11 次 429
4. 既有测试更新：today remaining 键集合两处断言加 "position"
5. 全量回归：464 基线 + 新增全绿；ruff/mypy 干净；前端视觉走查（练习页 meta 显示岗位、按钮可用、纠正后文案更新）
6. 服务器部署 + 冒烟：真实账号抽题 → 点纠正（云端 trace 可见 tag=drill-reclassify）→ 库里该题 position/category 已变、question_id 不变、练习记录不悬挂；未变化题目冒烟 changed=false；既有练习全流程不回归

## 实施计划

1. 规格 → 01-planned（本文件），开工流转 02
2. store 方法 + reclassify.v1.yaml + DrillService.reclassify（llm_classify 注入，deps.py/测试装配同步）+ 单测（AC1-2）
3. API 端点（schemas + app.py + 限流规则 "reclassify"）+ HTTP 测试（AC3-4）
4. 前端（drillCard meta + 按钮 + reclassifyDrill + style.css + 缓存版本号 +1）（AC5 走查）
5. ruff/mypy + 全量回归 + 本地视觉走查（AC5）
6. deploy + 服务器冒烟（AC6）
7. 收尾：流转 03 + README/记忆同步 + 中文报告

## Definition of Done

AC1-6 全部通过；position/category 为唯一被改列（question_id 不动、drill_attempts 零迁移）；校验失败一律不写库；分类走 cheap 档；冒烟证据写入实现备注；README 与记忆同步；红线合规。

## 实现备注（2026-10-27）

**架构落点**：新增提示词 `question_bank/reclassify.v1.yaml`（$positions/$category_map/$question 三变量，只输出 `{"position","category"}` JSON，并点名"Spring/Java 题不能因出现 Python 字样归 python_backend"）；QuestionStore 新增 `position_categories()`（SELECT DISTINCT 全库岗位→类别，动态枚举不硬编码）与 `reclassify()`（只改 position/category 两列，question_id 不变——练习答题 drill_attempts 按题 ID 引用，零悬挂）；DrillService 新增 `reclassify()`（题单归属校验 → cheap 档识别 → 枚举校验 → 同值不写库 changed=false；解析失败/越界 RuntimeError 不写库）；`_drill_trace_config` 增加 tag 参数（判分 "drill" / 纠正 "drill-reclassify"）。DrillService 构造器新增 llm_classify 参数（deps.py 装配 cheap 档——分类走 cheap、判分仍走 strong，两档不串用，单测锁定）。API：POST /api/drill/reclassify（404 与 answer 同文案「题目不存在」、502「分类识别暂不可用」、限流规则 "reclassify" 10/min/用户）；today_view remaining 每项新增 position 字段（DrillQuestionOut 同步）。前端：drillCard meta 行改为「难度 · 岗位 · 类别」+「分类有误？」小按钮（判分前后都可用——meta 不被 renderDrillResult 移除）；changed 时更新 meta 文案并 toast 已纠正，未变化 toast 一致；缓存版本 style.css?v=3 / app.js?v=4。

**测试（AC1-5）**：DrillService 单测 +7（移动落库且题 ID 不变 / 同值 changed=false 不写库 / 分类档与判分档不串用 / 不在题单 / 题库无记录 / 非 JSON·缺字段·岗位越界·类别越界四连 RuntimeError 不写库 / 纠正后再识别幂等）；API 测试 +4（happy path 纠正落库+再识别 changed=false / 跨用户·未知题 404 统一文案 / 解析失败 502 不写库 / 限流 10/min 第 11 次 429）；既有 2 处 remaining 键集合断言加 "position"。全量回归 **475 passed + 18 快照**（464 基线 + 11 新增），ruff/mypy 全干净。本地走查（真实 cheap LLM）：q_python_framework_003 临时翻错 python_web → 识别 python_db changed=true 落库 → 再识别 changed=false → 恢复原分类；401/404 正常；静态资源 v=4/v=3 内容核对通过。

**服务器部署与冒烟（AC6）**：deploy.py 全量部署（8 题库目录幂等导入 0 新增——MySQL 1497 不变），systemd 重启 healthz 200。服务器冒烟 **8/8**：smoke_rc_76181 账号 → PUT plan → remaining 带 position → POST reclassify 200 → 401/404 统一文案 → 既有判分不回归（真实 score.v2 严格打分 1/1/2）。**冒烟真实纠正了一条错题**：随机抽中的 q_python_web_027（Django @transaction.atomic + MySQL REPEATABLE READ 余额扣款事务题）被识别为 python_db 并落库——纠正合理，功能在生产上首次真实生效（v3 python_web 类别可能存在零星错标，用户可点击按钮自行纠正）。公网验证：http://8.137.192.231:8002 服务 app.js?v=4 与 style.css?v=3，公网登录+取计划 200。**云端 trace 可见**：v2 observations API 查得 2 条 GENERATION（sessionId `drill:q_python_web_027`=纠正调用、`drill:q_python_concurrent_039`=判分调用，userId=smoke_rc_76181）。

**踩坑记录**：Langfuse 云 `/api/public/traces` 已废弃（2026-11-16 下线，且限流 429）——trace 验证一律改走 `GET /api/public/v2/observations?type=GENERATION&fromStartTime=…&toStartTime=…`（唯一实时数据通道）。

**遗留**：无。批量复查其余 python_web 题未做（需用户拍板——单题纠正由用户触发，批量重分类需单独授权）。
