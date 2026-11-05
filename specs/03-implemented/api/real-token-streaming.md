# 真实 token 流式输出（SSE 升级：终稿分块 → LLM token 级）

## 目标

把服务端回复从「整轮图调用完成后按 30 字分块推送」升级为 **LLM token 级实时流式**：
四个业务（chat 经根图路由、interview/resume/review 直连）的用户可见终稿边生成边推送。
SSE 帧格式与 `done` 帧不变，非流式 JSON 分支行为不变；**内部节点内容（评分/提取/分类/工具中间步）永不外流**。

关键设计决策（均已实证，2026-10-22 探针）：

1. **同步 `graph.stream(stream_mode=["custom", "values"])`**，不用 astream——Sync SqliteSaver 的
   astream 直接 NotImplementedError（langgraph 1.2.16 实证）。FastAPI 同步生成器本来跑线程池，无异步需求。
2. **节点侧流式**：只有「终稿生产者」节点调用 `get_stream_writer()` 逐 token 写 custom 事件。
   内部节点不写 → 内部消息结构性不外流，不需要消息过滤层。
3. **chat 经根图**：根图包装节点内 `subgraph.stream` + 外层 `get_stream_writer()` 转发 custom
   事件（嵌套 invoke 不传播 custom 事件——实证）。
4. **ReAct 手写循环替换 `create_react_agent`**（预构建内部模型 `.invoke()` 不流式——读源码实证）：
   stream-first 单次调用（不双调，避免二次成本与展示/落库内容分叉）；空块时 invoke 兜底（桩兼容）。
   顺带消除 create_react_agent 弃用告警（pyproject filterwarnings 一并移除）。
5. **兜底**：若本轮终稿无 token 流（静态产出：面试出题/耗尽/结束语/fallback），
   `chunk_text` 分块推送（原行为保留）。
6. 前端去掉 30ms 假节奏；新增 `error` 帧处理。

## 输入输出契约

- 入：既有 `POST /api/sessions/{sid}/messages`，`MessageIn`（`stream: true`）。
- 出：`text/event-stream`：
  - `data: {"content": "<token>"}` × N（token 块大小不设上限，随 LLM 产出）
  - `data: {"done": true, "session_id": "<sid>"}` 收尾
  - 流式中途异常：`data: {"error": "<message>"}` 一帧后连接结束（**无** done 帧）
- 不变量：`joined(content 帧) == 同输入 stream=false 的 reply`（四业务均成立，含兜底路径）。
- `stream=false` JSON 契约、会话历史、检查点落库行为**完全不变**。

## 验收标准（AC）

1. 四业务 `stream=true` 且终稿为 LLM 产出时，content 帧 ≥ 2（token 级），joined == JSON 分支 reply，done 帧收尾。
2. 内部内容不出现在任何帧：interview 的 judge/score 产物、resume 的 extract/analyze、review 的
   per_question、chat 的工具调用指令与 ToolMessage、意图分类输出。
3. chat 经根图路由（react / clarify / 路由到 mock_interview 等）的终稿 token 流式（custom 事件转发）。
4. 静态终稿（面试出题/题库耗尽/结束语/fallback 回复）走 `chunk_text` 兜底，joined == reply。
5. `stream=false` 分支不变；流式后的会话历史与 JSON 分支一致（检查点照常落）。
6. 流式中途异常 → error 帧 + 连接结束（无 done 帧）。
7. 前端移除 30ms 假节奏；收到 error 帧时 toast 提示。
8. 全量回归通过（既有 194 测试 + 快照，契约测试 test_frontend AC3 不破）。

## 实施计划

1. 本规格落 02-in-progress（README 登记）。
2. 测试桩先行：`ScriptedChatModel._stream` 改为整块流式（含 tool_calls 单块，供流式路径复用）；
   新增 `TokenStreamChatModel`（内容逐字分块、tool_calls 整块，多帧断言用）。
3. 新建 `app/graphs/streaming.py`：`stream_text(llm, messages) -> str`——逐 chunk 写
   `get_stream_writer()`（custom 事件，仅写 str content），返回拼接全文。
4. 节点改造（终稿生产者用 stream_text）：
   - mock_interview.report、resume_analysis.report、interview_review.summary、clarify.clarify。
   - `react_agent.py` 手写循环：`agent`（stream-first：chunk 合并、tool_calls 检测、
     AIMessageChunk→AIMessage 转换、空块 invoke 兜底）→ 条件边 → `ToolNode` → 回 agent；
     状态 schema 仅 `messages`（步数上限仍由根图 R2 注入的 recursion_limit 控制）。
5. `root._make_subgraph_node`：`subgraph.stream(..., stream_mode=["custom", "values"])`，
   custom 事件经外层 writer 转发；最终 state 取最后一个 values 事件（R5/R6 逻辑不变）。
6. `app/api/executor.py` 新增 `stream_business(...)` 同步生成器：按会话取图（与 invoke_business
   同构）→ `stream(["custom", "values"])` → custom str 直接出帧；无 custom 事件时
   `chunk_text(终稿)` 兜底；`done` 帧；异常捕获出 `error` 帧。
7. `app.py` send_message 流式分支换 `stream_business`（仍 sync def + 同步生成器）；
   非流式分支不动。
8. 清理：pyproject 移除 create_react_agent 弃用 filter；前端 `app.js` 移除假节奏、error 帧处理。
9. 测试：
   - 桩：ScriptedChatModel/Tokens 流式行为单测。
   - graph 层：四图 `stream(custom+values)` 事件捕获断言（终稿 token 帧、内部节点零 custom 事件、
     react 循环 tool_call→最终答案流式、静态节点零 custom 事件）。
   - API 层：token 帧 joined == reply（四业务）、chat 含工具轮、静态兜底分块、error 帧、
     `stream=false` JSON 不变、历史一致；既有 test_stream.py 按新契约更新（去掉 ≤CHUNK_SIZE 断言）。
10. ruff + mypy；全量回归；真实 deepseek 冒烟（curl -N 观察逐 token：chat 知识问答、面试报告、
    简历报告）。
11. 流转 03-implemented + 实现备注（含坑 13/14：Sync SqliteSaver 不支持 astream；嵌套 invoke
    不传播 custom 事件）+ README/记忆同步 + 报告。

## Definition of Done

- AC1-8 全绿（既有 194 测试全量回归 + 新增测试 + 快照稳定）。
- ruff / mypy 干净。
- 真实冒烟：curl -N 三个业务均观察到逐 token 帧、done 收尾、历史照常落库。
- 规格流转至 03-implemented，实现备注补齐，README 与记忆同步。

## 实现备注（2026-10-22）

**验收结果**：AC1-8 全绿。全量 **210 测试 + 13 快照**（新增 16：单元桩契约 6、图级流式 8、API 级
流式 7 重写+2 新增）通过；ruff/mypy 干净。

**关键坑（勿重犯）**：

- (13) **Sync SqliteSaver 不支持 astream**（`aget_tuple` 直接 NotImplementedError）——
  流式必须走同步 `graph.stream()`；FastAPI 同步生成器本就跑线程池，无异步需求。
- (14) **嵌套 invoke 不传播 custom 事件**——子图在根图包装节点内被 `.invoke()` 时其 writer
  事件到不了外层；解法是包装节点改用 `subgraph.stream(["custom","values"])` 并用外层
  `get_stream_writer()` 转发。json 分支（invoke）写入被丢弃，行为不变。
- (15) **AIMessageChunk.tool_call_chunks 的 args 是 JSON 字符串**（部分参数累积格式），
  AIMessage.tool_calls 的 args 才是 dict——转换必须 json.dumps/loads。
- (16) **langchain 对含 tool_call_chunks 的流式块会追加一个空收尾块**（内容空、无 tool 信息），
  ReAct 循环按 `chunk + chunk` 累积即可正确合并。
- (17) **create_react_agent 内部模型走 `.invoke()` 不流式**（1.2.16 源码实证）——真实 token 流式
  必须手写循环（本规格已替换，pyproject 弃用告警过滤一并移除）。
- (18) 手写 ReAct 循环 stream-first 单次调用：工具调用轮 content 为空不写 writer（不外流）；
  空块产出时 invoke 兜底（桩模型兼容）；步数上限仍由组合层 recursion_limit 控制（R2 不变）。

**真实冒烟记录**（uvicorn + `curl -N` + 真实 deepseek）：

- chat「什么是数据库索引覆盖」→ 200+ 帧逐 token 渐进到达（首帧 12:49:56.9、done 12:50:09.0，
  真实生成节奏），done 帧 session_id 正确；历史接口读回双方完整消息。
- interview「开始模拟面试」（entry）→ 静态出题无 token 流，chunk_text 兜底 2 帧（30 字）+ done；
  第二轮 judge 追问（静态）同样兜底 5 帧 + done。
- resume「张三 3 年 Java 后端…」→ 报告 **416 个 token 帧** + done；历史接口读回 user + 报告。
- 首页 / static 资源 200。

**实现清单**：`app/graphs/streaming.py`（stream_text）、react_agent 手写循环、
mock_interview.report / resume.report / review.summary / clarify 四节点流式、
root 包装节点转发、`executor.stream_business` + `_business_target` 重构、app.py 分支切换、
pyproject 清理、前端去假节奏 + error 帧。未动：JSON 分支、历史/检查点、会话表、上传端点。
