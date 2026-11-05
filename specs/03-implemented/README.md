# 03-implemented：已实现

> AC 全部有测试且通过、ruff/mypy 干净的规格。

## 清单

| 规格 | 模块 | 完成日期 |
|---|---|---|
| [router/intent.md](router/intent.md) | 意图识别与路由决策 | 2026-10-21 |
| [prompts/registry.md](prompts/registry.md) | 提示词注册表 | 2026-10-21 |
| [question_bank/schema.md](question_bank/schema.md) | 题库 Schema 与导入 | 2026-10-21 |
| [core/llm-factory.md](core/llm-factory.md) | 模型工厂（deepseek 分档） | 2026-10-21 |
| [question_bank/store.md](question_bank/store.md) | 题库查询层 | 2026-10-21 |
| [graphs/root.md](graphs/root.md) | 根图 + 澄清/占位子图 | 2026-10-21 |
| [graphs/react-agent.md](graphs/react-agent.md) | ReAct 子图（B1 知识问答） | 2026-10-21 |
| [graphs/mock-interview.md](graphs/mock-interview.md) | A1 模拟面试工作流 | 2026-10-21 |
| [graphs/composition.md](graphs/composition.md) | 组合层：根图接线 + R5/R6（active_graph 锁定 + 派生 thread_id 隔离） | 2026-10-21 |
| [workflows/resume-analysis.md](workflows/resume-analysis.md) | C 简历分析一次性管道（extract → analyze → report） | 2026-10-21 |
| [workflows/interview-review.md](workflows/interview-review.md) | D 面试复盘一次性管道（load → per_question → summary） | 2026-10-21 |
| [prompts/react-b1-v2.md](prompts/react-b1-v2.md) | B1 提示词 v2：强制作答 + 会话上下文利用 + 路由上下文修复（模块三） | 2026-10-21 |
| [architecture/execution-boundaries.md](architecture/execution-boundaries.md) | 执行模式分层与相邻边界（R1-R6 + AC1-AC7 全部覆盖） | 2026-10-21 |
| [api/web-api.md](api/web-api.md) | Web API 层：FastAPI 按业务类型建会话（页面分离，直连子图） | 2026-10-21 |
| [web/frontend.md](web/frontend.md) | 前端四页（vanilla SPA 无构建 + 静态托管 + 会话列表端点） | 2026-10-21 |
| [api/resume-upload-jd.md](api/resume-upload-jd.md) | 简历文件上传（PDF/docx 提取）+ JD 对照（analyze/report v2） | 2026-10-21 |
| [api/sse-streaming.md](api/sse-streaming.md) | SSE 流式输出（终稿分块推送，stream 开关兼容 JSON） | 2026-10-21 |
| [api/interview-options.md](api/interview-options.md) | 面试难度/岗位显式选择（SessionCreate + 会话列 + 老库补列迁移） | 2026-10-22 |
| [api/real-token-streaming.md](api/real-token-streaming.md) | 真实 token 流式输出（SSE 升级 + ReAct 手写循环） | 2026-10-22 |
| [web/session-list-page.md](web/session-list-page.md) | 会话列表页（多会话管理：列表/继续/删除 + DELETE 端点） | 2026-10-22 |
| [question_bank/java-guide-import.md](question_bank/java-guide-import.md) | 题库扩充二期：JavaGuide 开源题库导入（fetch+extract+LLM 规范化，497 题入库） | 2026-10-22 |
| [interview/experience-polish.md](interview/experience-polish.md) | 面试体验打磨：题库要点/追问接入面试与复盘全链路（judge/score/report/per_question v2） | 2026-10-22 |
| [interview/exit-phrase.md](interview/exit-phrase.md) | 模拟面试「结束」口令确定性退出（judge 短接，冒烟发现并修复） | 2026-10-22 |
| [web/auth-login.md](web/auth-login.md) | 简单登录：注册/登录/登出 + 全 API Bearer 鉴权 + 会话按账号隔离 + 前端登录层 | 2026-10-22 |
| [web/image-captcha.md](web/image-captcha.md) | 图片验证码：SVG 零依赖生成 + 注册恒要求 + 登录失败 3 次触发（防爆破/防批量注册） | 2026-10-22 |
| [web/login-hardening.md](web/login-hardening.md) | 登录安全加固：24h 滑动窗口 5 次锁定 + 注册密码复杂度 + PBKDF2 迭代 600k（老散列兼容） | 2026-10-22 |
| [deploy/server-deploy.md](deploy/server-deploy.md) | 云服务器部署：8.137.174.74（fin 生产并存零触碰）+ Python 3.11 + MySQL 题库 497 题 + systemd 常驻 + 公网冒烟 20/20 | 2026-10-22 |
| [observability/langfuse-tracing.md](observability/langfuse-tracing.md) | Langfuse 监控接入：CallbackHandler 全链路 trace + session/user 元数据 + 本地与服务器双冒烟云端验证 | 2026-10-22 |
| [web/token-expiry-rate-limit.md](web/token-expiry-rate-limit.md) | 服务器侧登录加固二：token 7 天滑动过期 + 接口限流（IP 级/用户级滑动窗口，零新依赖） | 2026-10-22 |
| [architecture/bge-m3-embedding.md](architecture/bge-m3-embedding.md) | bge-m3 embedding 服务：127.0.0.1:8090 /embed 1024 维（自有模型副本 + torch 2.7.1 + 严格冒烟） | 2026-10-25 |
| [architecture/three-tier-memory.md](architecture/three-tier-memory.md) | 三层记忆：事件总结 → Qdrant 长期/画像（uuid5 点 ID）→ 装配召回注入（Redis 层 MVP 裁剪） | 2026-10-25 |
| [web/history-review-page.md](web/history-review-page.md) | 历史回顾页：练习成果浏览（面试/简历/复盘记录 + 完成态 + 报告预览 + 详情回放，零新增存储） | 2026-10-25 |
| [observability/langfuse-eval.md](observability/langfuse-eval.md) | Langfuse 评估体系：提示词管理（24 版本入云）+ 三测试集（75 条）+ 四实验真实跑分（intent 1.0 / judge 0.55 / b1 coverage 4.5）+ 版本替换对比 | 2026-10-25 |
| [question_bank/multi-position-expansion.md](question_bank/multi-position-expansion.md) | 题库扩充三期：Python/C/C++/C#/AI Agent/项目经理 六岗位 1000 题（总量 1497）+ 岗位别名映射 + B1 跨岗位检索（整句 keywords 拆词兜底）| 2026-10-25 |
| [drill/practice-plan.md](drill/practice-plan.md) | 练习计划：每日题单随机抽题 + score.v2 逐题判分（三维+点评+参考要点对照）+ 进度连续天数（新建「练习」页，独立服务层不走 langgraph）| 2026-10-25 |
| [drill/misclassification-correction.md](drill/misclassification-correction.md) | 错题纠正：练习页「分类有误？」一键重识别岗位/类别（cheap 档+动态枚举校验+题 ID 不变零悬挂），冒烟真实纠正一条 python_web 错题 | 2026-10-27 |
