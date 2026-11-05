# Spec: 提示词注册表与优化闭环 (prompts/registry)

> 状态：已实现（2026-10-21，流转至 03-implemented）。流转规则见 specs/README.md。

## 目标
提示词是版本化资产：本地 YAML 管理、按"名称 + 版本"渲染、快照锁定、评估集驱动优化。首个资产即 `router/intent`（意图分类提示词）。

## 输入/输出契约
- 文件布局：`templates/<dir>/<name>.v<N>.yaml`，如 `templates/router/intent.v1.yaml` → prompt 名 `router/intent`
- 文件格式（YAML）：
  ```yaml
  name: router/intent          # 必须与文件名推导一致
  version: 1
  description: 一句话说明
  template: |                  # string.Template 语法，$var 占位
    ...正文...
  ```
- API：
  - `registry.get(name, version=None) -> PromptSpec`（version=None 取最新版本）
  - `registry.render(prompt_name, version=None, **variables) -> str`（首参名 `prompt_name` 避免与常见模板变量 `name` 冲突；变量名不得与 `prompt_name`/`version` 同名）
  - `PromptSpec { name, version, description, template }`
- 模板语法：`string.Template`（`$var`）。**设计原则：模板保持纯替换，条件/组装逻辑放在上下文装配器**，模板保持"哑"以利快照测试与版本对比。

## 行为
1. 加载目录下所有 `*.vN.yaml` 文件（`N` 为正整数），按 (name, version) 索引；不合命名规则的文件忽略
2. 提示词名由路径推导：`templates/<dir>/<name>.vN.yaml` → `<dir>/<name>`（根目录文件无前缀）；文件名推导与内容声明的 name/version 不一致 → 加载报错（ValueError）
3. `get` 未知 name → `PromptNotFoundError`；未知版本 → `PromptNotFoundError`（错误信息含可用版本）
4. `render` 变量缺失 → `PromptRenderError`（错误信息列出缺失变量名）；多余变量忽略
5. 版本选择：显式版本优先，默认最新；回退 = 显式指定旧版本号（旧版本文件保留不删除）

## 验收标准（每条 → 至少一个测试）
- AC1 加载与渲染正确（含子目录命名推导 `router/intent`）
- AC2 缺变量报错，错误信息含缺失变量名
- AC3 未知 prompt / 未知版本报错
- AC4 显式版本与默认最新版本选择正确
- AC5 模板含 JSON 大括号（少样本示例）时渲染结果原样保留
- AC6 快照测试锁定：模板变更被 golden file 测试捕获（基线首次用 `pytest --snapshot-update` 生成）
- AC7 文件名推导与内容声明不一致 → 加载报错；非模板文件被忽略

## 优化闭环（L4）
- 评估集：`data/evals/intent.jsonl`，每行 `{"id", "message", "expected_intent"}`（15 条：规则命中 5 + 需 LLM 10）
- 评估器：`app/evaluation/intent.py`，`run_eval(llm, registry, cases) -> EvalReport { total, correct, failures, accuracy }`；**接受任意 LLM（含桩），评估器本身可单测**
- 跑批入口：`scripts/eval_intent.py`（真实模型接线在 Phase 2 的模型工厂完成，当前提示并退出）
- 优化流程：改模板出新版本 → 跑评估 → 对比版本准确率 → 人工决定晋升/回退（回退即显式指定旧版本号）
- 提示词每次渲染的版本号必须可被调用方取得（供后续 Langfuse 记录 prompt_version）

## 边界与异常
- 模板中不得出现 `$` 后接非标识符的写法（string.Template 限制）；需要 `$` 字面量时用 `$$`
- 本 spec 不覆盖 Langfuse Prompt Management 同步（Phase 2，接口预留：registry 保持"本地文件为源"）
