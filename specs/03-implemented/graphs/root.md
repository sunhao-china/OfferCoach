# 计划：根图（root）与轻量子图（clarify / stubs）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：docs/system-design.md §2/§4（Router 节点 → 子图）。

实现备注：
- Python 3.11 需 `typing_extensions.TypedDict`（pydantic 限制）
- langgraph 1.x 带 checkpointer 的图 invoke 必须传 `configurable.thread_id`（测试统一带 CONFIG）
- langchain-core 1.x 消息构造时生成随机 id，测试断言用 `.content` 比较而非整条消息相等

## 目标

根图按 `RouteDecision.target_graph` 分发到子图；M2 业务（resume_analysis / interview_review）先用占位子图，保证五个 target 全接线。

## 输入/输出契约

- 模块：`app/graphs/root.py`、`app/graphs/clarify.py`、`app/graphs/stubs.py`
- 状态 `RootState`：`user_id` / `session_id` / `messages`（add_messages）/ `route_decision`（dict）
- `build_root_graph(dispatcher, subgraphs: dict[str, CompiledGraph], *, checkpointer=None)`
  - route 节点：取最后一条 HumanMessage 文本 → `dispatcher.route(text)` → route_decision 落入状态（model_dump）
  - 条件边按 target_graph 分发到五个子图节点
  - 子图节点用函数包装：只把需要的 state 键传给子图，子图返回的 messages 合并回根状态；config 透传（共享 checkpointer）
  - mock_interview 子图输入键：messages（仅最后一条用户消息）/ user_id / session_id / position / years / interview_round / initial_difficulty
  - 槽位难度归一化：入门/简单/初级→entry，中等→medium，困难/高级→hard，缺省 medium
- `build_clarify_graph(llm, registry)`：单节点 LLM 澄清追问，提示词 `clarify/clarify.v1.yaml`（最多一问，1-2 句）
- `build_stub_graph(text)`：单节点追加固定 AI 消息（M2 占位）

## 验收标准

- AC1 五个 target（react_agent / mock_interview / clarify / resume_analysis / interview_review）各分发到对应桩子图
- AC2 桩子图返回的消息合并进根状态 messages
- AC3 route_decision 落入状态（含 intent / source / target_graph）
- AC4 子图输入只包含声明的键（桩子图断言收到的输入键集合）
- AC5 clarify 子图：桩 LLM 响应成为 AI 消息
- AC6 stub 子图：固定文案成为 AI 消息

## 实施计划

| 文件 | 动作 |
|---|---|
| `app/graphs/root.py` / `clarify.py` / `stubs.py` | 新建 |
| `app/prompts/templates/clarify/clarify.v1.yaml` | 新建提示词资产 |
| `tests/graph/test_root.py` | 新建：桩 dispatcher + 桩子图，checkpointer=InMemorySaver |
| `tests/graph/test_clarify.py` | 新建：桩 LLM |

## Definition of Done

- [x] AC1~AC6 全绿（另附：多轮 checkpointer 累积、未知目标降级 clarify、难度归一化参数化）
- [x] ruff + mypy 干净
