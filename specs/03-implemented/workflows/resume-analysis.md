# 计划：C 简历分析工作流（resume-analysis）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：[../architecture/execution-boundaries.md](../architecture/execution-boundaries.md)（R1/R3/R4/R5、AC6/AC7）；docs/system-design.md 业务表「简历分析/优化：解析 → 抽取 → 对照 JD 打分 → 修改建议」。

实现备注（相对计划的调整，均已测试锁定）：
- LLM JSON 解析兜底抽到共享 app/graphs/json_utils.py（parse_json_text）；mock_interview 同步收编（删本地副本，judge/score 改调共享函数，回归全绿）
- 测试助手提升至 tests/support/graphs.py：build_capture_stub（原 test_root 本地）、judge_json/score_json（原 test_composition 本地），避免测试文件互相 import
- 真实 API 冒烟通过：粘贴简历 → 路由识别 → extract/analyze/report → 修改建议报告（scripts/smoke_chat.py）

与设计文档的偏差（有意为之，已记录）：
- **JD 对照后置**：MVP 无 JD 上传（Web API /resume/upload 属后续 API 层规格）。目标岗位以路由槽位 `slots.position` 传入（缺省按通用社招标准分析），对照具体 JD 打分随 API 层补
- **文件解析（PDF/docx → resume_parser）后置**：MVP 文字聊天，简历以「最后一条用户消息全文」为输入
- **长期记忆落盘后置**：完成事件经 HookBus 发出（供记忆层订阅），持久化随服务器批次（memory 层规格）统一处理

## 目标

落地 C 简历分析：一次性管道（extract → analyze → report），一次 invoke 内产出结构化抽取结果 + 按目标岗位的分析 + 修改建议报告；报告以 AI 消息落根图会话历史，结构化结果经 `resume_analysis.complete` 事件发出；对结果的追问不扩展本工作流，自然走 B1（R4）。

## 输入/输出契约

### 模块一：`app/graphs/workflows/resume_analysis.py`（新增）

- `build_resume_analysis_graph(llm, registry, *, bus=None) -> CompiledStateGraph`
  - 无 checkpointer（R3 一次性管道，无多轮状态；R5 不占 active_graph——组合层不给它配 active_predicates）
  - 提示词：`resume/extract`、`resume/analyze`、`resume/report`
- `ResumeAnalysisState`（TypedDict，total=False）：
  - 输入通道：`messages`（add_messages）、`user_id`、`session_id`、`position: str | None`
  - 内部通道：`extracted: dict | None`（抽取结果）、`analysis: dict | None`（分析结果）
- 节点（每节点单次 LLM 调用，R1）：
  1. `extract`：取最后一条用户消息为简历全文，`registry.render("resume/extract", resume=text)` → JSON `{skills: [], years: str|None, education: str|None, experience: [], projects: []}`；解析失败 → `bus.fire(EVENT_ERROR)` + `extracted=None`（流程走兜底分支）
  2. `analyze`：`registry.render("resume/analyze", extracted=json, position=position或"通用")` → JSON `{overall: 1-5, strengths: [], weaknesses: [], position_match: str}`；解析失败 → EVENT_ERROR + 空 dict 兜底（流程继续）
  3. `report`：`registry.render("resume/report", analysis=json, position=…)` → 自由文本报告（总评 + 亮点 + 短板 + 修改建议）；`bus.fire(EVENT_RESUME_ANALYSIS_COMPLETE, payload={user_id, session_id, position, extracted, analysis})`；返回 `messages: [AIMessage(报告)]`
- 条件边：extracted is None → `fallback`（常数文案「没能从消息中解析出简历内容。请直接粘贴简历全文，我再帮你分析。」，不 fire complete），否则 → `analyze`
- 解析兜底复用 mock_interview 的 `_parse_json` 风格（容忍 markdown 围栏），失败不中断管道（judge/score 同款策略）

### 模块二：`app/hooks/bus.py`（修改）

- 新增常量 `EVENT_RESUME_ANALYSIS_COMPLETE = "resume_analysis.complete"`

### 模块三：`app/graphs/root.py`（修改，特化输入）

- 新增 `_resume_analysis_input(state, declared_keys)`（与 `_mock_interview_input` 同款模式）：
  - `messages`：仅最后一条用户消息（简历全文）
  - `user_id` / `session_id`：根状态透传
  - `position`：`route_decision.slots.position`
- `_subgraph_input` 按子图名分发到该特化函数

### 模块四：`app/composition.py`（修改）

- `resume_analysis` 子图从 stub 换成 `build_resume_analysis_graph(llm_strong, registry, bus=bus)`；**不加 active_predicates**（R5 一次性不锁定）

### 模块五：提示词模板（新增）

- `app/prompts/templates/resume/extract.v1.yaml`：抽取结构化字段，只输出 JSON
- `app/prompts/templates/resume/analyze.v1.yaml`：对照目标岗位（或通用社招标准）打分 + 亮点/短板
- `app/prompts/templates/resume/report.v1.yaml`：修改建议报告（Markdown，面向用户）

## 验收标准

- AC1（抽取）extract 解析成功 → `extracted` 落 state；analyze 渲染变量含 extracted JSON 与 position
- AC2（抽取兜底）extract 解析失败 → EVENT_ERROR + fallback 文案；不 fire complete 事件
- AC3（全流程 + 事件）报告文本落 messages；`resume_analysis.complete` payload 含 user_id/session_id/position/extracted/analysis
- AC4（分析兜底）analyze 解析失败 → EVENT_ERROR + 报告仍产出（通用建议）
- AC5（R1）一次 invoke 内 extract/analyze/report 各恰好一次 LLM 调用（StubLLM 计数断言）
- AC6（根图输入）resume_analysis 收到的输入：仅最后一条用户消息 + user_id + session_id + position 槽位（root 层 capture 断言）
- AC7（R4 追问走 B1，composition 集成）分析报告落会话后，追问消息被路由到 react_agent，且其 messages 输入含该报告文本（StubDispatcher 脚本 + ScriptedChatModel）
- AC8（提示词快照）resume/extract、resume/analyze、resume/report 三模板渲染快照（syrupy，同 test_mock_interview 模式）

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 模板 | templates/resume/*.v1.yaml ×3 | AC8 快照测试（tests/graph/test_resume_analysis.py 内） |
| ② 图实现 | app/graphs/workflows/resume_analysis.py、bus.py 常量 | AC1-AC5（L2：StubLLM + 事件断言） |
| ③ 根图特化输入 | app/graphs/root.py | AC6（test_root.py 增补） |
| ④ 组合接线 | app/composition.py | AC7（composition 集成：真实图替换 stub + 追问路由） |
| ⑤ 收尾 | specs 流转 01-planned → 02-in-progress → 03-implemented | 全量回归 + ruff + mypy |

## Definition of Done

- [x] AC1-AC8 全部有测试且通过；全量 147 例 + 10 快照回归通过
- [x] ruff / mypy（app 全量）干净
- [x] execution-boundaries AC6（事件 + 落会话 + 追问走 B1）与 AC7（节点内无 agent）本规格对应覆盖
- [x] 规格流转至 03-implemented，索引同步更新
