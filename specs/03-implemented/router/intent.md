# Spec: 意图识别与路由决策 (router/intent)

> 状态：已实现（2026-10-21，流转至 03-implemented）。流转规则见 specs/README.md。

## 目标
将用户消息映射为 RouteDecision，供根图选择执行图（ReAct 或 Workflow）。

## 意图枚举（v1）
`mock_interview` 模拟面试 / `resume_analysis` 简历分析 / `interview_review` 面试复盘 / `knowledge_qa` 知识问答 / `chitchat` 闲聊 / `clarify` 澄清

## 输入/输出契约
- 输入：`user_message: str`，`history_summary: str`（可为空）
- 输出：`RouteDecision { intent, confidence, slots, source, target_graph }`
  - `slots`：`InterviewSlots { position, years, round, difficulty }`，提取不到为 None
  - `source`：`rule`（规则命中）/ `llm`（分类器采纳）/ `fallback`（重试耗尽降级）
  - `target_graph`：由路由表解析（见下）

## 路由表（v1）
| intent | target_graph |
|---|---|
| mock_interview | mock_interview |
| resume_analysis | resume_analysis |
| interview_review | interview_review |
| knowledge_qa | react_agent |
| chitchat | react_agent |
| clarify | clarify |

## 依赖接口
- LLM：最小协议 `llm.invoke(list[str]) -> 带 .content 属性的对象`，与 LangChain BaseChatModel 鸭子兼容；本层不 import 任何框架
- 提示词：经 PromptRegistry 按 `router/intent` 渲染（版本化，见 specs/prompts/registry.md）
- 钩子：经 HookBus 触发 `route.decide` / `error` 事件

## 行为
1. 空白消息 → chitchat（source=rule，不调 LLM）
2. 规则路由：唯一命中 → 不调用 LLM，confidence=1.0，source=rule，槽位仅提取年限（正则），其余为空
3. 多规则冲突或未命中 → LLM 分类器（渲染版本化提示词，含少样本）
4. 分类结果 confidence ≥ 0.7 且 intent ≠ clarify → 采纳（source=llm）
5. 否则（低置信或明确 clarify）→ 降级 clarify，不进入任何业务子图
6. LLM 输出非法 JSON（含未知意图值、置信度越界）→ 追加纠错消息重试，最多 1 次
7. 重试耗尽 → clarify（source=fallback，confidence=0.0）+ 触发 `error` 钩子事件
8. 所有决策路径都触发 `route.decide` 钩子事件（payload=RouteDecision，metadata 含 intent/confidence/source/target_graph）

## 验收标准（每条 → 至少一个测试）
- AC1 规则单一命中（如"帮我模拟面试"）→ mock_interview，且无 LLM 调用
- AC2 规则冲突（如"先看简历再模拟面试"）→ 走 LLM 分类器
- AC3 高置信分类结果 → 采纳，target_graph 与路由表一致
- AC4 低置信（<0.7）→ clarify；显式 clarify 即使高置信也 → clarify
- AC5 非法 JSON 重试成功 → 采纳，且第二次调用含纠错提示
- AC6 重试耗尽 → clarify + source=fallback + `error` 事件已触发
- AC7 槽位透传：LLM 槽位原样保留；规则命中仅提取年限
- AC8 空白消息 → chitchat，无 LLM 调用
- AC9 每条路径均触发 `route.decide`，metadata 完整

## 边界与异常
- 拉丁文关键词按词边界匹配（"hi" 不得命中 "this"）
- LLM invoke 抛出的网络/服务异常**不**在本层捕获（由上层根图错误处理兜底），本层只处理"响应无法解析"
- 规则表与意图枚举变更需同步更新本 spec 与 eval 集
