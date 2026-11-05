# 计划：模型工厂（deepseek 分档）

> 状态：已实现（2026-10-21，流转至 03-implemented）。依据：docs/system-design.md §3.2 模型路由。

## 目标

构建模型分档工厂：**strong**（提问/追问/评分/报告）与 **cheap**（分类/总结/澄清），同供应商 deepseek，型号经环境变量覆盖，为后续 Langfuse 按档位统计 token 打基础。

## 输入/输出契约

- 模块：`app/core/llm_factory.py`
- 常量：`TIER_STRONG = "strong"`、`TIER_CHEAP = "cheap"`、`TIERS = (strong, cheap)`
- `build_chat_model(tier: str = TIER_STRONG, *, temperature: float | None = None) -> BaseChatModel`
  - 未设置 `DEEPSEEK_API_KEY` → `RuntimeError`，报错信息含环境变量名
  - tier 非法 → `ValueError`，报错列出合法档位
  - 型号：`DEEPSEEK_STRONG_MODEL` / `DEEPSEEK_CHEAP_MODEL`，默认均为 `deepseek-chat`（deepseek-reasoner 为强档候选，待定）
  - 返回 LangChain `ChatDeepSeek`（同时满足最小 LLM 协议：`invoke(list[str])` → 带 `.content` 的对象，与 StubLLM 鸭子兼容）
- 按 `(tier, temperature)` 缓存实例（functools.cache）；测试需 `cache_clear`

## 验收标准

- AC1 合法 tier 返回 ChatDeepSeek 实例，模型名与档位对应（默认 deepseek-chat）
- AC2 非法 tier 抛 ValueError，信息含合法档位
- AC3 无 DEEPSEEK_API_KEY 抛 RuntimeError，信息含变量名
- AC4 环境变量 DEEPSEEK_STRONG_MODEL / DEEPSEEK_CHEAP_MODEL 覆盖型号生效
- AC5 同档位同温度两次调用返回同一实例（缓存）；不同温度不同实例

## 实施计划

| 文件 | 动作 |
|---|---|
| `app/core/llm_factory.py` | 新建 |
| `tests/unit/test_llm_factory.py` | 新建：AC1~AC5（monkeypatch 环境变量 + cache_clear） |

## Definition of Done

- [ ] AC1~AC5 全绿（构造不发网络请求，测试无网络依赖）
- [ ] ruff + mypy 干净
