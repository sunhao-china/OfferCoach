# 计划：执行模式分层与相邻边界（execution-boundaries）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：docs/system-design.md §4 执行模式；2026-10-21 代码审查结论（根图逐消息重路由会截断进行中的面试）；用户决策（2026-10-21）：B1 保持 ReAct；C/D 分析结果支持追问、追问自然走 B1。

## 目标

钉死 OfferCoach 三种执行模式的分层与相邻边界，作为组合层（根图接线）与 C/D 工作流实现的唯一依据：

1. **业务映射锁定**：哪些业务走 Workflow、哪些走 ReAct、哪些是单节点
2. **相邻边界规则**：Workflow 与 ReAct 如何衔接——尤其 C/D 分析完成后的追问流
3. **会话连续性**：有状态子图会话进行中，根图不得重新路由截走消息

## 输入/输出契约

本规格为架构决策规格，不产生代码模块；产出为后续规格（graphs/composition、workflows/resume-analysis、workflows/interview-review）必须遵守的契约。

### 业务映射（决策锁定）

| 业务 | 模式 | 执行体 |
|---|---|---|
| A1 模拟面试 | Workflow | `app/graphs/workflows/mock_interview.py`（已实现） |
| B1 知识问答 | ReAct | `app/graphs/react_agent.py`（已实现，强档模型） |
| C 简历分析 | Workflow | `app/graphs/workflows/resume_analysis.py`（已实现：解析 → 字段抽取 → 按岗位分析 → 报告，一次性管道） |
| D 面试复盘 | Workflow | `app/graphs/workflows/interview_review.py`（已实现：读取面试记录 → 逐题点评 → 综合报告，一次性管道） |
| 澄清 | 单节点 LLM | `app/graphs/clarify.py`（已实现） |
| 闲聊 | ReAct | 与 B1 同执行体（ROUTE_TABLE 已映射 react_agent），模型用 cheap tier |

### 边界规则

- **R1**：Workflow 节点内只做「单次 LLM 调用 + 结构化解析」，不嵌套 agent；agent 只出现在 ReAct 层
- **R2**：ReAct 执行体限步数（MVP ≤8 步，经 `configurable.recursion_limit` 注入）+ 系统提示词强制「最终必须以答案收尾，不得只调工具不回答」
- **R3**：C/D 为一次性管道：一次 invoke 内走完并产出结构化分析结果，不扩展多轮状态机
- **R4（C/D 追问边界）**：分析结果以 AI 消息落根图会话历史 + 结构化完成事件（如 `resume_analysis.complete`，供记忆层订阅）落盘；用户对结果的追问由根图正常路由，自然走 B1——B1 的 messages 输入含完整会话历史（含分析报告），提示词约定「会话中已有分析结论时结合其回答追问」。C/D 不为追问扩展多轮。追问发生在同会话内由消息历史支撑；跨会话持久化由记忆层（服务器恢复后）承担
- **R5（会话连续性）**：RootState 增加 `active_graph: str | None` 通道。有状态子图会话未结束时，根图 START 条件边绕过 route 直通该子图；会话结束释放 active_graph（置 None）。无状态子图（react_agent/clarify/单次管道）不占用 active_graph。面试中用户说「结束/重新开始」等由 judge 语义消化（end → done → 释放），MVP 不另设根图级打断指令
- **R6（状态隔离，2026-10-21 REPL 验证）**：根图 wrapper 调用带 checkpointer 的子图时，必须派生独立 thread_id（如 `f"{thread_id}:{name}"`）传入子图 config。理由（已实证）：
  - 同 thread 共享 checkpointer 时子图会继承根图 messages 历史（通道互串），且子图写检查点会抹掉根图独有通道（active_graph 将丢失）
  - `checkpoint_ns` 读隔离不可靠：InMemorySaver 在 ns 未命中时回退到同 thread 最新检查点（换 ns 读到前 ns 数据）
  - 消息重复问题由 `add_messages` 按 message id 去重解决：子图结果消息含根图已有消息（同 id）时自动丢弃，仅新消息落根图

### 会话连续性机制契约

- `build_root_graph` 增加注入参数 `active_predicates: dict[str, Callable[[dict], bool]]`（子图单轮结果 dict → 是否仍有活跃会话），避免 root 反向依赖子图内部通道名造成循环 import
- MVP 仅 mock_interview 有谓语：`result.get("phase") != "done"`（首轮 phase=answering 即锁定）
- 组合层接线：root 与 mock_interview 共享 checkpointer 实例，但 config 按 R6 派生 thread_id

## 验收标准

- AC1 映射表被执行体覆盖：A1/C/D → Workflow，B1/闲聊 → ReAct，澄清 → 单节点（组合层五子图接线时成立）
- AC2 面试进行中（phase=answering）的用户消息不经 route、直通 mock_interview；phase=done 后恢复正常路由
- AC3 状态隔离：mock_interview 状态不继承根图历史消息；根图 active_graph 跨轮持久、不被子图写入抹掉
- AC4 根图消息无重复：连续两轮面试后，根 messages 中每条内容只出现一次
- AC5 ReAct 步数上限生效（recursion_limit 注入），且 react/b1 提示词含强制作答约束
- AC6 C/D 完成后：结构化完成事件触发 + 分析报告落会话；对结果的追问被路由到 react_agent，且其 messages 输入含该分析报告
- AC7 Workflow 节点内无 agent 调用（C/D 实现时以代码审查 + 契约测试校验）

## 实施计划

| 步骤 | 内容 | 测试 |
|---|---|---|
| ① | 本规格落盘（纯决策，无代码） | — |
| ② | `graphs/composition` 组合层规格（01-planned）：根图接线 + R5/R6 机制实现 | AC2/AC3/AC4 各有测试（根图 + mock_interview 链式多轮） |
| ③ | `workflows/resume-analysis`、`workflows/interview-review` 规格（01-planned） | 按 R1/R3/R4：AC6/AC7 + 各自流程 AC |
| ④ | `prompts/react/b1.v2` 提示词更新：强制作答 + 会话上下文利用 | AC5 提示词快照测试 |

## Definition of Done

- [x] 映射表与 R1-R6 无歧义，被 graphs/composition 等后续规格引用（引用用相对路径）
- [x] AC1-AC7 在各自实现规格中全部有测试且通过（本规格本身无代码）
- [x] 会话连续性机制契约（active_graph + active_predicates + 派生 thread_id）与组合层实现一致

## 实现备注

- 覆盖落点（2026-10-21）：AC2-AC4 → [../graphs/composition.md](../graphs/composition.md)；AC1/AC6/AC7 → [../workflows/resume-analysis.md](../workflows/resume-analysis.md) 与 [../workflows/interview-review.md](../workflows/interview-review.md)；AC5 提示词部分 + AC6 追问链路端到端 → [../prompts/react-b1-v2.md](../prompts/react-b1-v2.md)
- R4 在路由层同样成立（react-b1-v2 模块三增补）：根图 route 节点必须把最近对话摘要传给分类器，否则「对刚产出报告的追问」会被 LLM 分类器错分为 resume_analysis / interview_review；已由 AC6 真实冒烟验证修复
- R6 的 checkpoint_ns 结论被生产布局实验修正后吸收进 interview-review 规格：根图 wrapper 调用的子图检查点落 ns=子图名（顶层直接 invoke 则落 ns=""），读侧 `get_tuple({"thread_id": f"{root}:mock_interview", "checkpoint_ns": "mock_interview"})`
