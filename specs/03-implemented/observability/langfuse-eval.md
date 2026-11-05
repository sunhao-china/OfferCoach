# Langfuse 评估体系：测试集 + 提示词管理 + LLM-as-judge 评分（正式测试）

## 目标

在现有 Langfuse 追踪（每请求 trace + metadata）之上，建立正式的评估闭环：
1. **提示词管理**：把本地 YAML 模板（PromptRegistry 资产）推入 Langfuse Prompt Management（`offercoach/{目录}/{名称}`），本地各版本（v1/v2）成为 Langfuse 提示词版本，可被评估实验按版本"替换"对比；
2. **测试集**：三个 dataset——意图识别（复用 data/evals/intent.jsonl 标注集）、面试 judge 判定（题库题目+要点+模拟回答→期望 action）、B1 知识问答（题库抽题→reference_points 为期望要点）；
3. **评估运行器**：对 dataset 逐条跑真实 LLM（cheap 档），产出写回 Langfuse run（trace + dataset_item 关联），确定性打分（意图精确匹配 / judge action 匹配 / JSON 合法性）+ B1 用 LLM-as-judge 评要点覆盖度与相关性；
4. **提示词替换实验**：同一 dataset 以不同提示词版本跑实验，分数落云端对比（如 intent.v1 vs intent.v2、judge 新版候选）。

成本：全部 cheap 档，首轮 ~35k token（¥0.1-0.2）。生产运行仍用本地 YAML（Langfuse 提示词仅作评估/实验源，不引入运行时依赖云端）。

## 输入输出契约

输入：
- Langfuse SDK 5.0.4（已有依赖）；凭据 LANGFUSE_SECRET_KEY/PUBLIC_KEY/BASE_URL 从环境读（不入仓库）
- app/prompts/templates/**/*.yaml（16 个模板，name+version+template 字段，占位符 `$var` 格式）
- data/evals/intent.jsonl（15 条：id/message/expected_intent）
- 题库 dev DB（sqlite:///data/offercoach_dev.db，question_id/position/category/difficulty/question/reference_points）
- llm_factory TIER_CHEAP（deepseek-chat）

输出（app/observability/ 新模块 + scripts/ thin CLI + data/evals/ 新标注集）：
- `langfuse_prompts.py`：push/list/diff——模板名映射 `offercoach/{subdir}/{stem}`（如 offercoach/react/b1）；占位符转换 `$var`→`{{var}}`（推送）、`{{var}}`→`$var`（拉取）；每次 push 内容变更 → 新 Langfuse 版本，config 记 source_version（本地 v1/v2）与内容 sha256；无变更跳过；`--label latest` 标最新版
- `langfuse_datasets.py`：三个 dataset 构建器（数据源读取 + 幂等推送）：
  - `oc-intent-v1`：item input={message}，expected_output={intent}
  - `oc-judge-v1`：data/evals/judge.jsonl 新增标注集（约 20 条：input=judge 模板全变量 {question, reference_points, bank_followups, answer, followups, followup_count, max_followups}，expected_output={action}）
  - `oc-b1-qa-v1`：从题库抽样（每 category 按难度均衡、全岗位，合计 ~40 条）：input={question, position}，expected_output=reference_points
  - 幂等：拉取已有 dataset 的 item id 集合，只补缺失（item id = 确定性 slug）；`--all` 构建三个
- `langfuse_eval.py`：实验运行器——`--dataset` + `--prompt`（Langfuse 提示词名，`--prompt-version N` 指定版本=提示词替换；`--prompt-local` 用本地 YAML 兜底）+ `--task {intent,judge,b1_qa}`；逐条：取提示词→渲染→cheap LLM→解析→打分→写回 Langfuse（trace 挂 dataset_item_id + 实验名，逐条 score）；评分器：
  - intent：复用 RouteDispatcher 解析逻辑，命中 1/0 + 解析合法性 1/0
  - judge：action 与期望一致 1/0 + JSON 合法 1/0
  - b1_qa：LLM-as-judge（evaluation/b1_quality.v1.yaml 新模板）评 reference_points 覆盖 0~5 + 相关性 0~2；judge 用 cheap 档
- scripts/：`langfuse_prompts.py`、`langfuse_datasets.py`、`langfuse_eval.py`（thin CLI，环境无凭据快速失败）
- 依赖注入：全部核心逻辑接受 Langfuse 客户端协议（测试用 FakeLangfuse 桩，零网络）

## 验收标准（AC）

1. 占位符转换单测：`$var`↔`{{var}}` 往返一致；含多个变量/中文文本正确
2. 提示词推送单测（FakeLangfuse）：名称映射正确（含子目录）；内容变更→新版本+source_version 记录；无变更跳过零调用；latest 标签
3. dataset 构建单测：三个数据源加载正确（intent 15 条 / judge 标注集字段完整 / 题库抽样确定性、难度与岗位均衡）；幂等补推只补缺失 id
4. 运行器单测（StubLLM + FakeLangfuse）：intent/judge 确定性打分正确（含解析失败 0 分）；b1_qa LLM-as-judge 分数解析与钳制；trace/score 调用参数正确（dataset_item_id、实验名）
5. 全量回归：既有 393 测试 + 18 快照全绿（新增测试并入）；ruff/mypy 干净
6. 真实云端验收：提示词全部推入（版本可查）；三个 dataset 建立（item 数符合）；三个实验真实跑完，Langfuse 云端可见 run + 分数；`--prompt-version` 替换跑出第二个实验（如 intent v1 vs v2）分数对比可见

## 实施计划

1. 规格 → 01-planned（本文件，开工流转 02）
2. langfuse_prompts.py（占位符转换 + 推送/列表/diff）+ 单测（AC1/AC2）
3. langfuse_datasets.py（三数据源 + 幂等推送）+ judge 标注集 + 单测（AC3）
4. langfuse_eval.py（三 task + 打分 + 写回）+ b1_quality.v1.yaml + 单测（AC4）
5. ruff/mypy + 全量回归（AC5）
6. 真实云端执行：推提示词 → 建 dataset → 跑三实验 → 版本替换对比实验（AC6）
7. 收尾：流转 03 + README + 记忆同步 + 中文报告

## Definition of Done

AC1-6 全部通过；云端证据（提示词版本、dataset 条数、实验分数）写入实现备注；凭据未入仓库；生产运行路径不变（本地 YAML）。

## 实现备注

**代码**：app/observability/ 四模块 + scripts/ 三个 thin CLI + data/evals/judge.jsonl（20 条标注）+ app/prompts/templates/evaluation/b1_quality.v1.yaml：

- **langfuse_prompts.py**：to/from_langfuse_template（`$var`↔`{{var}}`，非标识符 `$` 原样保留——24 个生产模板往返零损失已验证）；template_sha256；push_all_prompts（每版本打标签 `sv{source_version}`，最高版本加 `latest`；get_prompt(label=svN) 比对 config.sha256 无变更跳过，幂等重跑零写入）；diff_prompts（missing/same/changed）；config 记 source_version/sha256/template_path（as_posix 正斜杠）
- **langfuse_datasets.py**：load_intent_items（复用 app.evaluation.intent 加载器）/load_judge_items（9 字段必填校验，缺字段抛错）/load_b1_qa_items（**确定性抽样**：每岗位至少 1 条 + 剩余名额按岗位题量最大余数法，岗位内按 category×difficulty 组同法配额，组内跨步取样，无随机）；sync_dataset 幂等（item id = input+expected 规范化 JSON md5 前 12 位；**SDK create_dataset 返回 API Dataset（无 .items），必须再 get_dataset 拿 DatasetClient**）
- **langfuse_eval.py**：langfuse_renderer（version=N 或 label=latest，取一次复用 compile）与 local_renderer（registry.render，生产同源）；三任务 make_intent_task（复用 parse_intent_json，失败→{"intent": None}）/make_judge_task（judge 模板全变量渲染、列表转编号文本、解析失败→{"json_valid": False}）/make_b1_qa_task（内联作答指令）；打分器 intent_evaluator（intent_match + json_valid）/judge_evaluator（action_match + json_valid）/make_b1_quality_evaluator（本地 b1_quality 模板 LLM-as-judge，coverage 0-5 / relevance 0-2 钳制，judge 失败 0 分）；run_experiment 组装后调 dataset.run_experiment（SDK 负责逐条 trace+dataset_item 挂接与分数写回）
- **测试**：FakeLangfuse 桩（tests/support/langfuse.py：FakePrompt.compile mustache、FakeDataset.run_experiment 模拟 SDK 逐条执行语义）；单测 37 个（AC1 转换 4 / AC2 推送 8 / AC3 数据源 10 / AC4 运行器 15）；全量 **430 测试 + 18 快照**、ruff/mypy 干净（AC5）

**云端验收证据（AC6，2026-10-25）**：

- 提示词：24 个版本（16 名）全部推入 Langfuse（`offercoach/{name}`，每版本 sv{N} 标签 + 最高版 latest）；重推 0 新建 / 24 跳过（幂等验证）
- 测试集：oc-intent-v1 15 条 / oc-judge-v1 20 条 / oc-b1-qa-v1 40 条（dev DB 抽样）；重跑 0 推送 / 15 跳过
- 实验（cheap 档真实跑完；分数从云端 scores API 按 trace_id 聚合）：

| 实验 | 条数 | 分数 |
|---|---|---|
| oc-intent-v1 · router/intent v2（latest） | 15 | intent_match 1.0 / json_valid 1.0 |
| oc-intent-v1 · router/intent v1（替换） | 15 | intent_match 1.0 / json_valid 1.0 |
| oc-judge-v1 · mock_interview/judge v2 | 20 | action_match 0.55 / json_valid 1.0 |
| oc-b1-qa-v1 · 内联作答 + b1_quality judge | 40 | coverage 4.525 / relevance 1.975 |

- **提示词替换实验**：同名实验 oc-intent-v1 下两个 run（intent v1/v2）云端可对比；本例两版均满分（15 例区分度不足），替换基础设施已验证

**成本与红线**：实验全部 cheap 档（约 4 万 token ≈ ¥0.2）；生产运行路径不变（本地 YAML 仍为运行时唯一提示词来源，Langfuse 提示词仅作评估/实验源）；凭据仅经环境变量传入（LANGFUSE_SECRET_KEY/PUBLIC_KEY），零仓库落盘。

**遗留**：judge action_match 0.55 为基线（11/20 全对；错例多为标注预期与模型合理判断分歧，如标注 move_on 而模型认为仍有追问空间），后续提示词迭代可用同名实验跑新 run 直接对比；SDK 5.0.4 的 ExperimentResult.dataset_run_id/dataset_run_url 已弃用（改用 experiment_id/experiment_url），脚本兼容两代字段。
