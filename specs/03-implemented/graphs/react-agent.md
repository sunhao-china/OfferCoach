# 计划：ReAct 子图（B1 知识问答）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：docs/system-design.md §4.1 ReAct 模式。

实现备注：
- langgraph 1.x 将 `create_react_agent` 标记为 deprecated（迁移目标 langchain.agents.create_agent，需引入 langchain 依赖）；当前保留并在 pyproject filterwarnings 静默，迁移随依赖升级处理
- 测试桩模型需子类化 GenericFakeChatModel 补 bind_tools 空实现

## 目标

B1 知识问答 / 闲聊的 ReAct 执行体：`create_react_agent` + 题库检索工具。MVP 只挂题库工具；知识库/联网/记忆工具后置（依赖服务器资源）。

## 输入/输出契约

- 模块：`app/tools/question_bank.py`、`app/graphs/react_agent.py`
- `build_question_bank_tool(store) -> BaseTool`
  - 工具名 `search_question_bank(keywords: str, category: str | None, difficulty: str | None) -> str`
  - 返回 JSON 化题目列表（question_id / category / difficulty / question / reference_points / followups），limit=5
  - 空结果返回明确提示："题库中未找到匹配的题目"
- `build_react_agent(model, store, system_prompt: str)`：system_prompt 由调用方经 PromptRegistry 渲染（资产 `react/b1.v1.yaml`：优先检索题库工具、无结果用自身知识、回答结构化）

## 验收标准

- AC1 工具检索命中：返回文本含题目 id 与题面（sqlite 种子库）
- AC2 工具空结果：返回"未找到"提示
- AC3 工具参数过滤生效：category / difficulty 传入后结果被过滤
- AC4 agent 图可编译，桩模型（GenericFakeChatModel 脚本化 tool_call）驱动时：工具被调用、最终答案成为最后一条 AI 消息

## 实施计划

| 文件 | 动作 |
|---|---|
| `app/tools/question_bank.py` | 新建 |
| `app/graphs/react_agent.py` | 新建 |
| `app/prompts/templates/react/b1.v1.yaml` | 新建提示词资产 |
| `tests/unit/test_question_bank_tool.py` | 新建：AC1~AC3 |
| `tests/graph/test_react_agent.py` | 新建：AC4 |

## Definition of Done

- [x] AC1~AC4 全绿
- [x] ruff + mypy 干净
