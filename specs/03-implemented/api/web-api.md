# 计划：Web API 层（FastAPI，按业务类型建会话）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：用户决策（2026-10-21）——页面/业务入口分离优先，避免单入口路由揉合复杂度；docs/system-design.md（Web 文字聊天 MVP）；M1 图执行层已全部实现（153 测试 + 11 快照全绿）。

## 目标

把 M1 图执行层暴露为 Web 服务：FastAPI 应用，**按业务类型创建会话**（页面分离的 API 形态——每个前端页面钉住一种业务，会话类型即业务）。interview / resume / review 会话的消息**直连对应子图、不经过路由**，路由只服务 chat 页（B1 问答/闲聊/澄清，仍可顺带路由到其他业务）。会话状态经 sqlite checkpointer 持久化（开发），生产换 MySQL（随服务器批次）。MVP 同步返回完整回复，SSE 逐 token 流式后置。

## 输入/输出契约

### 模块一：依赖声明（pyproject.toml）

- 补齐运行时依赖（现为裸装）：`langgraph`、`langchain-core`、`langchain-deepseek`、`langgraph-checkpoint-sqlite`、`fastapi`、`uvicorn`
- 已声明的 pydantic / sqlalchemy / pymysql 保留

### 模块二：`app/api/`（新增包）

- `app/api/schemas.py`：`BusinessType = Literal["chat", "interview", "resume", "review"]`；`SessionCreate {business, user_id, target_session_id?}`（review 必填 target，其余忽略）；`SessionOut {session_id, business, user_id, target_session_id?, created_at}`；`MessageIn {content}`（页面业务参数如 position/difficulty 由前端在创建会话时不给——MVP 走默认档，mock_interview 有 DEFAULT_* 兜底）；`MessageOut {reply}`；`HistoryOut {session_id, messages: [{role, content}]}`
- `app/api/session_store.py`：SQLAlchemy sqlite 表 `sessions(id, user_id, business, target_session_id, created_at)`（engine 注入，测试用 tmp 路径；生产换 MySQL 同构）——仅存会话元数据，消息本体在 checkpointer
- `app/api/executor.py`：按 business 的调用规则（见模块三）+ 历史读取规则（见模块四）
- `app/api/deps.py`：构建运行时依赖（Settings / QuestionStore / PromptRegistry / llm 分档 / HookBus / SqliteSaver / build_app_graph），测试可整体替换
- `app/api/app.py`：`create_app(deps)` 工厂 + 端点（见下）

端点（无鉴权，MVP）：

- `POST /api/sessions` → SessionOut（session_id = uuid4；review 会话校验 target_session_id 存在）
- `POST /api/sessions/{sid}/messages` → MessageOut（同步返回完整回复文本）
- `GET /api/sessions/{sid}/messages` → HistoryOut（从 checkpointer 读该会话消息）
- `GET /api/sessions/{sid}` → SessionOut
- `GET /healthz` → `{"status": "ok"}`

### 模块三：会话执行规则（页面分离核心）

| business | 执行体 | config thread_id / ns |
|---|---|---|
| chat | 根图（路由照常，含 R5 active_graph 面试锁定） | `sid` / 缺省 |
| interview | mock_interview 子图直连（槽位走默认档，页面后置可加参数） | `{sid}:mock_interview` / `mock_interview` |
| resume | resume_analysis 子图直连（一次性管道） | `{sid}:resume_analysis` / `resume_analysis` |
| review | interview_review 子图直连，payload session_id = target_session_id（复盘对象） | `{target}:interview_review` / `interview_review` |

直连 config 与根图 R6 派生规则一致，保证「面试页跑完 → 复盘页可读」的地址可互算。**路由（dispatcher/分类器）在直连会话中完全不被调用**。

### 模块四：历史读取规则

与模块三同表：chat 读 `(sid, "")`；interview 读 `({sid}:mock_interview, mock_interview)`；resume 读 `({sid}:resume_analysis, resume_analysis)`；review 读 `({target}:interview_review, interview_review)`。checkpointer.get_tuple → state.messages → role/content 列表。

前置改动：`build_resume_analysis_graph` 增加可选 `checkpointer` 参数（与 mock_interview/interview_review 同模式），`composition.py` 接线传入——否则 resume 会话无检查点可读。

### 模块五：前端页面（本规格不实现，仅锁契约）

四页：模拟面试 / 简历分析 / 面试复盘 / 问答，各对应一种 BusinessType 会话；复盘页提供「选择要复盘的会话」控件（target_session_id）。前端规格另立。

## 验收标准

- AC1（会话创建）四种 business 均可 POST /api/sessions 创建；review 缺 target_session_id 返回 422
- AC2（直连不经路由）interview / resume 会话消息返回对应子图结果，且 llm_classify 桩零调用
- AC3（chat 走路由）chat 会话：StubLLM 分类 → scripted ReAct tool_call → 真查题库 → 作答
- AC4（历史）interview 会话两轮后 GET /messages 读回双方消息；review 会话读回报告
- AC5（会话隔离）两个 chat 会话消息互不串（sqlite checkpointer 按 thread_id 隔离）
- AC6（跨页复盘）interview 会话跑到 done 后，review 会话（target=该会话）产出复盘报告
- AC7（回归）全量 153 + 11 快照不受影响；ruff / mypy（app 全量含 api/）干净
- AC8（真实冒烟）uvicorn 起服务，/healthz 200，真实 deepseek 走通 interview 一轮

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 依赖 | pyproject.toml + pip 安装 | — |
| ② resume 检查点前置 | app/graphs/workflows/resume_analysis.py、app/composition.py | 既有 resume 测试回归 |
| ③ API 包 | app/api/schemas.py / session_store.py / executor.py / deps.py / app.py + scripts/run_server.py | AC1/AC2/AC3（tests/api/test_web_api.py，TestClient + 桩 LLM + tmp sqlite） |
| ④ 历史与隔离 | executor 读规则 | AC4/AC5 |
| ⑤ 跨页复盘 | review 执行规则 | AC6 |
| ⑥ 冒烟 | uvicorn + curl /healthz + 真实对话 | AC8 |
| ⑦ 收尾 | 规格流转 03-implemented、索引同步 | AC7 |

## Definition of Done

- [x] AC1-AC6 全部有测试且通过；全量回归（161 + 11 快照）全绿
- [x] ruff / mypy（app 全量含 api/）干净
- [x] AC8 uvicorn 真实冒烟通过（记录结果）
- [x] pyproject 依赖完整可复现安装
- [x] 本规格流转 03-implemented，01/02/03 README 同步

## 实现备注

- **顶层直连 invoke 的检查点落 ns=""（2026-10-21 实证，InMemorySaver 与 SqliteSaver 一致）**：与根图嵌套调用（落 ns=子图名）形成两条写入路径。读取侧统一走 `app/graphs/checkpoints.py` 的 `read_checkpoint` 双 ns 兜底（interview_review load 与 API 历史接口），不依赖 saver 的 ns 回退行为差异
- **SqliteSaver 连接必须 `check_same_thread=False`**（langgraph 线程池跨线程写检查点；官方实现内部有锁）
- **interview_review 编译改为带 checkpointer**：原先靠根图包装写入状态，API 直连后需要自己的检查点承载会话消息（历史接口读 `f"{target}:interview_review"`）
- 测试约定：llm_strong 桩必须是 Runnable（create_react_agent 构建时校验），统一用 ScriptedChatModel；llm_classify 用 StubLLM 断言零调用（直连不经路由的 AC2 证据）
- 直连面试默认难度 medium（页面参数后置）：测试题库仅 1 道中等题时，面试在第 2 题耗尽后结束——测试脚本消费条数以实证为准（ask 耗尽后 judge 不再调用）
- AC8 冒烟结果（2026-10-21）：uvicorn 起服务，/healthz 200；真实 deepseek 面试两轮（出题 → 判分追问），历史接口读回 5 条双方消息，通过
