# 计划：面试体验打磨——题库要点/追问接入面试与复盘全链路

## 目标

面试链路目前「只用题面不用题资」：ask 抽题后只保留 question_id/question，题库的
reference_points（评分要点）与 followups（递进追问）从不进入 judge/score/report/复盘
（题库扩充二期后 497 题全部带这两个字段）。后果：追问由 judge 凭空编造、评分不看要点
覆盖、复盘点评说不出「漏了哪个要点」。本步把这两个字段接入 A1（ask→judge→score→
report）与 D（load→per_question），让追问/评分/点评有据可依。

边界（架构简化偏好）：**不改流程拓扑、不改任何 LLM 输出 JSON 形状**，只改节点输入与
提示词内容；summary.v1 保持不动（其输入 blocks 被接地后输出已够用）。成本：无 LLM 批量
调用，仅真实冒烟几轮 deepseek。

## 输入输出契约

### 模块一：ask 携带题资（`app/graphs/workflows/mock_interview.py`）

- state 新增 `current_reference_points: list[str]`、`current_followups: list[str]`；
- ask 抽到题后从 Question 行读 reference_points/followups（JSON 列，恒为 list）写入
  state；每次 ask 重置（与 current_question_id 同步生命周期）；
- 其余输出不变。

### 模块二：judge v2（`app/prompts/templates/mock_interview/judge.v2.yaml`）

- 新增变量 `$reference_points`（编号列表，「（无）」兜底）、`$bank_followups`（题库
  追问编号列表）；judge 节点渲染时传入（state.get 兜底空串）。
- 决策规则重写为要点对照式：
  1. 用户明确结束 → action=end；
  2. 不会/空泛/答非所问 → move_on + 要点讲解（feedback 引用未覆盖要点，≤3 句）；
  3. 对照要点有未覆盖、且追问未满 → followup：**优先使用/微调题库追问**（沿回答
     深挖或给出反例/权衡），题库追问与回答重复时再自拟；
  4. 要点基本覆盖或追问已满 → move_on，feedback 可一句点评或 null。
- 输出 JSON 形状不变：`{action, followup, feedback}`。

### 模块三：score v2（`app/prompts/templates/mock_interview/score.v2.yaml`）

- score 节点对每条未评分 answer 用 `store.get(question_id)` 取 reference_points，编入
  qa_blocks（「评分要点：1. … 2. …」，取不到写「（未提供）」）；
- 提示词要求 correctness 锚定要点覆盖程度；输出 JSON 形状不变。

### 模块四：report v2（`app/prompts/templates/mock_interview/report.v2.yaml`）

- report 节点 score_blocks 每条追加「亮点：… / 不足：…」（来自 scores 的
  strengths/weaknesses，缺失则省略该行）；
- 报告 200 字 → 300 字左右，结构不变（总体/均分/亮点/短板/练习建议）。

### 模块五：复盘接地（`app/graphs/workflows/interview_review.py` + per_question.v2.yaml）

- `build_interview_review_graph` 新增 `store: QuestionStore` 参数（composition 传入）；
- load 对每条 record 用 `store.get(question_id)` 取 reference_points 存入 record；
- `interview_review/per_question.v2.yaml`：qa_blocks 每条追加「评分要点」，点评规则
  要求指出未覆盖要点；输出 JSON 形状不变；
- summary.v1 不动。

### 模块六：快照迁移

- 既有 v1 快照测试（mock_interview 4 个 + interview_review 1 个）改为
  `render(..., version=1)` 显式锁定（v1 资产保留）；
- 新增 v2 渲染快照测试（judge/score/report/per_question 各 1）。

## 验收标准（AC）

1. **AC1（ask 携带题资）**：state 含 current_reference_points/current_followups，
   与所抽题一致；换题后重置。
2. **AC2（judge v2）**：渲染含要点与题库追问（快照锁定）；既有 judge 解析/兜底
   （parse 失败 move_on、追问满转 move_on）行为不变——既有测试全绿。
3. **AC3（score v2）**：qa_blocks 含评分要点；store.get 取不到时「（未提供）」兜底；
   分数钳制与去重逻辑不变。
4. **AC4（report v2）**：score_blocks 含亮点/不足；scores 缺 strengths 时不炸。
5. **AC5（复盘接地）**：load 的 records 含 reference_points；per_question v2 渲染含
   要点（快照锁定）；store 注入后 composition 直连路径可用；无记录兜底不变。
6. **AC6（回归）**：全量 249 测试 + 13 快照（更新后）全绿；ruff/mypy 干净。
7. **AC7（真实冒烟）**：dev DB（497 题）下真实面试一轮（entry，deepseek strong）：
   开场 → 答题 → 追问（应体现题库追问/要点对照）→ 评分 → 报告；再跑一次复盘：
   点评含「未覆盖要点」式具体内容。记录输出摘录。

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 计划落 01-planned（本规格） | specs/01-planned/interview/experience-polish.md | — |
| ② ask 携带题资 | mock_interview.py（state + ask） | AC1（test_mock_interview 增补） |
| ③ judge v2 | judge.v2.yaml + judge 节点变量 | AC2 + 快照（v1 pin version=1） |
| ④ score v2 | score.v2.yaml + score 节点 | AC3 + 快照 |
| ⑤ report v2 | report.v2.yaml + report 节点 | AC4 + 快照 |
| ⑥ 复盘接地 | interview_review.py（store 注入 + load）+ per_question.v2.yaml + composition.py | AC5 + 快照 |
| ⑦ 真实冒烟（deepseek strong，几轮） | — | AC7 |
| ⑧ 收尾 | 流转 03 + 实现备注 + README/记忆同步 + 报告 | AC6 |

## Definition of Done

- AC1-AC5 全部有测试且通过；全量回归（249 测试 + 13 快照，v1 显式锁定 + v2 新增）全绿。
- ruff / mypy 干净。
- AC7 真实冒烟完成：面试与复盘输出摘录进实现备注。
- 本规格流转 03-implemented，01/02/03 README 与记忆同步。

## 实现备注（2026-10-22）

基线 249 测试 + 13 快照；完成后 **254 测试 + 17 快照**（v1 显式锁定 5 个 + v2 新增 4 个），
ruff/mypy 干净。改动清单（对应模块一~六）：

1. [mock_interview.py](../../../app/graphs/workflows/mock_interview.py)：state 增
   `current_reference_points`/`current_followups`；ask 抽题时从 Question 行带出并每题重置；
   新增 `_numbered` 助手；judge 注入要点+题库追问、score 按 question_id 回查要点编入
   qa_blocks、report 每题追加亮点/不足行。
2. [judge.v2.yaml](../../../app/prompts/templates/mock_interview/judge.v2.yaml)：
   规则 2 空泛/答非所问 → move_on + 要点讲解；规则 3 对照要点有缺口且未满 → followup
   **优先用题库追问**。
3. [score.v2.yaml](../../../app/prompts/templates/mock_interview/score.v2.yaml)：
   correctness 锚定要点覆盖。
4. [report.v2.yaml](../../../app/prompts/templates/mock_interview/report.v2.yaml)：
   300 字左右、逐题亮点/不足。
5. [interview_review.py](../../../app/graphs/workflows/interview_review.py)：
   `build_interview_review_graph` 增 `store` 参数（composition 传入）；load 按 question_id
   回查 reference_points 入 records；per_question 块增「评分要点」。
6. [per_question.v2.yaml](../../../app/prompts/templates/interview_review/per_question.v2.yaml)：
   weaknesses 优先写「漏了哪个要点」。summary.v1 不动。

AC1-AC6 全绿。AC7 三轮真实冒烟（deepseek strong 主体 + cheap 分类）摘录：

**轮 1（dev DB 497 题，根图生产路径，2 题）**——judge 反馈直接引题库要点：
「你回答的是线程池，和本题无关。本题要点：Throwable 分为 Error（OOM、StackOverflow 等
JVM 级错误…）…」；评分：「正确性 1 / 深度 1 / 表达 2（…未覆盖任何评分要点。）」；
报告短板：「第 1 题未答出 Throwable/Error/Exception 的体系划分…第 2 题未涉及三次握手、
半开连接、伪造源 IP、SYN/ACK 重传与资源耗尽等核心机制」；复盘：「四个评分要点全部缺失」、
「因果链完全未涉及」——每处都点名具体漏掉的要点。面试结束后追问自动路由回 B1 知识问答
（R5 释放正常）。

**轮 2（dev DB，抽中分布式锁续期题）**——judge 要点讲解几乎逐字引用题库要点：
「Redisson 未显式指定超时时间时启用看门狗，默认每 10 秒续期一次…（看门狗机制、10 秒
续期/30 秒过期、持有校验、Lua 原子续期、显式超时不启用）」——证明要点来自题库而非编造。

**轮 3（种子 1 题「线程池核心参数与执行流程」，要点/题库追问可控）**——部分作答后 judge
连续追问，全部对照要点缺口：「keepAliveTime 需要配合哪个参数使用才能生效？另外，请完整
说一下任务提交后线程池的执行流程」「四种拒绝策略分别是什么，各自适合什么场景？」；答全后
move_on + 要点讲解（队列选型/OOM 风险/核心线程数公式=要点 4）；评分 comment 逐条对照：
「遗漏了 keepAlive 的时间单位，没有讲任务提交流程（核心线程→队列→最大线程→拒绝），
拒绝策略只点名两种且无适用场景，也未涉及队列选型与无界队列 OOM 风险」；报告短板同源。

教训：直连子图 invoke 的检查点落 ns="" 且 thread 名不含 `:mock_interview` 后缀，复盘 load
读不到——真实冒烟必须走 `build_app_graph` 根图（坑 #1 的冒烟版复现，轮 1 首版驱动因此
重写）。三轮冒烟合计约 30 次 LLM 调用，费用约 ¥1-2。
