# 云服务器部署（8.137.174.74）

## 目标

把 OfferCoach 部署到用户新启动的云服务器 8.137.174.74（侦察确认是旧服务器磁盘克隆的新实例：Ubuntu 22.04，**fin 项目生产服务并存**——8001 端口 uvicorn、fin-mysql / fin-redis / fin-qdrant 三个容器、/data/financial 都在跑）。上传代码 + 题库数据，MySQL `offercoach` 库导入 497 题，systemd 常驻 0.0.0.0:8000，本地真实 HTTP 冒烟通过。

## 输入输出契约

输入：
- 本地代码（app/ + scripts/ + pyproject.toml）与题库数据（data/question_bank/seed_v1 + java_guide_v1，共 20MB）
- 本地环境 DEEPSEEK_API_KEY；服务器 root 密码与 MySQL root 密码（一律走环境变量，不进仓库文件）
- 服务器现状（已侦察）：Ubuntu 22.04 / 8 核 32G / 系统 Python 3.10（不满足 >=3.11，deadsnakes PPA 已配置可直接 apt 装 3.11）/ docker 跑着 fin 三容器（127.0.0.1 绑定）/ pypi 可达

输出：
- /data/offercoach/：代码 + 题库数据 + .venv（Python 3.11，运行时依赖按本地版本锁定安装）
- /data/offercoach.env（仓库外、600）：DATABASE_URL（MySQL offercoach 库）+ DEEPSEEK_API_KEY + 监听配置
- systemd offercoach.service：常驻 + 开机自启 + 崩溃重启，0.0.0.0:8000
- MySQL `offercoach` 库 question_bank 表 497 行（seed_v1 200 + java_guide_v1 297）

## 验收标准（AC）

1. run_server.py 支持 OFFERCOACH_HOST / OFFERCOACH_PORT / DATABASE_URL 环境变量覆盖；本地缺省行为不变（127.0.0.1:8000 + 本地 sqlite 题库），本地全套测试不受影响
2. 服务器 venv（Python 3.11）依赖安装成功，导入 CLI 可用
3. MySQL 只建 `offercoach` 库（charset utf8mb4），两次导入共 497 行，question_bank 行数与分布契约一致
4. offercoach.service active(running) 且 enabled，本机 healthz 返回 200
5. 从本地对 8.137.174.74:8000 真实 HTTP 冒烟通过：healthz / 首页 / 验证码 / 注册 / 登录（含 3 次错触发验证码）/ chat 会话 + B1 真实 LLM 问答（SSE）/ 面试会话一轮问答（judge）/ 登出
6. 红线合规：全程未读/未写 fin 库、fin:* Redis 键、chunks 集合、/data/financial；凭据未写入仓库任何文件

## 实施计划

1. 改 scripts/run_server.py：env 覆盖 host/port/题库连接串（缺省不变）
2. 本地跑 ruff + 全量测试确认无回归
3. 服务器：apt 装 python3.11 + python3.11-venv
4. tar 上传（app/ scripts/ pyproject.toml data/question_bank/ + 部署锁定依赖文件）→ /data/offercoach
5. 服务器 venv + pip install 锁定依赖（含 cryptography，MySQL 8 caching_sha2 认证需要）
6. 用本 venv 的 pymysql 建库 offercoach（不 docker exec 进 fin 容器）；DATABASE_URL 指 MySQL 跑两次导入（seed_v1、java_guide_v1），核对行数
7. 写 /data/offercoach.env（600）+ systemd unit + enable/start + healthz
8. 本地真实 HTTP 冒烟（临时 .smoke_deploy.py，用后即删）：全流程 + SSE + 面试一轮
9. 若本地连不上 8000 → 检查服务器防火墙 → 报告用户去阿里云控制台开安全组 8000
10. 规格流转 03-implemented + README + 记忆同步 + 中文报告

## Definition of Done

AC1-6 全部通过；冒烟摘录写入本规格实现备注；specs/03-implemented/README.md 与记忆文件同步；确认 fin 资源零触碰；报告含访问地址与安全组提示。

---

## 实现备注（2026-10-22 部署完成）

**服务器与栈**：8.137.174.74（Ubuntu 22.04，旧服务器磁盘克隆的新实例，**fin 项目生产服务并存**：8001 端口 uvicorn + fin-mysql/fin-redis/fin-qdrant 容器，全程零触碰）。系统只有 Python 3.10，用 deadsnakes PPA（旧盘已配置）apt 装了 python3.11（3.11.0rc1）+ venv。pip 走阿里云镜像（服务器已有配置）。

**部署物**：
- /data/offercoach/：app + scripts + pyproject + data/offercoach_dev.db（sqlite 兜底）+ data/question_bank（vendor 源数据未上传——仅重取需要）
- /data/offercoach.env（仓库外，chmod 600）：DATABASE_URL（MySQL offercoach 库）+ DEEPSEEK_API_KEY + OFFERCOACH_HOST=0.0.0.0/PORT=8000
- systemd offercoach.service：enabled + active，Restart=always，journalctl 可查
- MySQL offercoach.question_bank：**497 行**（seed_v1 200 + java_guide_v1 297，直连行数复核一致）

**代码改动（本地回归 289 测试 + 17 快照全绿、ruff/mypy 干净）**：
1. scripts/run_server.py：OFFERCOACH_HOST / OFFERCOACH_PORT / DATABASE_URL 环境变量覆盖，缺省即本地行为（AC1）
2. scripts/import_questions.py：补 sys.path 引导（与 run_server.py 一致，服务器裸 checkout 可直接跑）
3. scripts/deploy/：deploy.py（paramiko，recon/exec/deploy 三子命令，凭据只走环境变量）、create_db.py（MYSQL_PWD 环境变量，只建 offercoach 库）、requirements.txt（运行时依赖按本地版本锁定 + cryptography——PyMySQL 连 MySQL 8 caching_sha2_password 必需）

**冒烟（本地 → 公网真实 HTTP，20/20）**：healthz 200 → 首页 200 → 验证码 SVG 提取 → 注册 201 → 错密码 ×3 全 401 → 触发验证码后登录 200 → chat 会话 + B1 真实 LLM 流式回复（SSE done 帧）→ interview 会话（entry/Java后端）+ judge 流式回复 → 会话列表含两会话 → 登出 → me 401。安全组 8000 已通，无需控制台操作。

**关键决策**：会话/鉴权库继续用 sqlite（data/offercoach_sessions.db，服务器全新开始，不传本地会话库）；MySQL 只作题库库（db/__init__ 设计意图）；建库/导入全走自己 venv 的 pymysql，不 docker exec 进 fin 容器；冒烟脚本用后即删。**遗留**（服务器恢复后加固项，见记忆）：token 无过期、无接口限流；三层记忆/embedding/Langfuse 继续推迟。
