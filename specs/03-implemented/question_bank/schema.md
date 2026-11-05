# 题库 Schema 与导入规范 v1.0

> 状态：已实现（2026-10-21，流转至 03-implemented）。A1 模拟面试的题库数据契约。业务依据：docs/business-scope.md §3-A1。

## 1. 目标与范围

- MVP 种子库（seed_v1）：**200 题，Java 后端**，来源 `ai_seed_v1`（AI 生成种子，无版权风险）
- 二期：开源题库（JavaGuide，Apache-2.0）经 LLM 规范化导入，来源标注 `java_guide`
- 用途：A1 抽题（按岗位/难度/类型/已出题过滤）+ B1 题库检索工具

## 2. 文件布局

- 数据文件：`data/question_bank/seed_v1/{category}.jsonl`，每行一个 JSON 对象（UTF-8，单行）
- 8 个文件对应 8 个 category，每个恰好 25 条

## 3. 字段定义

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| question_id | str | `q_{category}_{seq:03d}`，全库唯一，seq 与 category 一致 | 例 `q_concurrent_012` |
| position | str | 恒为 `java_backend` | 岗位方向 |
| category | str | 枚举见 §4 | 题目类别 |
| difficulty | str | 枚举见 §4 | 难度 |
| type | str | 枚举见 §4 | 题型 |
| question | str | 非空；归一化（去全部空白）后全库唯一 | 题目正文，面试官口吻；场景题含场景铺垫 |
| reference_points | list[str] | 3~5 条，非空串 | 评分要点（A1 评分依据，每条可独立判定） |
| followups | list[str] | 2~3 条，非空串 | 递进追问（追问机制弹药） |
| keywords | list[str] | ≥3 个，非空串 | 检索关键词 |
| source | str | 恒为 `ai_seed_v1` | 来源 |

**不允许出现上表之外的字段。**

## 4. 枚举

- category：`java_basics` / `jvm` / `concurrent` / `mysql` / `redis` / `spring` / `network_os` / `distributed`
- difficulty：`entry`（社招必会基础）/ `medium`（1-3 年高频深挖）/ `hard`（3-5 年大厂深挖、系统设计权衡、综合场景）
- type：`knowledge`（八股）/ `scenario`（场景与项目）/ `coding`（手写）/ `system_design`（系统设计）

## 5. 分布矩阵（seed_v1 契约）

type 与 difficulty 是两个独立维度。type 矩阵如下（每格为该 category 内该 type 的题数）；难度分布每个 category 均为 **entry 5 / medium 12 / hard 8**：

| category | knowledge | scenario | coding | system_design | 合计 |
|---|---|---|---|---|---|
| java_basics | 13 | 8 | 4 | 0 | 25 |
| jvm | 13 | 8 | 4 | 0 | 25 |
| concurrent | 10 | 8 | 7 | 0 | 25 |
| mysql | 10 | 8 | 0 | 7 | 25 |
| redis | 10 | 7 | 0 | 8 | 25 |
| spring | 12 | 13 | 0 | 0 | 25 |
| network_os | 12 | 8 | 5 | 0 | 25 |
| distributed | 0 | 0 | 0 | 25 | 25 |
| **合计** | **80** | **60** | **20** | **40** | **200** |

全局占比对应业务定义：八股 40%（knowledge）/ 场景与项目 40%（scenario + coding）/ 系统设计 20%。

## 6. 验收标准

- **AC1 结构完整**：每行可解析为 JSON 对象；必填字段齐全、类型正确；无未定义字段
- **AC2 ID 契约**：question_id 全库唯一，格式 `q_{category}_{seq:03d}`，category 段与本行 category 一致
- **AC3 枚举合法**：position/category/difficulty/type/source 全部取枚举值
- **AC4 题目唯一**：question 归一化（去全部空白）后全库唯一
- **AC5 列表约束**：reference_points 3~5 条、followups 2~3 条、keywords ≥3 个，且均为非空串
- **AC6 分布契约**：8 个文件各 25 条；type 矩阵逐格一致；每个 category 难度 5/12/8
- **AC7 导入幂等**：按 question_id upsert，重复执行不产生重复行
- **AC8 dry-run**：`--dry-run` 只校验不写库

## 7. 导入契约

`scripts/import_questions.py`（薄 CLI，逻辑在 `app/question_bank/importer.py`）：

- 参数：`--data-dir`（默认 `data/question_bank/seed_v1`）、`--force`（已存在的题覆盖更新，默认跳过）、`--dry-run`
- 流程：加载全部 JSONL → 校验（任一失败即整体退出并列出全部错误，不写库）→ upsert MySQL `question_bank` 表
- 连接：环境变量 `DATABASE_URL`（dry-run 不需要）；测试用 sqlite
- 表结构由 SQLAlchemy 模型创建（`create_all`），模型见 `app/db/__init__.py`
