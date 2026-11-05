"""题库数据校验器：结构 / 枚举 / 唯一性 / seed_v1 分布契约。

对应 specs/question_bank/schema.md §6 验收标准 AC1~AC6。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CATEGORIES: tuple[str, ...] = (
    # java_backend（seed_v1 + java_guide）
    "java_basics",
    "jvm",
    "concurrent",
    "mysql",
    "redis",
    "spring",
    "network_os",
    "distributed",
    # 三期多岗位（v3 各岗位目录，类别按文件名约束）
    "python_basics",
    "python_concurrent",
    "python_web",
    "python_framework",
    "python_db",
    "c_basics",
    "c_memory",
    "c_system",
    "c_algorithm",
    "cpp_basics",
    "cpp_stl",
    "cpp_concurrent",
    "cpp_modern",
    "csharp_basics",
    "csharp_net",
    "csharp_ef",
    "csharp_web",
    "agent_core",
    "agent_llm",
    "agent_tooling",
    "agent_system",
    "pm_process",
    "pm_agile",
    "pm_risk",
    "pm_soft",
)
DIFFICULTIES: tuple[str, ...] = ("entry", "medium", "hard")
TYPES: tuple[str, ...] = ("knowledge", "scenario", "coding", "system_design")
POSITIONS: tuple[str, ...] = (
    "java_backend",
    "python_backend",
    "c_backend",
    "cpp_backend",
    "csharp_backend",
    "ai_agent",
    "project_manager",
)
# 来源枚举（二期/三期扩充）：seed_v1 为 AI 种子，java_guide 为开源题库规范化导入，
# ai_seed_v3 为三期多岗位 AI 生成
SOURCES: tuple[str, ...] = ("ai_seed_v1", "java_guide", "ai_seed_v3")

# seed_v1 分布契约：category -> {type: 题数}，每类合计 25；难度分布每类 5/12/8
DISTRIBUTION: dict[str, dict[str, int]] = {
    "java_basics": {"knowledge": 13, "scenario": 8, "coding": 4, "system_design": 0},
    "jvm": {"knowledge": 13, "scenario": 8, "coding": 4, "system_design": 0},
    "concurrent": {"knowledge": 10, "scenario": 8, "coding": 7, "system_design": 0},
    "mysql": {"knowledge": 10, "scenario": 8, "coding": 0, "system_design": 7},
    "redis": {"knowledge": 10, "scenario": 7, "coding": 0, "system_design": 8},
    "spring": {"knowledge": 12, "scenario": 13, "coding": 0, "system_design": 0},
    "network_os": {"knowledge": 12, "scenario": 8, "coding": 5, "system_design": 0},
    "distributed": {"knowledge": 0, "scenario": 0, "coding": 0, "system_design": 25},
}
DIFFICULTY_COUNTS: dict[str, int] = {"entry": 5, "medium": 12, "hard": 8}

REQUIRED_STR_FIELDS = (
    "question_id",
    "position",
    "category",
    "difficulty",
    "type",
    "question",
    "source",
)
REQUIRED_LIST_FIELDS = ("reference_points", "followups", "keywords")
FIELDS = set(REQUIRED_STR_FIELDS + REQUIRED_LIST_FIELDS)

_SEED_ID_PATTERN = re.compile(rf"^q_({'|'.join(CATEGORIES)})_(\d{{3}})$")
_GUIDE_ID_PATTERN = re.compile(rf"^q_({'|'.join(CATEGORIES)})_g[0-9a-f]{{8}}$")


def norm_text(text: str) -> str:
    """归一化：去掉全部空白字符，用于题目文本去重（normalizer 跨源去重共用）。"""
    return "".join(text.split())


def load_file(path: Path) -> tuple[list[dict], list[str]]:
    """读取一个 JSONL 文件，返回 (记录, 行级解析错误)。"""
    records: list[dict] = []
    errors: list[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_no} JSON 解析失败: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path.name}:{line_no} 不是 JSON 对象")
            continue
        records.append(data)
    return records, errors


def validate_records(records: list[dict]) -> list[str]:
    """AC1~AC5：结构完整、ID 契约、枚举、题目唯一、列表约束。"""
    errors: list[str] = []

    def err(record: dict, msg: str) -> None:
        errors.append(f"{record.get('question_id', '(无 id)')}: {msg}")

    seen_ids: set[str] = set()
    seen_questions: dict[str, str] = {}
    for record in records:
        qid = record.get("question_id")
        # AC1 结构：必填字段齐全且类型正确，且无未定义字段
        missing = [
            f
            for f in REQUIRED_STR_FIELDS
            if not isinstance(record.get(f), str) or not record[f].strip()
        ]
        missing += [f for f in REQUIRED_LIST_FIELDS if not isinstance(record.get(f), list)]
        if missing:
            err(record, f"字段缺失或类型错误: {', '.join(missing)}")
        unknown = sorted(set(record) - FIELDS)
        if unknown:
            err(record, f"未定义字段: {', '.join(unknown)}")
        if missing:
            continue  # 后续校验依赖这些字段
        qid = record["question_id"]
        assert isinstance(qid, str)  # 已通过 AC1 校验，此处仅为类型收窄
        # AC2 ID 契约：格式（seed 序号 / java_guide 哈希）+ category 段一致 + 全库唯一
        match = _SEED_ID_PATTERN.fullmatch(qid) or _GUIDE_ID_PATTERN.fullmatch(qid)
        if not match:
            err(
                record,
                f"question_id 格式非法: {qid}"
                f"（应为 q_{{category}}_{{seq:03d}} 或 q_{{category}}_g{{hash8}}）",
            )
        elif match.group(1) != record["category"]:
            err(record, f"question_id 与 category 不一致: {qid} vs {record['category']}")
        if qid in seen_ids:
            err(record, f"question_id 重复: {qid}")
        seen_ids.add(qid)
        # AC3 枚举
        if record["position"] not in POSITIONS:
            err(record, f"position 必须为 {'/'.join(POSITIONS)}，实际 {record['position']}")
        if record["category"] not in CATEGORIES:
            err(record, f"category 非法: {record['category']}")
        if record["difficulty"] not in DIFFICULTIES:
            err(record, f"difficulty 非法: {record['difficulty']}")
        if record["type"] not in TYPES:
            err(record, f"type 非法: {record['type']}")
        if record["source"] not in SOURCES:
            err(record, f"source 必须为 {'/'.join(SOURCES)}，实际 {record['source']}")
        # AC4 题目归一化唯一
        norm_q = norm_text(record["question"])
        if norm_q in seen_questions:
            err(record, f"题目文本与 {seen_questions[norm_q]} 重复（归一化后）")
        seen_questions[norm_q] = qid
        # AC5 列表约束
        for field, lo, hi in (("reference_points", 3, 5), ("followups", 2, 3)):
            count = len(record[field])
            if not lo <= count <= hi:
                err(record, f"{field} 条数 {count} 不在 [{lo}, {hi}]")
        if len(record["keywords"]) < 3:
            err(record, f"keywords 至少 3 个，实际 {len(record['keywords'])}")
        for field in REQUIRED_LIST_FIELDS:
            for index, item in enumerate(record[field]):
                if not isinstance(item, str) or not item.strip():
                    err(record, f"{field}[{index}] 为空或非字符串")
    return errors


def validate_distribution(records: list[dict]) -> list[str]:
    """AC6：seed_v1 分布契约——每个 category 25 题、type 矩阵逐格一致、难度 5/12/8。

    二期（java_guide）批次只约束结构，不约束分布（题量上限由 normalizer cap 保证）；
    无 seed 记录的 category 不参与校验（guide 批次单独成目录导入）。
    """
    errors: list[str] = []
    records = [r for r in records if r.get("source") == "ai_seed_v1"]
    per_category: dict[str, Counter] = {cat: Counter() for cat in CATEGORIES}
    for record in records:
        cat = record.get("category")
        if cat in per_category:
            per_category[cat][f"type:{record.get('type')}"] += 1
            per_category[cat][f"difficulty:{record.get('difficulty')}"] += 1
    for cat, expected_types in DISTRIBUTION.items():
        counter = per_category[cat]
        if not counter:
            continue  # 该 category 无 seed 记录（如 java_guide 批次目录），跳过
        total = sum(counter[f"type:{t}"] for t in TYPES)
        if total != 25:
            errors.append(f"{cat}: 题量 {total} 不等于 25")
        for t, expected in expected_types.items():
            actual = counter[f"type:{t}"]
            if actual != expected:
                errors.append(f"{cat}: type={t} 实际 {actual} 不等于期望 {expected}")
        for d, expected in DIFFICULTY_COUNTS.items():
            actual = counter[f"difficulty:{d}"]
            if actual != expected:
                errors.append(f"{cat}: difficulty={d} 实际 {actual} 不等于期望 {expected}")
    return errors


def validate_bank(data_dir: Path) -> tuple[list[dict], list[str]]:
    """加载并校验整个题库目录，返回 (全部记录, 全部错误)。

    按目录内实际 *.jsonl 文件迭代（三期 v3 各岗位目录只含本岗位类别，不再要求全类齐全）；
    文件名的 stem 必须在 CATEGORIES 枚举内。
    seed_v1 完整性例外：目录内出现 ai_seed_v1 记录时，要求全部 DISTRIBUTION 类别文件齐全。
    """
    records: list[dict] = []
    errors: list[str] = []
    files = sorted(data_dir.glob("*.jsonl"))
    if not files:
        errors.append(f"目录下没有题库文件: {data_dir}")
    for path in files:
        if path.stem not in CATEGORIES:
            errors.append(f"题库文件名非法（category 不在枚举内）: {path.name}")
        loaded, load_errors = load_file(path)
        records.extend(loaded)
        errors.extend(load_errors)
    if any(r.get("source") == "ai_seed_v1" for r in records):
        for cat in DISTRIBUTION:
            path = data_dir / f"{cat}.jsonl"
            if not path.exists():
                errors.append(f"缺少题库文件: {path}")
    errors.extend(validate_records(records))
    errors.extend(validate_distribution(records))
    return records, errors
