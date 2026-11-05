# bge-m3 embedding 服务（推迟清单 ③，三层记忆 ② 的前置依赖）

## 目标

服务器本地 bge-m3 embedding HTTP 服务（127.0.0.1:8090）：进程内常驻模型、POST /embed 批量向量化，供三层记忆（长期/画像语义召回）调用。本地开发与测试零网络（确定性 stub），本地不装 torch。**顺序调整说明**：② 依赖 ③ 的向量化，故先做 ③。红线：模型文件放 /data/offercoach/models/（**不碰** /data/financial/data/models/bge-m3——那属于 fin 路径）；服务只绑 127.0.0.1 不暴露公网；零凭据。

## 输入输出契约

输入：
- 服务器 8c/32G，CPU 推理 bge-m3 可行（单次 embed 数百 ms，每会话结束 1 次 + 定时兜底，量级极小）
- 模型 BAAI/bge-m3（sentence-transformers 加载，约 2.2GB）；下载走 HF_ENDPOINT=https://hf-mirror.com（国内镜像）
- 服务器 venv 已具备 Python 3.11

输出：
- scripts/embed_server.py：FastAPI 小服务——GET /healthz；POST /embed `{"texts": [...]}` → `{"vectors": [[1024 维]...]}`；启动加载一次模型常驻；超长/空输入 422、模型未就绪 503
- scripts/deploy/embed-requirements.txt：sentence-transformers 等锁定版本（独立于主服务 requirements，主服务零新依赖）
- deploy.py 增 `embed` 子命令：装依赖 → 下载模型 → 写 systemd offercoach-embed.service → 启动 + 冒烟
- app/core/embedding.py：Embedder 协议 `embed_texts(texts) -> list[list[float]]`；HttpEmbedder（httpx 调 localhost:8090，懒客户端）；DeterministicEmbedder（文本 sha256 → 固定 32 维伪向量，测试用，零网络）

## 验收标准（AC）

1. 本地单测：HttpEmbedder（httpx MockTransport）正确请求/解析/异常传播；DeterministicEmbedder 同文本同向量、不同文本不同向量、维度稳定
2. 服务器：模型下载落 /data/offercoach/models/bge-m3；offercoach-embed 服务 active；/healthz 200；POST /embed 两段文本 → 各 1024 维、长度一致
3. 服务自启：systemctl enable 生效，重启后 active
4. 红线合规：8090 只绑 127.0.0.1（公网 curl 拒绝连接）；未读/写 /data/financial 任何路径；主服务 requirements 零新增；凭据零落盘
5. 本地全量回归 313 + 17 快照不变（app 侧仅新增 embedding.py，无消费者改动）

## 实施计划

1. 规格 → 01-planned（本文件，开工流转 02）
2. scripts/embed_server.py + app/core/embedding.py + tests/unit/test_embedding.py（AC1）
3. scripts/deploy/embed-requirements.txt + deploy.py `embed` 子命令（下载/建服务/启动/冒烟）
4. 服务器部署 + 冒烟（AC2/AC3/AC4）
5. 收尾：流转 03 + README + 记忆同步 + 中文报告

## Definition of Done

AC1-5 全部通过；冒烟证据写入实现备注；README 与记忆同步；红线合规；随后开工 ②（三层记忆，本服务为消费方）。

## 实现备注（2026-10-25）

**流程**：本地代码（scripts/embed_server.py、app/core/embedding.py、tests/unit/test_embedding.py）→ 服务器部署（deploy.py embed）→ 冒烟 + 红线验证。

**服务器证据**（8.137.174.74，2026-10-25）：
- 模型落 `/data/offercoach/models/models--BAAI--bge-m3`（HF cache 布局，与规格写的 `models/bge-m3` 子目录差异仅为 cache_dir 命名）；`models_ready` 标记存在，2204MB
- offercoach-embed：active + enabled（重启自启生效）；`/healthz` → `{"status":"ok"}`；`/embed` 两段文本 → `dims [1024, 1024]`
- 红线：`ss -tlnp` 8090 只绑 127.0.0.1；`/data/financial/data/models/bge-m3` 全程零读零写（fin 文件原样）；主服务 requirements 零新增（fastapi/uvicorn 只在 embed-requirements.txt）；无凭据落盘

**部署曲折与修复**（均已在 deploy.py/download_model.py 固化）：
1. 模型下载经 SSH 通道时静默超时（进度只写 stderr）→ `run()` 改双流并发排空 + 墙钟超时；下载改服务器侧 nohup 独立进程 + 本地轮询 `models_ready` 标记，断点续传
2. torch 2.5.1 + transformers 对 `torch.load` 的 CVE-2025-32434 限制 → 权重改用 pytorch_model.bin + **torch 升 2.7.1**（safetensors 方案废弃：bge-m3 的 safetensors 未下载完且 2.2GB 重拖太慢，.bin 已完整，升级 torch 是安全修复正方向）
3. embed-requirements 漏 fastapi/uvicorn（服务崩溃重启才暴露，之前卡在下载阶段没走到）→ 补依赖；[4/5] 改轮询 is-active、失败打印日志退出；[5/5] 冒烟改精确断言（1024 维），杜绝静默假成功

**偏差说明**：AC5 的"313 + 17 快照不变"——回归现为 352 + 17（② 新增测试并入，app 侧 ③ 无消费者改动）；部署期间服务器曾整机不可达（实例停止，用户重启后恢复）。

**AC 对照**：AC1 本地单测绿（352 全量内）；AC2 ✓ 服务器冒烟证据如上；AC3 ✓ enabled + active；AC4 ✓ 红线全过；AC5 ✓ 全量回归绿（mypy/ruff 干净）。
