# 计划：题库查询层（QuestionStore）

> 状态：01-planned（2026-10-21）。依据：specs/03-implemented/question_bank/schema.md（数据契约）。

## 目标

A1 抽题与 B1 题库检索共用的查询层。SQLAlchemy 实现，生产 MySQL / 测试 sqlite 同构（依赖注入 engine，不绑具体方言）。

## 输入/输出契约

- 模块：`app/question_bank/store.py`
- `QuestionStore(engine: Engine)`
  - `search(*, position="java_backend", category=None, difficulty=None, qtype=None, keywords=None, exclude_ids=(), limit=20) -> list[Question]`
    - 过滤按 AND 组合；`keywords` 为 str 或 list[str]，对 question 文本做 LIKE OR 匹配
    - 排序：category, question_id（稳定、可测）
  - `get(question_id: str) -> Question | None`
  - `random_pick(*, position="java_backend", category=None, difficulty=None, qtype=None, exclude_ids=()) -> Question | None`
    - 同 search 过滤 + `func.random()` 排序取 1（SQLAlchemy 自动方言编译：MySQL RAND() / SQLite RANDOM()）
    - `exclude_ids` 空时不加 NOT IN 条件

## 验收标准

- AC1 单条件过滤生效（category / difficulty / qtype 各一例）
- AC2 组合过滤生效（category + difficulty）
- AC3 keywords 命中 question 文本（单个与多个关键词 OR）
- AC4 exclude_ids 排除已出题（排除 1 题后 random_pick 不再返回该题）
- AC5 无匹配 → search 返回 []，random_pick 返回 None
- AC6 get 命中返回对应题目、未命中返回 None

## 实施计划

| 文件 | 动作 |
|---|---|
| `app/question_bank/store.py` | 新建 |
| `tests/unit/test_question_bank_store.py` | 新建：sqlite 临时库 + `run_import` 导入种子库（复用 tests/support/question_bank.py） |

## Definition of Done

- [ ] AC1~AC6 全绿
- [ ] ruff + mypy 干净
