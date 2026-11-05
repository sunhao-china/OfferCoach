# 计划：SSE 流式输出（终稿分块推送）

> 状态：已实现（2026-10-21）。依据：specs/01-planned README 下一批次；架构简化偏好——**不做 langgraph token 级流式**（`stream_mode="messages"` 会把 mock_interview 的 judge/score 等内部消息一并推出，前端需按节点过滤，复杂度高且内部输出不该暴露），MVP 采用**终稿分块 SSE**：LLM 计算仍一次性完成，最终回复文本按块以 `text/event-stream` 推送，前端逐块渲染；真实 token 流式后置。

## 目标

消息端点支持流式返回：前端发消息即见逐块浮现的回复（长报告体验从「等整篇」变「边出边读」），会话/历史/路由逻辑零改动。默认（`stream=false`）行为与现有 JSON 契约完全一致，既有测试不受影响。

## 输入/输出契约

### 模块一：SSE 工具 `app/api/sse.py`（新增）

- `CHUNK_SIZE = 30`（字符粒度，str 切片天然按字符，无字节截断问题）
- `chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]`：空串 → `[]`；非空串按 size 切块，块非空
- `sse_data(payload: dict) -> str`：`json.dumps(ensure_ascii=False)` 包成 `data: {json}\n\n`

### 模块二：消息端点流式分支（API 增补）

- `MessageIn` 增 `stream: bool = False`
- `POST /api/sessions/{sid}/messages`：`stream=true` 时返回 `StreamingResponse`（`text/event-stream`，`Cache-Control: no-cache`）
  - 事件序列：`data: {"content": "块1"}` … `data: {"done": true, "session_id": sid}`（每行一条 SSE data）
  - 回复文本仍由 `invoke_business` 一次算出（生成器内惰性计算），分块推送；**服务端不加人为延迟**（渲染节奏由前端控制，测试保持快速）
  - 404 校验在流式分支前，未知会话照样 404
- `stream=false`（默认）：行为与现在完全一致（JSON `MessageOut`）

### 模块三：前端流式渲染

- app.js `sendMessage` 改走 `stream=true` 的 fetch 流读取：`response.body.getReader()` + `TextDecoder(stream)`，按 `\n\n` 分帧解析 `data:` JSON；`{content}` 追加到最后一条助手气泡，`{done}` 收尾
- 逐块之间 `setTimeout(~30ms)` 帧节奏，让长报告可见地逐块浮现（仅影响观感，不阻塞交互）
- 全四页业务统一走流式；出错时 toast（与现有错误处理一致）

### 模块四：测试

- L1 `tests/unit/test_sse.py`：chunk_text（空串/短于 size/恰为 size 整数倍/中文多字）、sse_data 格式
- L3 `tests/api/test_stream.py`：
  - chat/interview/resume（含 jd_text）三类会话 `stream=true`：content-type 为 `text/event-stream`；逐行解析 data JSON 后 content 拼接 == 预期回复；末条 `{"done": true}` 且 session_id 正确；每块长度 ≤ CHUNK_SIZE 且非空
  - `stream=false` 显式 JSON 返回（content-type application/json）；未知会话流式请求仍 404

## 验收标准

- AC1（SSE 工具）L1 单测全绿
- AC2（流式端点）三类业务流式拼接 == 一次性回复；done 事件与 content-type 正确；404 不受影响
- AC3（默认兼容）stream=false / 不传 stream 的既有 25 个 API 测试全绿（零改动）
- AC4（回归）全量 180 + 13 快照基础全绿；ruff/mypy 干净
- AC5（真实冒烟）uvicorn + `curl -N` 真实 deepseek 流式轮：SSE 行可解析、拼接为完整回复、历史接口仍读回双方消息；页面 200

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① SSE 工具 | app/api/sse.py | AC1（tests/unit/test_sse.py） |
| ② 端点流式分支 | app/api/schemas.py、app.py | AC2/AC3（tests/api/test_stream.py） |
| ③ 前端流式渲染 | app/web/app.js | —（契约测试自动覆盖不变） |
| ④ 回归 + 冒烟 | — | AC4、AC5 |
| ⑤ 收尾 | 规格流转 03-implemented、索引同步 | — |

## Definition of Done

- [x] AC1-AC3 全部有测试且通过；全量回归（180 + 13 快照基础）全绿
- [x] ruff / mypy（app 全量）干净
- [x] AC5 uvicorn 真实冒烟通过（记录结果）
- [x] 本规格流转 03-implemented，01/02/03 README 同步

## 实现备注（2026-10-21）

- 按计划五步落地，新增 10 个测试（190 = 180 + 10：L1 工具 5 + L3 流式 5），13 快照不变；既有 25 个 API 测试零改动全绿（默认 stream=false 兼容验证）。ruff/mypy 全清。
- **FastAPI 联合返回注解坑**：`-> MessageOut | StreamingResponse` 会被 FastAPI 当作响应模型推断并抛 FastAPIError——需在装饰器显式 `response_model=None`（返回时 pydantic 模型走 jsonable_encoder，StreamingResponse 作为 Response 直传）。
- **服务端不加人为延迟**：块渲染节奏由前端 `setTimeout(30ms)` 控制（仅观感），测试与冒烟保持快速。
- **前端流式解析**：fetch `response.body.getReader()` + `TextDecoder(stream)`，按 `\n\n` 分帧解析 `data:` JSON；`{content}` 追加到最后一条助手气泡（`appendToLastMessage`），`{done}` 随流结束自然收尾。全四页统一走流式。
- **AC5 真实冒烟记录**（uvicorn + `curl -N`）：真实 deepseek chat 轮「什么是索引覆盖」→ 95 字回复分 4 块（CHUNK_SIZE=30），done 帧 session_id 正确；流式消息照常落 checkpointer（历史接口读回完整双方消息）；页面/静态资源 200。
- **烟测环境坑（沿用）**：curl 输出与 Python 解析必须同侧路径（Git Bash /tmp vs D:\tmp），管道进 Python 带 PYTHONIOENCODING=utf-8。
