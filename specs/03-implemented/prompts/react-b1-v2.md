# 计划：react/b1.v2 提示词（强制作答 + 会话上下文利用）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：[../architecture/execution-boundaries.md](../architecture/execution-boundaries.md)（R2/R4、AC5/AC6 的提示词部分）；docs/system-design.md §4（ReAct 执行体限步数 + 强制收尾）。

## 目标

把 execution-boundaries 的 R2（强制作答）与 R4（会话结论利用）落到 B1 的 ReAct 系统提示词：新版本 react/b1.v2，覆盖 AC5/AC6 中尚未实现的提示词部分——AC5 的「提示词含强制作答约束」、AC6/R4 的「提示词约定会话中已有分析结论时结合其回答追问」。本步完成后 execution-boundaries 的 AC1-AC7 全部有测试覆盖，该规格可流转 03-implemented。

## 输入/输出契约

### 模块一：`app/prompts/templates/react/b1.v2.yaml`（新增）

- b1.v1 保留在仓库（显式版本可回退）；PromptRegistry 按最新版本渲染，`PROMPT_REACT = "react/b1"` 无需改动
- 保留 v1 规则 1-4（工具优先 / 未命中自答 / 结构化回答 / 语气中文）
- 新增「强制作答约束」（R2）：每轮最终必须输出面向用户的答案文本收尾——即使工具无结果也必须给出自己的回答，不得只调工具不回答；连续两次工具调用未获有用信息时停止调用、直接作答
- 新增「会话上下文利用」（R4）：会话历史中已有分析结论（简历分析报告 / 面试复盘报告）时，用户追问其内容必须结合该结论回答——先引用结论相关要点，再补充解释或具体建议，不泛泛而谈、不重新分析

### 模块二：无代码模块变化

- `recursion_limit`（≤8 步，R2 步数上限）已由组合层注入，本规格不重复
- `build_react_agent` / `composition.py` 接线不变（渲染时自动取 v2）

### 模块三（AC6 冒烟发现的后置增补）：路由上下文修复

AC6 首轮冒烟暴露：追问「报告里说的量化项目经历，具体应该怎么写？」被 LLM 分类器分到 resume_analysis（重跑一次空分析）而非 knowledge_qa。根因：根图 route 节点只传了用户消息、未传历史摘要，分类器看不到刚产出的报告。修复（提示词部分已在模块一，这里是路由部分）：

- `app/graphs/root.py`：新增 `_history_summary(messages)`（排除待分类的最后一条用户消息，最近 4 条、每条截 400 字，格式「用户：…/助手：…」）；route 节点改为 `dispatcher.route(text, _history_summary(messages))`
- `app/prompts/templates/router/intent.v2.yaml`（新增，v1 保留）：新增上下文规则（要求 4）——摘要显示刚产出简历分析/面试复盘报告时，对报告内容的追问 → knowledge_qa，只有新简历原文或明确要求重新分析才分 resume_analysis / interview_review；补 1 条少样本示例
- `tests/support/graphs.py`：StubDispatcher 记录 `histories` 供断言

## 验收标准

- AC1（版本共存）`registry.render("react/b1")` 渲染 v2；`render("react/b1", version=1)` 仍是 v1 原文（可回退）
- AC2（强制作答，AC5 提示词部分）v2 渲染结果含「以答案文本收尾」与「不得只调工具不回答」类约束（文本断言）
- AC3（上下文利用，AC6 提示词部分）v2 渲染结果含「结合该结论回答」/「先引用」类约束（文本断言）
- AC4（快照）v2 渲染 syrupy 快照锁定（tests/graph/test_react_agent.py 内，同 test_mock_interview 模式）
- AC5（回归）组合层 ReAct 端到端回归全绿：既有 scripted tool_call → 真查题库 → 作答测试（composition AC7 闲聊链路）不受 v2 影响
- AC6（真实追问冒烟）smoke_chat 真实 deepseek：先简历分析、再追问报告内容，回复引用报告要点（人工判断，记录结果）

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 模板 v2 | templates/react/b1.v2.yaml | AC1-AC4（tests/graph/test_react_agent.py 增补） |
| ② 回归 | — | AC5 全量 pytest |
| ③ 冒烟 + 模块三 | scripts/smoke_chat.py；root.py / intent.v2.yaml | AC6 冒烟失败 → 模块三修复 → 复验；测试：root 摘要传递（StubDispatcher.histories）、分类器摘要入提示词、intent 快照更新 |
| ④ 收尾 | execution-boundaries.md 流转 | DoD 勾选 + 03-implemented README 增行 |

## Definition of Done

- [x] AC1-AC5 全部有测试且通过；全量 153 例 + 11 快照回归通过
- [x] ruff / mypy（app 全量）干净
- [x] AC6 真实冒烟通过（追问路由到 B1 且引用报告要点）
- [x] execution-boundaries.md 流转 01-planned → 03-implemented（AC1-AC7 全部覆盖），索引同步更新

## 实现备注

- 模块三为 AC6 冒烟暴露的缺口后置增补：提示词层（模块一）只是必要条件，路由层不传历史摘要时分类器根本看不到报告（R4 在路由层同样成立）
- intent.v2 快照更新在 tests/unit/test_prompt_registry.py（既有测试，v1→v2 属预期 diff；v1 模板保留可回退）；模块三新增断言集中在 test_intent_classifier.py（摘要入提示词）与 test_graph/test_root.py（route 节点传摘要）
- AC6 复验结果（2026-10-21）：追问被路由到 react_agent，回复以「结合前文报告中的第 1 条建议…和第 5 条…」开头引用报告要点后展开，通过
