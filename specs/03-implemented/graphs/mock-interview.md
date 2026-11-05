# 计划：A1 模拟面试工作流（mock_interview）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：docs/system-design.md §4.2 Workflow 模式；docs/business-scope.md §3-A1。

实现备注（相对计划的调整，均已测试锁定）：
- 增加 `checkpointer` 参数：多轮状态持久化必需，生产上与根图共享同一实例（config 透传）
- score 去重以答案真实 question_id 为准：LLM 声称的 id 仅在对上未评分题目时采用，否则按位置回退
- judge 解析失败兜底 move_on（保证流程前进）+ error 事件；追问满 max_followups 时防御性强制 move_on
- initial_difficulty 缺省归一化为 medium（与根图一致）

## 目标

A1 核心流程状态机：开场 → 抽题 → 提问 → 追问（≤max_followups）→ 每 score_every 题评分（正确性/深度/表达 1-5 + 点评）→ 难度自适应 → 报告 → done。全部 LLM 调用经 PromptRegistry 渲染，依赖注入 llm/store/registry/bus。

## 输入/输出契约

- 模块：`app/graphs/workflows/mock_interview.py`
- `build_mock_interview_graph(llm, store, registry, *, bus=None, max_questions=6, max_followups=2, score_every=2)`
- 状态 `MockInterviewState`（total=False）：
  - 会话：`messages`（add_messages）/ `user_id` / `session_id` / `position` / `years` / `interview_round` / `initial_difficulty`
  - 流程：`phase`（opening/answering/done）/ `difficulty`（entry/medium/hard，初始=initial_difficulty 归一化，缺省 medium）
  - 当前题：`current_question_id` / `current_question` / `followup_count` / `pending_main_answer` / `pending_followups`（list[{question, answer}]）
  - 进度：`asked_ids` / `answers`（已完成题：{question_id, question, difficulty, answer, followups}）/ `scores` / `scored_count` / `completed` / `exhausted`
  - 路由：`last_judge_action`（followup/move_on/end）
- 节点与边：

```
START → entry（按 phase 条件路由）
  phase=opening → opening → ask → END（等用户输入）
  phase=answering → judge
  phase=done → finish（固定礼貌回复）→ END
judge → cond_action:
  followup → append_followup（追问消息 + followup_count+1）→ END
  move_on / end → cond_next:
    end → score；completed≥max_questions → score；completed%score_every==0 → score；否则 ask
score → cond_after_score:（completed≥max 或 last_judge_action=end 或 exhausted）→ report；否则 ask
ask → 有题：提问消息 → END；无题：exhausted=True + 提示 → score
report → 生成报告 → phase=done + fire bus EVENT_MOCK_INTERVIEW_END → END
```

- 节点行为：
  - **opening**（LLM）：开场白+规则（共 max_questions 题、每题最多追问 max_followups 次、每 score_every 题评分、可随时说"结束"），2-4 句
  - **ask**：`store.random_pick(position, difficulty, exclude_ids=asked_ids)`；题面原样输出为"第 N 题（难度标签）：题面"
  - **judge**（LLM）：输入当前题/用户回答/追问状态，输出 JSON `{action: followup|move_on|end, followup: str|null, feedback: str|null}`
    - 用户明确要结束/退出 → end（当前题不计入 answers）
    - 回答不会/空泛 → move_on + feedback 给简短要点讲解（≤3 句）
    - 回答有价值且追问未满 → followup（追问沿回答深挖/反例/权衡）
    - 回答充分或追问已满 → move_on（feedback 可一句点评或 null）
    - 记录规则：followup_count==0 时用户文本记为 pending_main_answer；>0 时记为 pending_followups 末条 answer；move_on 时落 answers
  - **score**（LLM）：未评分题批量评分，输出 `{results: [{question_id, correctness, depth, expression, comment, strengths, weaknesses}]}`；分数 1-5 钳制，解析失败默认 3 分并记 error 事件；中间反馈消息；难度自适应：本轮 (c+d+e)/3 均分 ≥4 升档、≤2.5 降档（entry<medium<hard）
  - **report**（LLM）：总体评价/各维度均分/亮点/短板/下一步练习建议；phase=done
- 提示词资产：`mock_interview/{opening,judge,score,report}.v1.yaml`
- 钩子：`app/hooks/bus.py` 增加常量 `EVENT_MOCK_INTERVIEW_END = "mock_interview.end"`，report 后 fire，payload {user_id, session_id, completed, scores}（记忆层订阅点）

## 验收标准

- AC1 开场：开场白 + 第 1 题（难度=初始档），asked_ids 记录，question 文本来自题库
- AC2 追问链路：judge=followup → 追问消息落盘、followup_count+1；第二答 judge=move_on → answers 记录主答+追问对
- AC3 不会：judge=move_on+feedback → feedback 消息落盘、题目计为已完成
- AC4 评分周期：完成第 score_every 题 → 评分反馈消息 + scores 记录；维度分数 1-5 且越界被钳制
- AC5 难度自适应：均分 ≥4 升档（后续抽题难度变化）；≤2.5 降档；档位不越界
- AC6 提前结束：judge=end → 未评分题补评分 → report → phase=done + 触发 mock_interview.end 事件（payload 含 completed）
- AC7 满题数：completed=max_questions → 最终评分 → report → phase=done
- AC8 题库耗尽：ask 无题 → exhausted → score → report → phase=done
- AC9 done 后消息 → 固定礼貌回复，不再触发 judge
- AC10 评分解析失败：非法 JSON → 默认 3 分 + error 事件，流程继续

## 实施计划

| 文件 | 动作 |
|---|---|
| `app/graphs/workflows/mock_interview.py` | 新建 |
| `app/prompts/templates/mock_interview/{opening,judge,score,report}.v1.yaml` | 新建 4 个提示词资产 |
| `app/hooks/bus.py` | 增加 EVENT_MOCK_INTERVIEW_END 常量 |
| `tests/graph/test_mock_interview.py` | 新建：StubLLM 脚本化（opening→judge×n→score×n→report 调用序列）+ sqlite 种子库 + InMemorySaver 多轮（thread_id） |

## Definition of Done

- [x] AC1~AC10 全绿（另附：hard 档不越界、judge 解析失败兜底）
- [x] ruff + mypy 干净
- [x] 4 个提示词渲染均有快照锁定（snapshots 4 个）
