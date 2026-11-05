"""L1: 题库校验器 — 结构 / 枚举 / 唯一性 / seed_v1 分布契约。"""

from __future__ import annotations

from app.question_bank.validator import validate_bank, validate_distribution, validate_records
from tests.support.question_bank import build_seed_bank, make_record, write_bank_category


def test_valid_record_passes():
    errors = validate_records([make_record("java_basics", 1)])
    assert errors == []


def test_missing_field_rejected():
    record = make_record("java_basics", 1)
    del record["difficulty"]
    errors = validate_records([record])
    assert any("difficulty" in e for e in errors)


def test_unknown_field_rejected():
    errors = validate_records([make_record("java_basics", 1, extra="多余")])
    assert any("未定义字段" in e for e in errors)


def test_invalid_enum_rejected():
    errors = validate_records([make_record("java_basics", 1, difficulty="简单")])
    assert any("difficulty 非法" in e for e in errors)


def test_question_id_format_rejected():
    errors = validate_records([make_record("java_basics", 1, question_id="q_java_1")])
    assert any("question_id 格式非法" in e for e in errors)


def test_question_id_category_mismatch_rejected():
    errors = validate_records([make_record("java_basics", 1, question_id="q_jvm_001")])
    assert any("不一致" in e for e in errors)


def test_duplicate_question_id_rejected():
    errors = validate_records([make_record("java_basics", 1), make_record("java_basics", 1)])
    assert any("question_id 重复" in e for e in errors)


def test_duplicate_normalized_question_rejected():
    first = make_record("java_basics", 1, question="并发 容器 有哪些")
    second = make_record("java_basics", 2, question="并发容器有哪些")
    errors = validate_records([first, second])
    assert any("重复" in e for e in errors)


def test_reference_points_bounds():
    too_few = validate_records([make_record("java_basics", 1, reference_points=["a", "b"])])
    assert any("reference_points" in e for e in too_few)
    too_many = validate_records([make_record("java_basics", 1, reference_points=["a"] * 6)])
    assert any("reference_points" in e for e in too_many)


def test_keywords_minimum():
    errors = validate_records([make_record("java_basics", 1, keywords=["a", "b"])])
    assert any("keywords" in e for e in errors)


def test_seed_bank_passes_contract(tmp_path):
    data_dir = tmp_path / "seed_v1"
    records = build_seed_bank(data_dir)
    assert len(records) == 200
    loaded, errors = validate_bank(data_dir)
    assert errors == []
    assert len(loaded) == 200


def test_type_distribution_violation_detected():
    records = [make_record("java_basics", seq) for seq in range(1, 26)]
    records[0]["type"] = "coding"  # knowledge 13→12
    errors = validate_distribution(records)
    assert any("java_basics" in e and "knowledge" in e for e in errors)


def test_difficulty_distribution_violation_detected():
    records = [make_record("java_basics", seq) for seq in range(1, 26)]
    records[0]["difficulty"] = "entry"  # medium 12→11, entry 5→6
    errors = validate_distribution(records)
    assert any("java_basics" in e and "medium" in e for e in errors)


def test_missing_category_file_reported(tmp_path):
    data_dir = tmp_path / "seed_v1"
    build_seed_bank(data_dir)
    (data_dir / "jvm.jsonl").unlink()
    _, errors = validate_bank(data_dir)
    assert any("缺少题库文件" in e for e in errors)


def test_json_parse_error_line_reported(tmp_path):
    data_dir = tmp_path / "seed_v1"
    data_dir.mkdir(parents=True)
    (data_dir / "java_basics.jsonl").write_text(
        '{"question_id": "q_java_basics_001",\n', encoding="utf-8"
    )
    _, errors = validate_bank(data_dir)
    assert any("java_basics.jsonl:1" in e and "JSON 解析失败" in e for e in errors)


# ---- 二期 java_guide 契约 ----


def test_java_guide_record_passes():
    record = make_record(
        "java_basics", 1, question_id="q_java_basics_g0123abcd", source="java_guide"
    )
    assert validate_records([record]) == []


def test_unknown_source_rejected():
    errors = validate_records([make_record("java_basics", 1, source="github")])
    assert any("source 必须为" in e for e in errors)


def test_guide_id_bad_hash_rejected():
    record = make_record("java_basics", 1, question_id="q_java_basics_gxyz", source="java_guide")
    errors = validate_records([record])
    assert any("question_id 格式非法" in e for e in errors)


def test_guide_id_category_mismatch_rejected():
    record = make_record("java_basics", 1, question_id="q_jvm_g0123abcd", source="java_guide")
    errors = validate_records([record])
    assert any("不一致" in e for e in errors)


def test_guide_batch_distribution_unconstrained():
    # 二期批次不套 seed_v1 的 25 题/type/难度契约：任意形状均通过分布校验
    records = [
        make_record(
            "java_basics",
            seq,
            question_id=f"q_java_basics_g{seq:08x}",
            source="java_guide",
            difficulty="hard",
            qtype="coding",
        )
        for seq in range(1, 41)
    ]
    assert validate_distribution(records) == []


# ---- 三期多岗位契约 ----


def test_v3_record_passes():
    record = make_record("python_basics", 1, position="python_backend", source="ai_seed_v3")
    assert validate_records([record]) == []


def test_v3_position_enum_violation_rejected():
    errors = validate_records(
        [make_record("python_basics", 1, position="golang", source="ai_seed_v3")]
    )
    assert any("position 必须为" in e for e in errors)


def test_v3_sparse_dir_passes(tmp_path):
    # 分岗位目录只含本岗位类别文件：按文件迭代，不再要求全类齐全、也不触发 seed 完整性
    data_dir = tmp_path / "v3_python"
    write_bank_category(
        data_dir,
        "python_basics",
        [make_record("python_basics", 1, position="python_backend", source="ai_seed_v3")],
    )
    write_bank_category(
        data_dir,
        "python_db",
        [make_record("python_db", 1, position="python_backend", source="ai_seed_v3")],
    )
    loaded, errors = validate_bank(data_dir)
    assert errors == []
    assert len(loaded) == 2


def test_unknown_category_file_rejected(tmp_path):
    data_dir = tmp_path / "v3_x"
    write_bank_category(
        data_dir,
        "golang_basics",
        [make_record("python_basics", 1, position="python_backend", source="ai_seed_v3")],
    )
    _, errors = validate_bank(data_dir)
    assert any("题库文件名非法" in e for e in errors)


def test_seed_completeness_not_triggered_by_v3(tmp_path):
    # v3 目录全是 ai_seed_v3 → 不要求 DISTRIBUTION 类别齐全；seed_v1 目录仍要求（既有测试）
    data_dir = tmp_path / "v3_python"
    write_bank_category(
        data_dir,
        "python_basics",
        [make_record("python_basics", 1, position="python_backend", source="ai_seed_v3")],
    )
    _, errors = validate_bank(data_dir)
    assert not any("缺少题库文件" in e for e in errors)


def test_empty_bank_dir_reported(tmp_path):
    _, errors = validate_bank(tmp_path / "empty")
    assert any("目录下没有题库文件" in e for e in errors)
