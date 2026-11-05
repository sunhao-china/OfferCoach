# Langfuse 监控接入（推迟清单第 ④ 项）

## 目标

用 Langfuse 云（us.cloud）监控 OfferCoach 的 LLM 调用：每个 HTTP 业务请求一条 trace，嵌套所有 LLM 调用（generation，含输入/输出/token/耗时），带 session_id / user_id / business 标签可过滤。用户已提供密钥（LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY / LANGFUSE_BASE_URL=https://us.cloud.langfuse.com）。**密钥不进仓库任何文件**：本地走 Windows 用户环境变量，服务器走 /data/offercoach.env（仓库外、600）。

## 输入输出契约

输入：
- 三个 LANGFUSE_* 环境变量（用户提供）
- 现有 LLM 调用点清单（已侦察）：intent.py classify、react_agent agent 节点、stream_text 助手（clarify + 三个 report/summary 终稿节点）、mock_interview ask/judge/score、resume extract/analyze、review per_question——全部不带 config，需补透传
- Langfuse v3+ 集成方式（已确认）：`from langfuse.langchain import CallbackHandler`，无参构造，经 LangChain config 的 `callbacks` 传入、`metadata` 用 `langfuse_session_id` / `langfuse_user_id` / `langfuse_tags` 键；langfuse 5.0.4 本地实测 import 无需 langchain 伞包

输出：
- app/core/tracing.py：有密钥才构建 handler（惰性 import langfuse，无密钥环境零依赖加载）
- executor.py 在每次业务执行注入 handler + 会话元数据；所有 LLM 调用点透传 config
- pyproject 加 langfuse 依赖；服务器 requirements 锁定 langfuse==5.0.4 + /data/offercoach.env 加三变量 + 重启
- 本地与服务器真实冒烟后，Langfuse 云端可见 trace

## 验收标准（AC）

1. tracing 模块：无 LANGFUSE_SECRET_KEY/PUBLIC_KEY → build_trace_handler() 返回 None、tracing_enabled() False；有密钥 → 返回 handler。单测覆盖（构造不触发网络）
2. config 透传：RecordingHandler（继承 BaseCallbackHandler）替换 build_trace_handler 后，chat 路径（根图→route→react_agent）与直连 interview 路径（mock_interview ask）均收到 on_llm_start；metadata 含 langfuse_session_id/user_id/tags
3. 无密钥零影响：测试环境不设 LANGFUSE_*，全量 289 测试 + 17 快照不变通过（行为不变量）
4. 本地真实冒烟（设密钥）：B1 问答 + 面试一轮，Langfuse 公共 API（us.cloud）能查到 trace 且含 session/user 元数据
5. 服务器部署：requirements 加 langfuse==5.0.4 并安装；/data/offercoach.env 加三变量（仓库外）；重启后服务器真实冒烟，云端可见来自服务器的 trace
6. 红线合规：密钥未写入仓库任何文件（本地用户环境变量 + 服务器仓库外 env 文件）；fin 资源零触碰

## 实施计划

1. 写规格 → specs/02-in-progress（本文件流转）
2. app/core/tracing.py（tracing_enabled + build_trace_handler 惰性 import）；pyproject 加 langfuse>=3.8
3. executor.py：_attach_tracing(config, session) 挂 callbacks + metadata，chat/直连两分支接入
4. config 透传（新加参数一律无注解 `config=None`，遵循 interview_review 既有模式）：intent.classify → dispatcher.route → root route 节点；stream_text；clarify；react_agent agent；mock_interview ask/judge/score/report；resume extract/analyze/report；review per_question/summary
5. tests/unit/test_tracing.py + tests/api/test_tracing.py（RecordingHandler 双路径）+ ruff/mypy + 全量回归
6. setx 本地三变量 → 本地真实冒烟（临时脚本用后即删）→ Langfuse 公共 API 验证 trace
7. 服务器：requirements.txt 加 langfuse==5.0.4、env 文件加三变量、pip 重装、systemctl 重启、服务器冒烟 → 云端验证
8. 规格流转 03 + README + 记忆同步 + 中文报告

## Definition of Done

AC1-6 全部通过；冒烟与云端 trace 证据写入实现备注；specs/03-implemented/README.md 与记忆文件同步；密钥零落盘（仓库内）。

## 实现备注

**代码**：app/core/tracing.py 新增 tracing_enabled/build_trace_handler（双密钥才启用、惰性 import，无密钥环境不加载 langfuse）；app/api/executor.py 新增 `_attach_tracing(config, session)`，把 CallbackHandler 挂到 `config["callbacks"]`、会话元数据写入 `config["metadata"]`（langfuse_session_id / langfuse_user_id / langfuse_tags=[business]），chat（根图）与直连两分支统一接入。config 透传沿调用链补齐：intent.classify → dispatcher.route → root.route 节点 → `_derive_subgraph_config`（`{**config, ...}` spread 保留 callbacks/metadata）→ 各子图节点。关键坑：新增节点参数一律无注解 `config=None`——模块有 `from __future__ import annotations` 时注解会变成字符串，langgraph 1.x 识别不出 RunnableConfig 参数、不注入 config（interview_review 既有模式）。mock_interview/resume_analysis/interview_review 三个工作流的全部 LLM 节点、stream_text、clarify、react_agent 均补 `config=None` 并透传。测试：tests/unit/test_tracing.py 3 个（无密钥禁用/双密钥启用/需双密钥）；tests/api/test_tracing_api.py 3 个（RecordingHandler 计数证明 chat 根图与直连 interview 的 LLM 调用都收到回调、metadata 正确、无 handler 时 config 原样返回）。测试桩 StubDispatcher.route 补 `config=None`。pyproject 加 `langfuse>=3.8`。

**云端 API 迁移**：Langfuse 4.67 的 `GET /api/public/traces` 返回 410 Gone（2026-11-16 移除），改查 `GET /api/public/v2/observations?sessionId=<id>&limit=50`（basic auth = public:secret）。观测字段 type ∈ {CHAIN, GENERATION}——LangGraph 节点自动记为 CHAIN、LLM 调用记为 GENERATION；sessionId/userId 元数据落在观测上，tags 未暴露，验证改用 sessionId/userId/type。

**测试证据**：本地无密钥全量回归 295 通过 + 17 快照通过（AC3）；本地真实冒烟（B1 问答 780 字 + 面试一轮）：云端两个 sessionId 分别查到 11 观测（CHAIN 7 / GENERATION 2）与 7 观测（CHAIN 6 / GENERATION 1），userId 均为冒烟用户（AC4）；ruff/mypy 通过。

**服务器部署（AC5）**：scripts/deploy/requirements.txt 加 `langfuse==5.0.4`；deploy.py 的 env 文件写入追加 LANGFUSE_SECRET_KEY/PUBLIC_KEY/BASE_URL，凭据经 OC_LANGFUSE_SECRET/OC_LANGFUSE_PUBLIC 环境变量传入（仓库零密钥，AC6）。全量重跑 deploy.py 8 步：题库导入幂等（新增 0/更新 0/跳过 497，无翻倍）、服务重启 active、healthz 200。公网冒烟（chat 842 字 + interview 27 字）：云端两个 sessionId 分别查到 16 观测（GENERATION 3）与 7 观测（GENERATION 1），userId 均为服务器冒烟用户——服务器 trace 已进云端。

**遗留**：Langfuse 免费额度与 token 计费未评估（监控本身不改变业务行为，开销仅回调上传）；标签（business）在 v2 observations API 未暴露，云端 UI 可按 sessionId/userId 过滤。
