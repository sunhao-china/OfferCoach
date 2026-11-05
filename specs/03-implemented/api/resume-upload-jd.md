# 计划：简历文件上传（PDF/docx）与 JD 对照

> 状态：已实现（2026-10-21）。依据：specs/01-planned README 下一批次（简历文件上传 / JD 对照）；架构简化偏好——上传**不落盘不建表**，提取文本直接返回前端走既有 C 一次性管道（无 schema 迁移、无新持久化）；specs/03-implemented/workflows/resume-analysis.md（C 管道既有契约）。

## 目标

给简历页补两件事：**(1) 文件上传**——PDF/docx 服务端提取文本，前端填入输入区直接分析；**(2) JD 对照**——分析时可附目标岗位 JD，analyze/report 提示词 v2 对照 JD 输出匹配点/差距/建议。无 JD 时行为与现状完全一致。

## 输入/输出契约

### 模块一：解析器 `app/api/parsers.py`（新增）

- `extract_text(filename: str, data: bytes) -> str`：按扩展名分发；仅支持 `.pdf`/`.docx`（其余 `ValueError("仅支持 PDF / docx 文件")`）；提取为空或解析失败统一 `ValueError`（调用方转 400）
- `extract_pdf(data: bytes) -> str`：pypdf 逐页 `extract_text()` 拼接
- `extract_docx(data: bytes) -> str`：python-docx 段落 + 表格单元格文本拼接

### 模块二：上传端点（API 增补）

- `POST /api/sessions/{sid}/files`（multipart，字段 `file`）→ `UploadOut {session_id, filename, text}`
- 校验顺序：会话不存在 404；扩展名不支持 400；`>5MB` 413；提取失败/损坏文件 400
- 不持久化：text 返回给前端，由前端触发既有 resume 管道

### 模块三：JD 对照（C 管道增强）

- `MessageIn` 增 `jd_text: str | None`（仅 resume 会话生效，其余 business 忽略）
- `ResumeAnalysisState` 增 `jd_text: str | None`；executor 在 business=resume 时注入 payload
- 提示词 `resume/analyze.v2.yaml`、`resume/report.v2.yaml`：提供 JD 时围绕 JD 分析匹配点/差距/补救建议；未提供时保持 v1 逻辑。节点侧恒传 `jd`（空串走无 JD 分支，registry 模板变量必须齐）
- extract 不变（仍 v1）；v1 可经 `version=1` 回滚（registry 既有能力）

### 模块四：前端（resume 页增强）

- 文件选择控件（`accept=".pdf,.docx"`）+ 上传按钮；上传成功 → 提取文本填入 textarea 并自动触发分析
- JD textarea（可选，占位「目标岗位 JD（可选）」）；发送时随 `jd_text` 提交
- app.js `api()` 对 FormData 跳过 JSON content-type（浏览器自动带 boundary）
- 超长用户消息气泡只显示前 120 字 + 「…（全文已提交）」

### 模块五：依赖

- pyproject 增 `pypdf>=4`、`python-docx>=1.1`、`python-multipart>=0.0.9`（UploadFile 表单解析必需）

## 验收标准

- AC1（提取器）单测：中文 docx 往返；手写最小 PDF（ASCII 文本）往返；坏字节/不支持扩展名 → ValueError
- AC2（上传端点）docx/pdf 上传 200 且文本往返；未知会话 404；非法扩展名 400；超 5MB 413；损坏 docx 400
- AC3（JD 注入）图级：state 带 jd_text 时 analyze/report 渲染提示词含 JD 文本；extract 提示词不含；无 JD 时既有测试全绿（快照更新为传 jd="" 的新版）
- AC4（前端契约）既有契约测试自动覆盖 `/api/sessions/{sid}/files` 路由；首页含文件控件与 JD textarea
- AC5（回归）全量 164 + 新增 全绿；11 快照（resume analyze/report 快照随 v2 更新，新增带 JD 快照）；ruff/mypy 干净
- AC6（真实冒烟）uvicorn + curl multipart 上传生成的中文 docx → 文本提取成功；真实 deepseek 带 JD 分析一轮，报告含匹配/差距表述

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 解析器 + 依赖 | app/api/parsers.py、pyproject | AC1（tests/unit/test_parsers.py） |
| ② 上传端点 | app/api/schemas.py、app.py | AC2（tests/api/test_resume_upload.py） |
| ③ JD 管道 | MessageIn、resume_analysis.py、analyze.v2/report.v2.yaml、executor.py | AC3（test_resume_analysis.py 增补 + 快照） |
| ④ 前端 | index.html、app.js、style.css（微调） | AC4（test_frontend.py 增补） |
| ⑤ 回归 + 冒烟 | — | AC5、AC6 |
| ⑥ 收尾 | 规格流转 03-implemented、索引同步 | — |

## Definition of Done

- [x] AC1-AC4 全部有测试且通过；全量回归（164 + 11 快照基础）全绿
- [x] ruff / mypy（app 全量）干净
- [x] AC6 uvicorn 真实冒烟通过（记录结果）
- [x] 本规格流转 03-implemented，01/02/03 README 同步

## 实现备注（2026-10-21）

- 按计划六步落地，新增 16 个测试（180 = 164 + 16），13 快照（resume analyze/report 升 v2：2 更新 + 2 新增带 JD 版）；ruff/mypy 全清。
- **v2 回滚兼容**：analyze/report v2 成为默认，`registry.render(name, version=1)` 仍可取 v1；节点侧恒传 `jd`（空串走无 JD 分支）——registry 的 `string.Template.substitute` 要求模板变量齐备。
- **analyze JSON 契约新增可选键 `jd_gaps`**（未提供 JD 时为空数组）；旧 v1 回滚路径不受影响（v1 模板无此键，下游解析为普通 dict）。
- **上传不持久化**：`POST /api/sessions/{sid}/files` 提取后直接返回文本，前端填入 textarea 走既有管道——零 schema 迁移、零附件表（架构简化落地）。
- **前端细节**：`api()` 对 FormData 跳过 JSON content-type（浏览器自动带 boundary）；超长简历的用户气泡只显示前 120 字（全文照常提交）；JD 仅随单条消息生效（不落会话状态）。
- **AC6 真实冒烟记录**（scripts/run_server.py + curl）：
  - 生成中文 docx（张三 3 年后端简历）→ `curl -F file=@...` 上传 → 提取文本完整返回；
  - 同消息附 `jd_text`（高级 Java 后端，5 年+高并发分布式）→ 真实 deepseek 报告严格按 v2 结构输出，第 4 节「与 JD 的差距与补救建议」逐条对照（年限不足、缺微服务治理、未体现消息中间件、无集群运维）；
  - 页面/静态资源 200 不受影响。
- **烟测环境坑（Git Bash）**：Windows Python 的 `/tmp` 解析为 `D:\tmp`，Git Bash `/tmp` 是用户临时目录——文件生成与消费必须同侧；管道传给 Python 时必须带 `PYTHONIOENCODING=utf-8`（否则 stdin 按 GBK+surrogateescape 解码出代理字符）。
