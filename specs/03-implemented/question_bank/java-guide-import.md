# 计划：题库扩充二期——JavaGuide 开源题库导入（fetch + extract + LLM 规范化）

## 目标

把题库从 seed_v1 的 200 题扩充到 **~500 题**：抓取 JavaGuide（Apache-2.0，已实证
LICENSE）文档，按 md 标题提取问答题，经 LLM（cheap 档）规范化出评分要点/追问/定档，
导入 dev DB（来源标注 `java_guide`）。一次性投入，成本控制 ~¥1-2。

背景（已核实，2026-10-22）：

- JavaGuide 仓库 **gitee 镜像可克隆**（github 直连 fetch-pack 断流），LICENSE 确认为
  Apache-2.0 → 题面/答案可原文保留，LLM 只补附加值字段（reference_points/followups/
  difficulty/type/keywords），输出量从整题重写砍半以上。
- 题集文档结构：`### ⭐️ 什么是线程和进程？` 问题式标题 + 正文（含嵌套子问题标题），
  8 个既有 category 都有对应文档路径。
- 现有 validator **硬编码 seed_v1 契约**（source 恒 ai_seed_v1、ID 仅 `q_{cat}_{seq:03d}`、
  分布矩阵 AC6）——二期必须扩展，且不破坏既有 14 个 validator 测试。
- 导入管线已具备：`scripts/import_questions.py --data-dir … --dry-run` 校验幂等 upsert。

## 输入输出契约

### 模块一：fetcher（`scripts/fetch_java_guide.py`，薄 CLI）

- clone `https://gitee.com/SnailClimb/JavaGuide` `--depth 1`（github 失败时回退原仓库）
  到 `data/question_bank/vendor/java_guide`；已存在则报错退出（重取 = 手动删目录）。
- clone 后删除 `.git` 目录，写 `MANIFEST.json`（commit SHA、日期）；**校验 LICENSE 文件
  含 Apache 字样**，缺失即失败——防误用非许可源。

### 模块二：extractor（`app/question_bank/md_extract.py`）

- `CATEGORY_RULES`：路径片段 → category 的有序规则表（首个命中生效），跳过 README.md：
  - `docs/java/basis/`、`docs/java/collection/` → java_basics
  - `docs/java/concurrent/` → concurrent；`docs/java/jvm/` → jvm
  - `docs/database/mysql/` → mysql；`docs/database/redis/` → redis
  - `docs/system-design/framework/spring/` → spring
  - `docs/cs-basics/network/`、`docs/cs-basics/operating-system/` → network_os
  - `docs/distributed-system/`、`docs/high-availability/`、`docs/high-performance/` → distributed
- `extract_candidates(root) -> list[RawQuestion(category, question, answer)]`（纯函数，确定性：
  文件按路径排序、题按文档序）：
  - 标题行 `^(#{2,4})\s+(.+)`；清理 emoji（⭐️）、markdown 链接/反引号；
  - 题面：清理后以 `？` 结尾，长度 6~100；
  - 正文：到同级或更高级标题为止（嵌套子题成为独立候选，父题正文包含子题），
    剔除 fenced code block、图片，链接语法转纯文本；
  - 过滤：正文 100~2500 字（太少没内容、太长是整章）。
- 候选内去重：题面归一化（去全部空白）重复者只留第一个。

### 模块三：normalizer（`app/question_bank/normalize.py`）

- `normalize_candidates(llm, candidates, existing_ids, existing_questions, cap, output_dir) -> report`：
  - `question_id = q_{category}_g{md5(norm(category|题面))[:8]}`（确定性，重跑幂等）；
  - **resume**：id 已在库 → 跳过；**去重**：题面归一化命中 seed 题 → 跳过；cap 每类
    默认 40（选够即停，LLM 零浪费）；
  - LLM（cheap 档，JSON）：提示词模板 `app/prompts/templates/question_bank/normalize.md`
    输入 {{question}}/{{answer}}，输出 JSON `{question, reference_points, followups,
    difficulty, type, keywords}`；题面保留原意可微调（去序号/emoji），reference_points
    3~5 条可独立判分，followups 2~3 条递进追问，含场景/权衡内容优先定 scenario/
    system_design；
  - 输出校验：字段/枚举/条数不合法 → 记入失败列表不写文件（不重试）；
  - 每类一个 JSONL（8 个文件齐全，空类空文件），逐条 append + flush（崩溃安全）；
  - report：各类条数、失败数、输入/输出字符统计（成本估算 ~1 字 1 token）。
- 提示词变量齐备（坑 9）：模板只声明 question/answer 两个变量。

### 模块四：validator 扩展（`app/question_bank/validator.py`）

- `SOURCES = {"ai_seed_v1", "java_guide"}`；source 校验改为成员判断（错误信息保留原文案风格）；
- ID 契约分源：seed 仍 `q_{cat}_\d{3}`；java_guide 为 `q_{cat}_g[0-9a-f]{8}`；category
  段一致校验两式共用；
- `validate_distribution`（AC6）只对 `source == ai_seed_v1` 的记录生效——java_guide
  批次按题量自由分布（cap 由 normalizer 保证）；
- 既有 14 个 validator 测试与 seed_v1 快照不破。

### 模块五：CLI（`scripts/build_java_guide_bank.py`）

- 流程：校验 vendor（提示先 fetch）→ extract → 去重 → 读库取 existing（ids + 题面）→
  normalize（cap/limit 参数）→ `run_import --dry-run` 校验 → `--import` 时真实导入。
- 参数：`--cap`（默认 40/类）、`--limit`（试跑前 N 题）、`--import`（默认只 dry-run）、
  `--data-dir`、DATABASE_URL 同既有约定（dev 用 `sqlite:///data/offercoach_dev.db`）。

## 验收标准（AC）

1. **AC1（fetch 合规）**：clone 成功、LICENSE 校验通过、MANIFEST 落盘；github 回退路径
   单测覆盖（桩）。
2. **AC2（extract 契约）**：夹具 md → 候选的题面过滤/层级边界/代码块剔除/分类规则/长度
   过滤/确定性顺序/候选内去重全部按契约。
3. **AC3（normalize 契约）**：桩 LLM 下——字段齐全且枚举合法、ID 确定性（同输入同 ID）、
   resume 跳过已入库 ID、题面归一化去重命中 seed、cap 生效（选够即停、不超发）、非法
   LLM 输出进失败列表不写文件。
4. **AC4（validator 扩展）**：java_guide 记录通过 validate_records；非法 source/ID 拒绝；
   分布校验只约束 seed 记录；既有 seed 契约测试与快照全绿。
5. **AC5（导入闭环）**：build CLI（桩 LLM）产出 JSONL → `run_import` 校验通过、幂等
   重跑零新增、DB 中 source=java_guide 计数正确。
6. **AC6（回归）**：全量 217 测试 + 13 快照全绿；ruff/mypy 干净。
7. **AC7（真实执行）**：fetch 真实仓库 → extract → `--limit 20` 真实 deepseek 试跑，
   记录字符/成本估算 → 全量跑完（cap 40/类）→ dry-run + 导入 dev DB → 报告各类条数
   与总额（目标总量 ~500，各专题均有增量）。

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 计划落 01-planned（本规格） | specs/01-planned/question_bank/java-guide-import.md | — |
| ② fetcher | app/question_bank/fetcher.py + scripts/fetch_java_guide.py | AC1（桩 git 命令） |
| ③ extractor | app/question_bank/md_extract.py | AC2（新 tests/unit/test_md_extract.py） |
| ④ normalizer + 提示词 | app/question_bank/normalize.py + prompts/templates/question_bank/normalize.md | AC3（新 tests/unit/test_normalize.py） |
| ⑤ validator 扩展 | app/question_bank/validator.py | AC4（test_question_bank_validator.py 增补） |
| ⑥ build CLI | scripts/build_java_guide_bank.py | AC5（新 tests/unit/test_build_java_guide_bank.py，桩 LLM + sqlite） |
| ⑦ 真实执行（试跑 → 全量） | — | AC7 |
| ⑧ 收尾 | 流转 03 + 实现备注 + README/记忆同步 + 报告 | AC6 |

## Definition of Done

- AC1-AC5 全部有测试且通过；全量回归（217 测试 + 13 快照）全绿。
- ruff / mypy 干净。
- AC7 真实执行完成：试跑成本记录 + 全量导入 dev DB + 条数报告。
- 本规格流转 03-implemented，01/02/03 README 与记忆同步。

## 实现备注（2026-10-22）

**验收结果**：AC1-AC7 全绿。全量 **249 测试 + 13 快照**（新增 32：md_extract 7、
normalize 11、fetcher 6、validator 增补 5、build 闭环 3）通过；ruff/mypy 干净。

**关键实现点**：

- validator 扩展保持既有 14 测试零改动：`SOURCES` 二元组 + 双 ID 正则（seed 序号 /
  guide 哈希 8 位）+ source 成员判断；`validate_distribution` 只统计 ai_seed_v1 记录，
  **且无 seed 记录的 category 整体跳过**——guide 批次单独成目录导入时才不会因
  "题量 0 ≠ 25" 报错（真实执行中暴露并修复）。
- md 提取的层级语义用 flush 闭包收口：同级/更高级标题收口当前题；**更深的子问题先
  收口父题**再独立成候选；更深的非问题小标题并入正文。题面 emoji（⭐️）剔除在
  `_clean_heading` 最前，避免把章节标题误当题面。
- fetcher：gitee 镜像优先（github 直连 fetch-pack 断流已实证），LICENSE 缺失文件与
  无 Apache 字样两种失败都清理目录后报错。
- normalizer：ID 由 category+题面 md5 前 8 位派生，LLM 输出波动不影响重跑幂等；
  resume 覆盖入库 + 已写盘两层，重跑零 LLM 调用。
- **cap 累计修复（真实执行暴露）**：初版 cap 预算每次运行重置，补跑失败题时被上一轮
  超 cap 跳过的候选重新吃满 40，导致 6 类翻倍（network_os 达 100）。修复为
  `budget = cap − 已写盘条数`，补测试锁定（重跑仍被累计 cap 拦住、零 LLM 调用），
  数据回滚后重导修复。
- 提示词落地为 registry 格式 `normalize.v1.yaml`（规格初稿写 normalize.md，实现时
  沿用既有 PromptRegistry 约定）；CLI 参数为 `--vendor-dir/--output-dir`
  （规格初稿写 `--data-dir`）。

**真实执行记录**（AC7）：

- fetch：gitee @ commit `50f02a5`，MANIFEST 落盘，LICENSE Apache-2.0 校验通过。
- extract：**843 条候选**（network_os 233 / distributed 190 / concurrent 124 /
  java_basics 114 / redis 75 / mysql 50 / spring 35 / jvm 22）。
- 试跑 20 题（真实 deepseek）：输入 23,097 字 / 输出 13,138 字，20 条通过校验，
  1 条失败（reference_points 条数不合规，按契约不重试——全量跑时被 resume 自然重试成功）。
- 全量首跑：新增 293 + 失败 23（多为 reference_points 条数出界，1 条非 JSON），
  字符量 399,105 入 / 193,380 出（≈59 万字符 ≈ 50 万 token ≈ **~¥2**）。
- cap 修复后数据回滚：JSONL 截断至每类 40，DB 删 521 行 java_guide，重导 297 条，
  重跑零 LLM 调用（累计 cap 生效的实证）。
- **最终 dev DB：497 题**（ai_seed_v1 200 + java_guide 297），分类分布 65×6 + 60 + 47，
  各专题均有增量，达成 ~500 目标。
- 总成本：含 bug 导致的补跑合计约 100 万字符 ≈ **¥3-4**（正常路径 ~¥2，符合规格预算）。

**实现清单**：`app/question_bank/fetcher.py`、`md_extract.py`、`normalize.py`、
`build_bank.py`、`validator.py`（扩展）、`app/prompts/templates/question_bank/
normalize.v1.yaml`、`scripts/fetch_java_guide.py`、`scripts/build_java_guide_bank.py`、
测试 4 新 + 1 增补。未动：seed_v1 管线、importer、store、既有 seed 契约与快照。
