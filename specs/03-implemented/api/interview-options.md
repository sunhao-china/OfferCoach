# 计划：面试难度与岗位显式选择

> 状态：已实现（2026-10-22）。依据：specs/01-planned README 下一批次（面试难度/岗位显式选择）；现状——直连面试默认 medium、岗位默认 java_backend（`normalize_difficulty(None)` 兜底 / `ask` 抽题无匹配时回退默认岗位），用户无法选择；架构简化偏好——难度/岗位属于**会话属性**（一轮面试一个难度），落 SessionCreate + chat_sessions 两列，消息契约不动。

## 目标

创建面试会话时可显式指定难度（entry/medium/hard）与目标岗位：executor 在 interview 直连时注入 `initial_difficulty` / `position`（子图既有 state 字段，opening 消费难度、ask 按岗位抽题）；未指定时行为与现状完全一致（medium + 默认岗位）。前端面试页加难度下拉与岗位输入，新建会话时随 SessionCreate 提交。

## 输入/输出契约

### 模块一：Schema 与存储

- `SessionCreate` 增 `difficulty: Literal["entry", "medium", "hard"] | None = None`、`position: str | None = None`（仅 interview 生效，其余 business 忽略——与 jd_text 同款约定；非法难度由 Literal 422）
- `ChatSession` 增列 `difficulty`（String(16)）、`position`（String(64)），均可空
- **迁移**：`SessionStore.__init__` 在 `create_all` 后 inspect 已有列，缺失则 `ALTER TABLE ADD COLUMN`（老 dev db 无迁移脚本，create_all 不增量，5 行内解决；列名/类型为常量无注入面）
- `SessionOut` 回显两字段；`SessionStore.create` 增两形参

### 模块二：executor 注入

- `invoke_business`：`business == "interview"` 且值非 None 时 payload 注入 `initial_difficulty` / `position`（每轮同值注入无害：opening 只跑一轮，state 持久化）
- 规范值 `entry/medium/hard` 经 `normalize_difficulty` 透传（`DIFFICULTY_SLOT_MAP` 已含规范值透传分支）；岗位无匹配题时子图 `ask` 自动回退默认岗位（既有行为）

### 模块三：前端（interview 页）

- 难度下拉（入门/中等/困难，默认中等）+ 岗位输入（可选）
- localStorage `oc_interview_opts` 记住上次选择；新建面试会话时随 SessionCreate 提交；已有会话不受影响（会话属性在创建时锁定）

## 验收标准

- AC1（落库与回显）创建 interview 会话带 difficulty/position → SessionOut 与 GET 回显一致；非法难度 422
- AC2（注入生效）entry 难度会话直连首答含「（入门）」标签；分类器零调用
- AC3（迁移）老 schema 的 chat_sessions 表经 SessionStore 初始化后出现新列，老表读写正常
- AC4（前端 DOM）首页含难度下拉与岗位输入控件
- AC5（回归）全量 190 + 13 快照全绿；ruff/mypy 干净
- AC6（真实冒烟）真实 deepseek：entry + 岗位创建面试会话，首答含「（入门）」，与现状 medium 会话对照

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① Schema + 存储 + 迁移 | schemas.py、db/__init__.py、session_store.py、app.py | AC1/AC3（tests/api/test_web_api.py 增补、tests/api/test_session_store.py 新增） |
| ② executor 注入 | executor.py | AC2 |
| ③ 前端 | index.html、app.js、style.css | AC4（test_frontend.py 增补） |
| ④ 回归 + 冒烟 | — | AC5、AC6 |
| ⑤ 收尾 | 规格流转 03-implemented、索引同步 | — |

## Definition of Done

- [x] AC1-AC4 全部有测试且通过；全量回归（190 + 13 快照基础）全绿
- [x] ruff / mypy（app 全量）干净
- [x] AC6 uvicorn 真实冒烟通过（记录结果）
- [x] 本规格流转 03-implemented，01/02/03 README 同步

## 实现备注（2026-10-22）

- 按计划五步落地，新增 4 个测试（194 = 190 + 4：选项回显 1 + 难度生效 1 + 迁移 2），13 快照不变；ruff/mypy 全清。
- **迁移方案**：`create_all` 不增量，`SessionStore.__init__` 在 create_all 后 inspect 已有列、缺失则 `ALTER TABLE ADD COLUMN`（幂等，列名/类型常量）。真实 dev 库（data/offercoach_sessions.db，老 schema）在冒烟启动时完成补列，无需删库。
- **难度取值**：schema `Literal["entry","medium","hard"]` 与图内 `DIFFICULTY_ORDER` 一致；`normalize_difficulty` 的 `DIFFICULTY_SLOT_MAP` 已含规范值透传分支（entry/medium/hard 原样保留）。
- **注入语义**：`initial_difficulty` 只在 opening 消费（第一轮），后续轮次同值注入无害（state 经 checkpointer 持久化）；`position` 每轮注入同值，ask 抽题无匹配时回退默认岗位（子图既有行为，岗位口径 java_backend 与用户自由文本不一致时不会误判耗尽）。
- **前端**：难度/岗位存 localStorage `oc_interview_opts`（新建会话时提交并保存，刷新后恢复）；已创建的会话属性在创建时锁定，改选项需「新建面试」。
- **AC6 真实冒烟记录**：entry + 「Java 后端」→ 首答「第 1 题（入门）：Spring Bean 的作用域有哪些…」；对照默认会话 → 「第 1 题（中等）：长事务锁等待诊断…」；页面 200。
