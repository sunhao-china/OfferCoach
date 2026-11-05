# OfferCoach · 求职面试训练助手

面向社招 1-5 年求职者的 AI 面试训练 Web 应用：模拟面试、知识问答、简历分析、面试复盘、每日练习五大业务，后端 DeepSeek 大模型分档调用 + LangGraph 编排，自建 1497 题题库与三层记忆系统，前端原生 JS 无构建。

在线体验：<http://8.137.192.231:8002>

## 功能

- **模拟面试**：状态机四阶段（提问 → 判定 → 评分 → 报告），追问锚定题库参考要点、难度自适应升档、「结束」口令确定性退出
- **知识问答**：意图路由 + ReAct 循环，手写循环逐 token 流式输出（SSE 终稿分块）
- **简历分析**：粘贴或上传 PDF/docx，可附目标岗位 JD 对照分析
- **面试复盘**：选一轮已完成面试生成逐题点评报告
- **每日练习**：按计划随机抽题单，score.v2 三维判分 + 参考要点对照 + 连续天数统计；**错题纠正**——「分类有误？」一键让 AI 重新识别题目岗位/类别并纠正题库
- **历史回顾**：已完成会话的列表与详情回放
- **三层记忆**：事件总结 → Qdrant 长期记忆/用户画像（bge-m3 自建 embedding）→ 面试开场装配注入

## 架构

```
app/
├── router/          # 意图识别与路由决策
├── graphs/          # LangGraph 根图 + 子图（react / mock_interview / 工作流）
├── workflows/       # 一次性管道：简历分析、面试复盘
├── drill/           # 练习计划服务层（独立于 langgraph）
├── question_bank/   # 题库：查询层 / 规范化 / 导入 / 校验
├── prompts/         # 提示词注册表 + 版本化 YAML 模板
├── memory/          # 三层记忆（store / summarizer / profile / assembler / scheduler）
├── observability/   # Langfuse：提示词管理 / 测试集 / 评估实验
├── api/             # FastAPI 层：鉴权 / 限流 / SSE / 业务端点 / 前端静态托管
└── web/             # 前端单页（vanilla JS 无构建，静态托管）
```

关键机制：会话按业务与账号双重隔离（checkpoint 双命名空间兼容嵌套执行）；LLM 分档调用（strong 判分生成 / cheap 分类澄清）；登录四层加固（PBKDF2 600k + 零依赖图片验证码 + 24h 滑动窗口锁定 + token 7 天过期）+ 全接口限流；SDD/TDD 五段式规格驱动（`specs/` 状态文件夹流转）。

## 快速开始

```bash
# Windows（Python 3.11，Git Bash）
python -m venv .venv
.venv/Scripts/pip install -r scripts/deploy/requirements.txt
export DEEPSEEK_API_KEY=sk-xxx           # 环境变量（本地无 Key 时仅题库/鉴权可用）
.venv/Scripts/python scripts/run_server.py   # http://127.0.0.1:8002
```

首次启动会自动建本地开发库（sqlite：`data/offercoach_dev.db` 题库 + `data/offercoach_sessions.db` 会话）；生产环境设 `DATABASE_URL=mysql+pymysql://…` 走 MySQL 题库。

## 测试

```bash
LANGCHAIN_TRACING_V2=false PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -o addopts="" -q
# 全量 475 测试 + 18 快照；ruff/mypy 零告警
```

## 部署

```bash
# scripts/deploy/deploy.py（paramiko；凭据一律环境变量，不落仓库）
OC_SSH_PASS=… OC_MYSQL_PASS=… python scripts/deploy/deploy.py deploy <host>
```

服务器 systemd 双服务（offercoach 主服务 + offercoach-embed bge-m3 127.0.0.1:8090），与同机其他生产服务资源隔离共存。

## 目录速览

- `app/` — 后端全部业务代码（见上方架构图）
- `specs/` — SDD 规格（01-planned / 02-in-progress / 03-implemented / 04-archived）
- `data/question_bank/` — 题库种子（seed_v1 200 题 + java_guide 297 题 + 三期多岗位 1000 题，jsonl；JavaGuide 上游源码不入仓，`scripts/fetch_java_guide.py` 可重取）
- `data/evals/` — Langfuse 评估测试集素材
- `scripts/` — 运行 / 部署 / 题库构建与评估 CLI
- `tests/` — 475 测试 + 18 提示词快照

## 安全约定

凭据（服务器密码 / 模型 API Key / Langfuse 密钥）一律走环境变量或服务器侧 env 文件，**绝不写入仓库任何文件**；本地数据库（含账号数据）不入仓（.gitignore）。
