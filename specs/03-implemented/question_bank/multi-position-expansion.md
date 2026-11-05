# 题库扩充三期：多岗位题库（Python / C / C++ / C# / AI Agent / 项目经理）

## 目标

现题库 497 题全部 `position=java_backend`（seed_v1 200 + java_guide 297）。三期新增 6 个岗位各 **160 题起步**（AI 生成、cheap 档、`source=ai_seed_v3`，无版权风险）：python_backend / c_backend / cpp_backend / csharp_backend / ai_agent / project_manager，总量 ~1500 题。同时打通「口语岗位输入 → 库内 position」的别名映射（现状"Java 后端"≠"java_backend" 靠回退 java_backend 兜底），并让 B1 题库检索跨岗位（默认 position=None 全岗位，不再锁 java_backend）。

## 输入输出契约

输入：
- Question 表：position/category 列已是自由字符串（String(32)，有索引），无需迁移
- validator 现状：CATEGORIES 8 个枚举、POSITION 单值常量、SOURCES 两枚举、ID 正则 `q_{category}_{seq:03d}` / `q_{category}_g{hash8}`；validate_bank 按全局 CATEGORIES 要求每类文件齐全（seed_v1 完整性契约，不适用于 v3 分岗位目录）
- import_questions.py --data-dir 目录导入；deploy.py [5/8] 目前只导 seed_v1 + java_guide_v1
- B1 检索工具默认 `position=java_backend`；mock_interview ask() 用原始用户输入查 position、无匹配回退 DEFAULT_POSITION
- llm_factory TIER_CHEAP + PromptRegistry（生成走 cheap 档，成本 ~1200 题 × 350 token ≈ ¥1-2）

输出：
- validator：POSITIONS 7 枚举（含 java_backend）、CATEGORIES 增 v3 各类、SOURCES 增 ai_seed_v3、position 校验改 in POSITIONS；validate_bank 改为按目录内实际 *.jsonl 文件迭代（文件名 stem 必须在 CATEGORIES 内），移除"缺文件"全局检查（seed 完整性仍由 validate_distribution 按 source 过滤保证）
- store.py：`normalize_position(raw) -> str`——小写、去空白、别名表（"python"/"py"/"python 后端"→python_backend、"c++"/"cpp"→cpp_backend、"c#"/"csharp"→csharp_backend、"ai agent"/"agent"/"ai"→ai_agent、"项目经理"/"pm"→project_manager、java 系列→java_backend），未命中返回原文
- mock_interview：ask() 与 opening 用 normalize_position（opening 的 LLM 展示仍用原始输入文本）
- B1 search_question_bank 工具：position 参数改为可省略（None=全岗位检索）
- prompts/question_bank/generate.v1.yaml + scripts/gen_bank_v3.py：每批 5 题生成（JSON 输出：question_id/category/difficulty/type/question/reference_points/followups/keywords），落 `data/question_bank/v3_{position}/{category}.jsonl`；id 按 `q_{category}_{seq:03d}` 续号；resume 跳过已写盘、norm_text 跨文件去重、非法输出记失败不重试（重跑自然补）
- 前端：interview-position 输入加 datalist 岗位建议（Java/Python/C/C++/C#/AI Agent/项目经理）
- deploy.py [5/8] 导入循环加 6 个 v3 目录

岗位/类别矩阵（每类 40 题，难度 entry 12 / medium 20 / hard 8；type 按岗位适配）：

| position | categories |
|---|---|
| python_backend | python_basics / python_concurrent / python_web / python_framework / python_db |
| c_backend | c_basics / c_memory / c_system / c_algorithm |
| cpp_backend | cpp_basics / cpp_stl / cpp_concurrent / cpp_modern |
| csharp_backend | csharp_basics / csharp_net / csharp_ef / csharp_web |
| ai_agent | agent_core / agent_llm / agent_tooling / agent_system |
| project_manager | pm_process / pm_agile / pm_risk / pm_soft |

## 验收标准（AC）

1. validator 扩展单测：新 category/position/source 通过校验；旧 ai_seed_v1 分布契约不受影响；按文件迭代不再要求全类齐全（v3 分岗位目录可通过）
2. normalize_position 单测：别名命中（大小写/空白不敏感）、未知输入原文返回、java 旧路径不变；ask() 用归一化后 position 抽题（mock_interview 测试）
3. gen_bank_v3 单测（StubLLM）：批次 JSON 解析/字段校验/续号（已有 3 题 → 新题从 004 起）/resume 跳过/norm_text 去重/非法输出失败不重试；模板渲染测试
4. 数据落地：6 岗位各 ≥160 题、dry-run 校验零错误；本地 dev DB 导入后题量 497 → ~1450+；`--force` 重跑幂等
5. 全量回归：既有 371 测试 + 17 快照全绿（新增测试并入）
6. 服务器部署 + 冒烟：deploy 重跑（v3 目录导入，MySQL 题量同 dev）；真实面试指定「Python」岗位抽到 python_backend 题（Langfuse/回复中题目来源可证）；别名「C++」「项目经理」各命中；B1 问「Python 的 GIL」检索命中 python_backend 题；既有面试/回顾/记忆不回归

## 实施计划

1. 规格 → 01-planned（本文件，开工流转 02）
2. validator/store/mock_interview/B1 工具改造 + 单测（AC1/AC2）
3. generate.v1.yaml + gen_bank_v3.py + 单测（AC3）
4. 本地生成 6 岗位数据（后台长任务）→ dry-run → dev DB 导入（AC4）
5. ruff/mypy + 全量回归（AC5）
6. deploy + 服务器冒烟（AC6）
7. 收尾：流转 03 + README + 记忆同步 + 中文报告

## Definition of Done

AC1-6 全部通过；生成数据落仓库 data/question_bank/v3_*；冒烟证据写入实现备注；README 与记忆同步；红线合规（只动 offercoach 库）。

---

## 实现备注（2026-10-25）

**代码**：validator POSITIONS 7 枚举 + v3 类别 + ai_seed_v3 source、按目录内实际文件迭代校验；store.normalize_position 别名表（口语岗位 → 库内 position）；mock_interview ask/opening 用归一化 position 抽题（展示仍用原始输入）；B1 工具 position 可省略（None=全岗位检索）；generate.v1.yaml + scripts/gen_bank_v3.py（每批 5 题 JSON、resume 跳过已写盘、norm_text 跨文件去重、非法输出记失败不重试、stall-guard）；前端岗位 datalist 建议；deploy.py [5/8] 八目录导入循环。

**AC1-3**：validator / normalize_position / gen_bank_v3 单测全绿（并入全量回归）。

**AC4 数据落地**：本地生成 6 岗位 **1000 题**（python_backend 200 + c/cpp/csharp/ai_agent/project_manager 各 160）。生成统计：写盘新增 990 条 + 跳过 19（与库内/已写盘题面重复）+ 失败 49（全部为 LLM 输出字段校验不合规，按约定不重试——六岗位均已达标，无需补跑）；dry-run 校验 **1000 条零错误**；dev DB 导入 497 → **1497**；`import_questions.py --force` 重跑幂等（新增 0 / 更新 200 / 总数不变）。生成成本：输入 54.1 万 + 输出 92.7 万字符（cheap 档）。

**AC5 全量回归**：**430 测试 + 18 快照**全绿（跑两遍：主跑 + B1 兜底修复后复跑）；ruff / mypy（69 文件）干净。

**AC6 部署 + 服务器冒烟**：deploy 重跑成功（八目录导入，MySQL 题量 1497 与 dev 一致；服务 active + healthz 200）。公网真实冒烟 **12/12**：
- 验证码注册 → 岗位别名面试：指定「Python」抽到 v3_python_backend 题（回复中题目原文精确比对命中）、「C++」→ v3_cpp_backend、「项目经理」→ v3_project_manager 各命中；
- Python 面试完整一轮（作答 → judge 反馈 → 「结束」→ 总结报告）；C++/项目经理「结束」口令收尾正常；
- B1 问「Python 的 GIL 是什么？它为什么还存在？」→ 回复与 python_backend 参考要点 6-gram 重叠 **0.46**（跨岗位检索命中可证，且回复基于题库要点作答）；
- 复盘（review）对刚结束的 Python 面试产出报告；历史回顾页含该面试且 completed=True——既有面试/回顾不回归。

**真实冒烟揪出的坑（已修）**：B1 cheap 档模型会把**整句问题当 keywords** 传给 search_question_bank（LIKE 精确子串不命中 → 返回「题库未命中」）。按「不依赖模型自觉」原则在工具层加确定性兜底：空结果时拆词、去停用词、按词条 OR 重试（放弃 category/difficulty/position 过滤换召回；重试必须显式 position=None——store.search 缺省会回退 java_backend）。新增 4 个单测锁定；修复后服务器复跑 B1 命中（重叠 0.37 → 0.46）。生产提示词不变。

**部署工具坑（已记入记忆）**：Git Bash 的 MSYS 会把独立 argv 里的 `/xx/...` 路径转成 Windows 路径（`/data/...` → `C:/Program Files/Git/data/...`），`deploy.py put --put-remote /data/...` 会静默传错路径导致服务器 ENOENT。规避：命令前加 `MSYS2_ARG_CONV_EXCL='*'`（exec 子命令的远程命令是整串单参数、内嵌路径不转换，不受影响）。

**遗留**：无。49 条生成失败题按设计不重试（六岗位均达标）；题库总量 1497（java 497 + v3 1000）。
