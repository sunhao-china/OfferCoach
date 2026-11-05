# 计划：组合层（composition）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：[../architecture/execution-boundaries.md](../architecture/execution-boundaries.md)（R5/R6 与 AC2-AC4）；docs/system-design.md §3.1 意图路由、§5.1 短期记忆（Checkpoint）、§11 会话生命周期。

实现备注（相对计划的调整，均已测试锁定）：
- 子图 config 在派生 thread_id 之外，还**把 checkpoint_ns 固定为子图名**：根图 Pregel 传给节点任务的 config 带随机 task_id 命名空间（如 mock_interview:\<uuid\>），透传会导致子图每轮换命名空间、多轮状态丢失（组合层集成测试实证后修正）
- route 节点 route_decision 改用 `model_dump(mode="json")`：枚举转字符串，checkpointer（jsonplus）序列化可往返，消除未注册类型告警
- StubLLM 增加 no-op `bind_tools`：组合层总是构建 ReAct 子图（create_react_agent 要求模型可被 coerce 为 Runnable）；测试以 ScriptedChatModel 充当 llm_strong，未用 react 的用例传空序列
- 根图结果只含根通道：子图内部进度（completed/phase）在测试中经 `checkpointer.get_tuple((thread:name, name))` 断言
- 本地冒烟已执行：sqlite 200 题导入 + QuestionStore 检索/抽题 + 真实 deepseek 两轮对话（scripts/smoke_chat.py）

与设计文档的偏差（有意为之，已记录）：
- §3.1「中途换轨」（面试中说"帮我改简历"→ 暂停+恢复）**后置**：MVP 采用 execution-boundaries R5 纯锁定（需结束当前面试才能换轨），换轨需状态监听器 + 恢复询问流，属后续里程碑
- §3.2 模型路由表中「闲聊用低成本快模型」**后置**：MVP 闲聊与 B1 共用强档 react_agent（规则快路径已省分类成本，回答仅一次强档调用），档位差异化随成本优化里程碑
- §11 Web 会话 API（POST /session、SSE 流式、warmup）不在本规格范围，属后续 API 层规格；本规格只提供本地聊天入口 `invoke_chat`

## 目标

把根图与五个子图用真实组件接线：deepseek 分档 LLM（生产）/ 桩 LLM（测试）、PromptRegistry、QuestionStore、RouteDispatcher、HookBus、共享 checkpointer；落地 R5 会话连续性（active_graph 锁定 + active_predicates 释放）与 R6 状态隔离（派生 thread_id）；提供本地 sqlite 开发库初始化说明与聊天入口函数，产出本地端到端可跑通的对话骨架。

## 输入/输出契约

### 模块一：`app/composition.py`（新增）

- `build_app_graph(*, store, registry, llm_classify, llm_strong, bus=None, checkpointer=None, dispatcher=None, max_questions=6, max_followups=2, score_every=2) -> CompiledStateGraph`
  - dispatcher 缺省构造 `RouteDispatcher(llm_classify, registry, bus=bus)`；测试注入 StubDispatcher
  - 子图字典：react_agent（llm_strong + registry.render("react/b1")）、mock_interview（llm_strong + store + registry + bus + checkpointer）、clarify（llm_classify + registry）、resume_analysis / interview_review（stub 图）
  - active_predicates 仅 mock_interview：`lambda result: result.get("phase") != "done"`
  - invoke_options 仅 react_agent：`{"configurable": {"recursion_limit": 8}}`（R2 步数上限）
  - 返回 build_root_graph 编译产物（共享 checkpointer 实例）
- `invoke_chat(graph, user_id, session_id, text, *, config=None) -> str`
  - 输入 `{user_id, session_id, messages: [HumanMessage(text)]}`，config 缺省 `{"configurable": {"thread_id": session_id}}`
  - 返回最后一条 AIMessage 的 content

### 模块二：`app/graphs/root.py`（修改）

- RootState 增加 `active_graph: str | None`
- `build_root_graph(dispatcher, subgraphs, *, checkpointer=None, active_predicates=None, invoke_options=None)`
  - active_predicates：`dict[str, Callable[[dict], bool]]`（子图单轮结果 → 是否仍有活跃会话）；键必须 ⊆ TARGET_GRAPHS，否则 ValueError
  - invoke_options：`dict[str, dict]`（子图名 → 注入 config 的片段，如 react 的 recursion_limit）
- START 条件边改为：`_pick_entry(state)` → `state.get("active_graph")` 若在 TARGET_GRAPHS 中，否则 `"route"`；path_map 覆盖 5 目标 + route
- `_make_subgraph_node(name, subgraph, *, predicate=None, options=None)`：
  1. 派生子图 config：`thread_id = f"{原thread}:{name}"`（原 config 无 thread_id 则不派生），合并 options
  2. `subgraph.invoke(sub_input, 派生config)`
  3. 有 predicate 时回写 `active_graph`：predicate(result) True → name，False → None；无 predicate 不回写
  4. 返回 `{"messages": result.get("messages", [])}`（add_messages 按 id 去重防重复）

### 模块三：本地 sqlite 开发库（说明，无代码改动）

`DATABASE_URL=sqlite:///data/offercoach_dev.db python scripts/import_questions.py`（先 `--dry-run` 校验 200 题，再正式导入；幂等，重复导入自动跳过）。随后 `QuestionStore` 可直接指向该 sqlite 文件（engine 注入，与生产 MySQL 同构）。

### 模块四：`scripts/smoke_chat.py`（新增，开发冒烟工具）

真实组件（DEEPSEEK_API_KEY + sqlite 库）构建 build_app_graph，标准输入循环对话，打印回复与当前 active_graph。不进 pytest，手动运行。

## 验收标准

- AC1（R6 派生）wrapper 传给子图的 config `thread_id == f"{原}:{name}"`（capture stub 捕获 config 断言）
- AC2（R5 锁定）面试进行中（phase=answering）的消息不触发 dispatcher.route（StubDispatcher.calls 不增长）、直通 mock_interview 且 judge 正常推进（completed+1）；phase=done 后下一轮恢复路由（calls 增长）
- AC3（R5 释放）mock_interview 一轮走完 done 后 active_graph 为 None
- AC4（R6 隔离）面试前会话有闲聊消息时，mock_interview 自身状态（subgraph.get_state(派生 config)）不含闲聊消息；根图 active_graph 跨轮持久（不被子图检查点写入抹掉）
- AC5（无重复）连续三轮面试后根图 messages 每条内容只出现一次
- AC6（R2 上限）react_agent 子图收到的 config 含 `recursion_limit == 8`
- AC7（集成）build_app_graph 产物端到端：闲聊→react_agent（scripted 工具调用真查 sqlite 题库并作答）；模拟面试→A1 全程（StubLLM 脚本走 opening→ask→judge→score→report）；澄清→clarify；未知目标→clarify 降级（StubDispatcher 场景）
- AC8（校验）active_predicates 键不在 TARGET_GRAPHS → ValueError；invoke_options 同理
- AC9（入口）invoke_chat 返回最后 AI 文本；同一 session 跨轮累积消息

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① root R5/R6 改造（active_graph、派生 config、predicates、options） | app/graphs/root.py | tests/graph/test_root.py 增补：AC1/AC2(stub 层)/AC6/AC8；既有 16 例回归 |
| ② 组合构建 + 聊天入口 | app/composition.py | tests/graph/test_composition.py（新）：AC2(真实 mock_interview)/AC3/AC4/AC5/AC7/AC9 |
| ③ 冒烟工具 | scripts/smoke_chat.py | 手动（不进 pytest） |
| ④ 本地 sqlite 冒烟 | scripts/import_questions.py（已有） | 手动：200 题入库 + QuestionStore.search 抽查 |
| ⑤ 收尾 | specs 流转、全量回归 | pytest 全绿 + 5 快照 + ruff + mypy |

## Definition of Done

- [x] AC1-AC9 全部有测试且通过；全量 132 例 + 5 快照回归通过
- [x] ruff / mypy（app 全量）干净
- [x] 本地 sqlite 库导入冒烟通过（200 题），smoke_chat 真实 deepseek 两轮对话跑通
- [x] 规格流转至 02-in-progress → 03-implemented，01-planned 索引同步更新
- [x] execution-boundaries.md 的 AC2/AC3/AC4/AC5 由本规格测试覆盖（其 DoD 随 C/D 完成后整体勾选）
