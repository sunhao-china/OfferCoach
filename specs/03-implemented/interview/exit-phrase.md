# 计划：模拟面试「结束」口令确定性退出（judge 短接）

## 目标

HTTP 全流程冒烟发现：开场白承诺「随时可以说结束」，但真实 deepseek judge 把「结束」
当成当前题作答（move_on 继续发题），用户无法按约定终止面试。judge.v2 规则 1 依赖
LLM 判别，空泛作答语境下不可靠。给「结束」短语加确定性短接：命中即 action=end，
不调 LLM、不计入作答，直接走 score→report。

## 输入输出契约

- `mock_interview.py` 新增模块级 `EXIT_PHRASES = ("结束", "结束吧", "结束面试", "不面了", "停止面试")`
  与 `_is_exit(text)`：strip 空白 + 去尾部中文/半角标点后**精确匹配**（子串不触发，
  避免「什么时候结束」类作答误杀）；
- judge 节点顶部：`if _is_exit(answer): return {"last_judge_action": "end"}`——
  不调 LLM、不写入 answers/pending 状态；
- 长文本结束语（如「我想结束本次面试，谢谢」）仍走 LLM 规则 1；judge.v2 不动；
- 其余节点与 JSON 输出形状不变。

## 验收标准（AC）

1. **AC1（短接退出）**：「结束」/「结束。」/「不面了」等 → 零 judge LLM 调用、
   action=end、不计入 answers/completed，score（无未评分题时零调用）→ report →
   phase=done。
2. **AC2（不误杀）**：含「结束」子串的正常作答（如「线程什么时候结束」）仍走 judge
   LLM 正常流转。
3. **AC3（回归）**：全量测试（254 + 17 快照）全绿 + 新增 2 测试；ruff/mypy 干净。
4. **AC4（真实冒烟）**：HTTP 面试轮中「结束」→ 回复含报告，历史含阶段评分。

## 实施计划

| 步骤 | 文件 | 测试 |
|---|---|---|
| ① 计划落盘（本规格） | specs/01-planned/interview/exit-phrase.md | — |
| ② judge 短接 | mock_interview.py | AC1/AC2 |
| ③ 回归 + HTTP 冒烟 | — | AC3/AC4 |
| ④ 收尾 | 流转 03 + README/记忆同步 + 报告 | — |

## Definition of Done

- AC1-AC2 测试通过；全量回归全绿；ruff/mypy 干净。
- HTTP 真实冒烟：面试中「结束」→ 评分 + 报告。
- 本规格流转 03-implemented，README 与记忆同步。

## 实现备注（2026-10-22）

改动一处：[mock_interview.py](../../../app/graphs/workflows/mock_interview.py) 新增
`EXIT_PHRASES`（结束/结束吧/结束面试/不面了/停止面试）与 `_is_exit`（strip 空白 + 去
尾部中英文标点后精确匹配）；judge 节点顶部短接：命中即 `{"last_judge_action": "end"}`，
不调 LLM、不写入 answers/completed，score（无未评分题时零调用）→ report → done。

测试：新增 2 个（短接退出零 judge 调用、子串不误杀）；既有两个测试的脚本序列同步
（test_ac6 的「结束吧」与 composition 的锁定全流程不再消耗 judge 响应，composition
脚本删除 judge_json("end") 条目）。全量 **256 测试 + 17 快照**，ruff/mypy 干净。

HTTP 全流程冒烟（真实 deepseek，23/23 项）验证：面试轮发「结束」→ 回复直接是总结报告、
历史含阶段评分；其余 chat（路由+B1+SSE 156 帧）/ interview / resume（docx 上传+JD）/
review / 会话管理（含被引用 409）全部通过。
