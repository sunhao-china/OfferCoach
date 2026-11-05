# 三层记忆（推迟清单 ②，依赖 ③ 已先行落地）

## 目标

跨会话个性化闭环（依据 docs/memory-design.md v1.1）：会话结束 → LLM 总结 → Qdrant 长期记忆点 + 画像候选；候选证据 ≥2 晋升画像；新会话装配时注入画像 + 长期记忆召回；T1/T2 定时兜底与遗忘。**MVP 裁剪（遵循架构简化偏好，规格内说明）**：(1) **Redis 短期记忆层不建**——会话内消息已全量在 sqlite checkpointer（B1 提示词已用），双写 Redis 窗口零增量价值纯增复杂度；Redis `oc:` 前缀红线保留，如后续需要"跨业务会话级滚动压缩"再启用。(2) **召回降级**：embedding 服务不可用时降级为"仅最近 2 场按时间序"（零向量），记忆层任何故障 fail-open 不阻断业务。(3) 测试用 FakeMemoryStore（内存），真实 Qdrant 靠服务器冒烟验证。

## 输入输出契约

输入：
- Qdrant 服务（fin-qdrant 容器 127.0.0.1:6333，只建 `oc_` 集合）；bge-m3 embedding 服务（③ 已部署 127.0.0.1:8090）
- HookBus 既有事件：mock_interview.end / resume_analysis.complete / interview_review.complete（payload 含 user_id/session_id）
- checkpointer 可读会话消息（read_checkpoint 双 ns 模式已有）
- chat_sessions 表（sqlite）无 last_message_at 列（需迁移）；chat 会话无结束事件（靠 T1 兜底判定）
- 依赖新增：qdrant-client、apscheduler（锁版本进 requirements）

输出：
- app/memory/store.py：MemoryStore 协议 + QdrantMemoryStore（懒连接、`oc_` 四集合：long_term_memory / profile_memory / profile_candidates / profile_archive，建集合时按 embedder 实际维度创建）+ FakeMemoryStore（测试）
- app/memory/summarizer.py：会话总结（strong LLM + prompts/memory/summarize.yaml）→ 长期记忆点 + 画像候选点（幂等：点 id = `{user_id}:{session_id}`）
- app/memory/profile.py：晋升（候选证据 ≥2 独立条 → profile_memory 点 + 删候选）+ 遗忘状态机（active → decayed → forgotten；长期删点、画像归档）
- app/memory/assembler.py：ContextPackage 装配（画像 active 条目按 importance 截断 + 长期记忆最近 2 场时间序 + top-k 语义召回，embedding 失败降级时间序）；装配命中刷新 last_recalled_at/recall_count
- app/memory/scheduler.py：APScheduler 进程内 T1（每 10 分钟：last_message_at 超 30 分钟且无长期记忆点的会话 → 总结）/ T2（每天 03:00：三集合遗忘扫描 + 晋升扫描）
- 接线：build_runtime 订阅 HookBus 结束事件即时总结 + 启动 scheduler；B1 chat（react_agent system 注入）与 mock_interview 开场注入 ContextPackage（构造参数 memory=None 缺省，测试零行为）；chat_sessions 加 last_message_at 列（消息端点更新 + sqlite 迁移）
- pyproject + scripts/deploy/requirements.txt 加 qdrant-client/apscheduler

## 验收标准（AC）

1. MemoryStore/FakeMemoryStore：upsert/查询（user_id 过滤）/状态更新/删除语义单测覆盖（FakeMemoryStore 行为对齐真实客户端接口）
2. 总结器：脚本化 LLM → 长期记忆点结构正确（topic/summary/importance/status=active）+ 画像候选点生成；幂等（同会话二次总结不产生重复点）；chat/面试/复盘三类会话各有一条用例
3. 晋升与遗忘（假时钟）：候选 1 证据不晋升、≥2 证据晋升且候选删除；长期记忆 30 天未召回 → decayed → 再 30 天删除；画像 14/30 天 → decayed → forgotten 归档 archive（原 collection 移除）
4. 装配器：画像 active 注入 + 长期记忆时间序 2 场 + 语义 top-k（DeterministicEmbedder）；embedding 抛错 → 降级时间序不抛；命中后 last_recalled_at 刷新
5. 端到端（FakeMemoryStore + StubLLM）：interview 会话跑完一轮 → 结束事件触发总结（事件驱动）；chat 会话发消息 → last_message_at 更新；T1 扫描（假时钟推进）对超时无点会话补总结；B1 chat 与 interview 开场的新消息能读到已存画像注入
6. 全量回归：memory=None 缺省下既有 316 测试 + 17 快照全绿（零行为不变量）
7. 服务器部署：requirements 加依赖重跑 deploy；Qdrant 四集合建成（维度 1024）；服务器冒烟——真实面试一轮 → 结束事件即时总结 → Qdrant 有 long_term 点且 payload 完整 → 新会话 B1 回答体现画像召回（冒烟脚本断言装配日志/行为）+ T2 状态机手工触发一次验证；redis 零触碰、fin 资源零触碰
8. 红线合规：Qdrant 只建/写 `oc_` 集合；不读 chunks 等 fin 集合；凭据零落盘

## 实施计划

1. 规格 → 01-planned（本文件，开工流转 02；③ 已完成）
2. app/memory/store.py（协议 + Fake + Qdrant 实现）+ tests/unit/test_memory_store.py（AC1）
3. prompts/memory/summarize.yaml + summarizer.py + tests（AC2）
4. profile.py（晋升/遗忘）+ tests（AC3）
5. assembler.py + tests（AC4）
6. 接线：executor 消息路径 last_message_at、build_runtime 事件订阅 + scheduler + react/mock_interview 注入（memory=None 缺省）+ tests（AC5）
7. ruff/mypy + 全量回归（AC6）
8. deploy（requirements + 迁移）+ 服务器冒烟（AC7/AC8）
9. 收尾：流转 03 + README + 记忆同步 + 中文报告

## Definition of Done

AC1-8 全部通过；冒烟与 Qdrant 证据写入实现备注；README 与记忆同步；红线合规；Redis 层裁剪决策在报告与记忆中明示。

## 实现备注（2026-10-25）

**流程**：本地代码（app/memory/ 六模块 + tests）→ 全量回归 → 服务器部署（deploy 重跑）→ AC7 服务器冒烟（真实 LLM + 真实 bge-m3 + 真实 Qdrant）。③ bge-m3 embedding 先行部署（② 的语义召回消费方）。

**架构决策（MVP 裁剪）**：Redis 短期记忆层不建——会话消息已全量在 sqlite checkpointer（B1 提示词已用），双写 Redis 窗口零增量价值。Redis `oc:` 前缀红线保留未启用，后续如需跨会话滚动压缩再启用。

**服务器证据**（8.137.174.74，2026-10-25）：
- memory.ensure() 启动懒建 4 个 `oc_` 集合，维度 1024；`chunks` 原样未触碰；题库 seed 幂等（skip 497）；部署后 healthz 200
- **AC7 冒烟 6/6 全过**：
  - [1/6] 真实面试一轮（开始→结束），结束事件即时总结（daemon 线程）
  - [2/6] Qdrant long_term 点 payload 完整（user_id/session_id/topic/importance/status=active），topic 为真实 LLM 总结（"Java 后端 3 年经验模拟技术面试，首题…用户开场即主动终止面试"，importance 0.35）
  - [3/6] B1 chat 发知识问题 → 装配召回命中 → **recall_count 0→1**
  - [4/6] T2 扫描手工触发（独立进程）：decayed/forgotten/promoted 计数正确（全新点，均 0）
  - [5/6] 集合清单 = {chunks} ∪ 4 oc_，维度 1024；[6/6] healthz 200

**关键坑与修复**：
1. **Qdrant 点 ID 只接受 uint64/UUID**——原始逻辑 id `{user_id}:{session_type}:{session_id}` 是字符串，AC7 冒烟真实写入时 Qdrant 返回 400 才发现（单测 Fake 全绿掩盖）。修复：uuid5 确定性映射（LOGICAL_NAMESPACE 常量命名空间）+ `payload["logical_id"]` 往返还原逻辑 id；Fake 同步注入 parity + 2 个新测试锁定。
2. 冒烟 [3/6] 首跑"召回计数未刷新"——Langfuse v2 observations 取证（trace caf41086）：消息"结合我之前的面试表现，给点练习建议"被意图分类器路由到 interview_review（D 复盘）子图，复盘无装配调用，记忆链路本身无 bug。冒烟消息改为纯知识问题"什么是线程池？"后 B1 装配命中。**产品行为注意**：含"面试表现"措辞的会话消息会进复盘而非 B1。
3. Qdrant client 1.16.2 vs server 1.19.2 版本 UserWarning（无害；后续可 check_compatibility=False）。

**偏差说明**：
- 规格"点 id = {user_id}:{session_id}"落地为三段 `{user_id}:{session_type}:{session_id}`（画像候选与长期点同用户同会话需 session_type 区分）+ uuid5 wire 映射
- AC6 基线为 313 + 17 快照（规格起草时写 316）；② 收尾全量 **357 + 17 快照**全绿（新增 44 个）
- 部署期间服务器曾整机不可达（实例停止，用户重启后恢复）

**AC 对照**：AC1-5 ✓ 本地单测（357 全量内）；AC6 ✓ 357 + 17 快照全绿（memory=None 缺省零行为）；AC7 ✓ 服务器冒烟 6/6（证据如上）；AC8 ✓ Redis 零触碰（层未建）、Qdrant 只建/写 oc_、chunks 未读、凭据零落盘（服务器 env 文件继承，不进部署日志）。

