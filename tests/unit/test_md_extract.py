"""L1: JavaGuide md 提取 — 题面过滤 / 层级边界 / 代码剔除 / 分类规则 / 确定性 / 去重。

依据 specs/02-in-progress/question_bank/java-guide-import.md 的 AC2。
"""

from __future__ import annotations

from app.question_bank.md_extract import extract_candidates

PROCESS_BODY = (
    "进程是操作系统进行资源分配和调度的基本单位，一个进程可以包含多个线程，"
    "多个线程共享进程的堆和方法区等资源。进程拥有独立的内存空间，进程间通信"
    "需要借助管道、消息队列、共享内存等机制，通信成本相对较高，进程的创建和"
    "销毁开销也明显大于线程。"
)
THREAD_BODY = (
    "线程是程序执行的最小单位，一个进程可以包含多个线程，线程之间共享进程的堆"
    "和方法区等资源，但每个线程拥有独立的程序计数器、虚拟机栈和本地方法栈。"
    "线程的创建和切换开销远小于进程，因此常把线程称为轻量级进程，多线程编程"
    "是提升并发性能的常用手段。"
)
LIFECYCLE_BODY = (
    "线程的生命周期包含新建、就绪、运行、阻塞和终止五个状态。新建状态的线程"
    "调用 start 方法后进入就绪状态，获得 CPU 时间片后进入运行状态；运行中的线程"
    "遇到锁竞争、等待 IO 等情况会进入阻塞状态，条件满足后回到就绪状态，"
    "run 方法执行完毕进入终止状态。"
)


def _write(tmp_path, rel_path: str, content: str):
    path = tmp_path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_extract_questions_with_level_boundaries_and_fences(tmp_path):
    md = f"""\
# 章节标题（一级不进候选）

## 第一节 线程基础

### ⭐️ 什么是线程和进程？
{THREAD_BODY}

#### 什么是进程？
{PROCESS_BODY}

### 线程的生命周期是什么？
{LIFECYCLE_BODY}
```java
Thread.State state = t.getState();
```

## 第二节 集合
"""
    _write(tmp_path, "docs/java/concurrent/c01.md", md)
    candidates = extract_candidates(tmp_path)
    assert [c.question for c in candidates] == [
        "什么是线程和进程？",
        "什么是进程？",
        "线程的生命周期是什么？",
    ]
    parent, child, lifecycle = candidates
    # 更深的子问题先收口父题：父题正文只含自己的段落，不吞子题段落
    assert "轻量级进程" in parent.answer
    assert "进程是操作系统进行资源分配和调度的基本单位" not in parent.answer
    assert "进程是操作系统进行资源分配和调度的基本单位" in child.answer
    # fenced code block 整体剔除
    assert "```java" not in lifecycle.answer
    assert "Thread.State" not in lifecycle.answer
    assert lifecycle.answer.count("新建") >= 1


def test_deeper_non_question_heading_merges_into_body(tmp_path):
    md = f"""\
### 什么是线程和进程？
{THREAD_BODY}

#### 进程与线程的关系补充
进程与线程的关系常被类比为工厂与流水线工人，一个工厂可以有多条流水线。

### 什么是进程？
{PROCESS_BODY}
"""
    _write(tmp_path, "docs/java/concurrent/c04.md", md)
    candidates = extract_candidates(tmp_path)
    assert [c.question for c in candidates] == ["什么是线程和进程？", "什么是进程？"]
    parent, child = candidates
    # 更深的非问题小标题及其段落并入父题正文
    assert "进程与线程的关系补充" in parent.answer
    assert "工厂与流水线" in parent.answer
    # 同级问题标题收口父题，互不吞并
    assert "进程是操作系统进行资源分配和调度的基本单位" not in parent.answer
    assert "进程是操作系统进行资源分配和调度的基本单位" in child.answer


def test_question_like_heading_filters(tmp_path):
    md = f"""\
### 线程基础知识（章节标题，不以问号结尾）
{PROCESS_BODY}
### 线程池的核心参数有哪些？
{THREAD_BODY}
### 这个问题太长了{"？" * 1}这里凑足长度超过一百字{"。" * 60}
{PROCESS_BODY}
"""
    _write(tmp_path, "docs/java/concurrent/c02.md", md)
    candidates = extract_candidates(tmp_path)
    assert [c.question for c in candidates] == ["线程池的核心参数有哪些？"]


def test_heading_cleanup_strips_emoji_and_links(tmp_path):
    md = f"""\
### ⭐️ [什么是线程池？](https://javaguide.cn/thread-pool)
{THREAD_BODY}
"""
    _write(tmp_path, "docs/java/concurrent/c03.md", md)
    candidates = extract_candidates(tmp_path)
    assert candidates[0].question == "什么是线程池？"


def test_category_rules_and_readme_skip(tmp_path):
    _write(tmp_path, "docs/java/basis/b.md", f"### 什么是面向对象？\n{PROCESS_BODY}")
    _write(tmp_path, "docs/database/redis/r.md", f"### 什么是缓存穿透？\n{THREAD_BODY}")
    _write(tmp_path, "docs/ai/interview-questions/llm.md", f"### 什么是 RAG？\n{PROCESS_BODY}")
    _write(tmp_path, "docs/java/concurrent/README.md", f"### 什么是线程？\n{THREAD_BODY}")
    candidates = extract_candidates(tmp_path)
    by_category = {c.question: c.category for c in candidates}
    assert by_category == {
        "什么是面向对象？": "java_basics",
        "什么是缓存穿透？": "redis",
        # 无规则命中的 ai 路径与 README 均跳过
    }


def test_deterministic_order_and_dedup(tmp_path):
    # 两个文件、题内重复：路径排序 + 文档序，题面归一化去重只留第一个
    body = (
        f"### 线程池的核心参数有哪些？\n{PROCESS_BODY}\n### 线程池的核心参数有哪些？\n{THREAD_BODY}"
    )
    _write(tmp_path, "docs/java/concurrent/b.md", body)
    _write(tmp_path, "docs/java/concurrent/a.md", f"### 什么是线程安全？\n{THREAD_BODY}")
    candidates = extract_candidates(tmp_path)
    assert [c.question for c in candidates] == ["什么是线程安全？", "线程池的核心参数有哪些？"]
    # 只保留首个重复题：正文为第一次出现时的段落，第二次的正文不入
    assert candidates[1].answer.count("进程是操作系统") == 1
    assert "线程是程序执行的最小单位" not in candidates[1].answer


def test_answer_length_bounds(tmp_path):
    short = "### 什么是进程？\n进程是程序的运行实例。"  # 正文 < 100 字
    _write(tmp_path, "docs/java/concurrent/short.md", short)
    long_heading = f"### 什么是线程？\n{'内容' * 1300}"  # 正文 > 2500 字
    _write(tmp_path, "docs/java/concurrent/long.md", long_heading)
    candidates = extract_candidates(tmp_path)
    assert candidates == []
