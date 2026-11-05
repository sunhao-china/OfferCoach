# 计划：D 面试复盘工作流（interview-review）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：[../architecture/execution-boundaries.md](../architecture/execution-boundaries.md)（R1/R3/R4/R5、AC6/AC7）；docs/system-design.md 业务表「面试复盘：还原面试记录 → 逐题点评 → 改进计划」。

实现备注（相对计划的调整，均已测试锁定）：
- load 节点的 config 参数**不能加类型注解**：本模块 `from __future__ import annotations` 会把注解变字符串，langgraph 1.x 识别不出 RunnableConfig 参数、不注入 config（实测 thread_id 丢失、回退 session_id）。沿用捕获桩的 `config=None` 无注解模式（langgraph 按参数名注入）
- 测试夹具必须经根图包装运行 mock_interview：直接顶层 invoke 子图会把检查点写到 ns=""（langgraph 顶层调用行为），与生产派生地址（f"{thread}:mock_interview" + ns "mock_interview"）不一致；load 按生产地址读取
- 难度自适应影响夹具：score_json 全 4 分触发升档（entry→medium），夹具题库需含 medium 题，否则第二题耗尽、面试提前结束
- AC6 集成测试改为同会话真实场景（面试 → 复盘 → 追问）：夹具在同一根线程跑面试，会话历史含面试消息属预期；断言聚焦「react 输入含报告 + 末尾为追问」

与设计文档的偏差（有意为之，已记录）：
- **跨会话复盘后置**：MVP 复盘范围 = 当前会话内的模拟面试记录（从 mock_interview 检查点读取）。历史会话记录表（GET /session/{id}/report）随服务器批次（持久化 + memory 层）统一处理；届时本工作流的 load 节点替换数据源即可
- **长期记忆落盘后置**：同 resume-analysis——完成事件经 HookBus 发出，持久化随服务器批次

## 目标

落地 D 面试复盘：一次性管道（load → per_question → summary），从同会话 mock_interview 检查点还原结构化面试记录（answers + scores），逐题点评并产出综合报告 + 改进计划；报告以 AI 消息落根图会话历史，结构化结果经 `interview_review.complete` 事件发出；对结果的追问不扩展本工作流，自然走 B1（R4）。

## 输入/输出契约

### 模块一：`app/graphs/workflows/interview_review.py`（新增）

- `build_interview_review_graph(llm, registry, *, bus=None, checkpointer) -> CompiledStateGraph`
  - `checkpointer` 必填（无默认）：与根图共享的实例，load 节点借它读 mock_interview 的隔离状态。子图自身**不带** checkpointer 编译（R3 一次性）
  - 提示词：`interview_review/per_question`、`interview_review/summary`
- `InterviewReviewState`（TypedDict，total=False）：
  - 输入通道：`user_id`、`session_id`（经根图通用输入路径透传，root.py 无需改动）
  - 内部通道：`records: list[dict] | None`（合并后的面试记录）、`reviews: list[dict]`（逐题点评）
- 节点：
  1. `load`（无 LLM）：
     - 面试记录地址推导（R6 契约）：自身 invoke config 的 `thread_id` 为根图派生的 `f"{root}:interview_review"`，去掉 `:interview_review` 后缀得 root thread（config 无 thread_id 时回退 `session_id`）
     - `checkpointer.get_tuple({"configurable": {"thread_id": f"{root}:mock_interview", "checkpoint_ns": "mock_interview"}})` → `channel_values` 的 `answers` / `scores` / `completed`
     - 按 `question_id` 合并 answers（含 followups）与 scores（correctness/depth/expression/comment/strengths/weaknesses）→ `records`；无检查点或 answers 为空 → `records=None`
  2. `per_question`：`registry.render("interview_review/per_question", qa_blocks=…)`（逐题 题目/难度/回答/追问/得分）→ JSON `{reviews: [{question_id, comment, strengths, weaknesses}]}`；解析失败 → EVENT_ERROR + 每 record 空点评兜底（流程继续）
  3. `summary`：`registry.render("interview_review/summary", review_blocks=…, completed=…)` → 自由文本报告（逐题点评汇总 + 综合报告 + 改进计划）；`bus.fire(EVENT_INTERVIEW_REVIEW_COMPLETE, payload={user_id, session_id, completed, records, reviews})`；返回 `messages: [AIMessage(报告)]`
- 条件边：records is None → `fallback`（常数文案「当前会话还没有模拟面试记录。先说「开始模拟面试」面几轮，再来复盘。」，不 fire complete），否则 → `per_question`
- 解析兜底同 mock_interview `_parse_json` 风格（容忍 markdown 围栏），失败不中断管道

### 模块二：`app/hooks/bus.py`（修改）

- 新增常量 `EVENT_INTERVIEW_REVIEW_COMPLETE = "interview_review.complete"`

### 模块三：`app/composition.py`（修改）

- `interview_review` 子图从 stub 换成 `build_interview_review_graph(llm_strong, registry, bus=bus, checkpointer=checkpointer)`；**不加 active_predicates**（R5 一次性不锁定）

### 模块四：提示词模板（新增）

- `app/prompts/templates/interview_review/per_question.v1.yaml`：逐题点评，只输出 JSON（点评需比 mock_interview/score 更深：结合追问表现与失分点）
- `app/prompts/templates/interview_review/summary.v1.yaml`：综合报告 + 改进计划（Markdown，面向用户）

## 验收标准

- AC1（还原）预置 mock_interview 检查点（`(t1:mock_interview, "mock_interview")` 写入 answers/scores/completed）→ records 按 question_id 合并正确（含 followups 与各维度分）
- AC2（无记录兜底）空 checkpointer → fallback 文案 + 不 fire complete 事件
- AC3（全流程 + 事件）per_question JSON 解析 → summary 报告落 messages；`interview_review.complete` payload 含 user_id/session_id/completed/records/reviews
- AC4（点评兜底）per_question 解析失败 → EVENT_ERROR + 空点评兜底，管道仍产出报告
- AC5（R1）一次 invoke 内 per_question/summary 各恰好一次 LLM 调用（load 无 LLM；StubLLM 计数断言）
- AC6（R4 追问走 B1，composition 集成）复盘报告落会话后，追问消息被路由到 react_agent，且其 messages 输入含该报告文本（先跑一轮真实 mock_interview 再复盘）
- AC7（提示词快照）per_question、summary 两模板渲染快照（syrupy，同 test_mock_interview 模式）
- AC8（根图输入）interview_review 经通用路径收到 user_id/session_id（messages 通道因报告回传需要而声明，随通用路径透传但 load 不使用；capture 断言，root 无改动）

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 模板 | templates/interview_review/*.v1.yaml ×2 | AC7 快照测试 |
| ② 图实现 | app/graphs/workflows/interview_review.py、bus.py 常量 | AC1-AC5（L2：InMemorySaver 预置检查点 + StubLLM + 事件断言） |
| ③ 组合接线 | app/composition.py | AC6/AC8（composition 集成：真实 mock_interview 一轮 + 复盘 + 追问路由） |
| ④ 收尾 | specs 流转 01-planned → 02-in-progress → 03-implemented | 全量回归 + ruff + mypy |

## Definition of Done

- [x] AC1-AC8 全部有测试且通过；全量 147 例 + 10 快照回归通过
- [x] ruff / mypy（app 全量）干净
- [x] execution-boundaries AC6（事件 + 落会话 + 追问走 B1）与 AC7（节点内无 agent）本规格对应覆盖
- [x] 规格流转至 03-implemented，索引同步更新
